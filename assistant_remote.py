"""Tenant-local, explicitly permitted phone-to-PC business tool transport."""
import hashlib,json,re,time
from server import Problem

def init(con):
    con.executescript('''CREATE TABLE IF NOT EXISTS assistant_access(user_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS assistant_jobs(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL,lease REAL DEFAULT 0);''')
    now=time.time()
    con.execute("UPDATE assistant_jobs SET status='expired',payload='{}',result='{}' WHERE status IN ('pending','working') AND created<?",(now-120,))
    con.execute("UPDATE assistant_jobs SET payload='{}',result='{}',status='expired' WHERE status='complete' AND created<?",(now-3600,))
    con.execute('DELETE FROM assistant_jobs WHERE created<?',(now-90*86400,))

def allowed(con,uid):
    return bool(con.execute('SELECT 1 FROM assistant_access a JOIN users u ON u.id=a.user_id WHERE a.user_id=? AND a.enabled=1 AND u.active=1',(uid,)).fetchone())

def status(bridge,con,user):
    init(con)
    access=allowed(con,user['id'])
    online=time.time()-float(bridge.get(con,'assistant_seen','0'))<30
    return dict(allowed=access,online=online,context=json.loads(bridge.get(con,'assistant_context','{}')) if access and online else {})

def handle(bridge,con,user,desktop,method,action,b,q):
    init(con);now=time.time()
    if desktop:
        if action=='access' and method=='GET':
            return dict(users=[dict(id=r['id'],enabled=allowed(con,r['id'])) for r in con.execute('SELECT id FROM users')])
        if action=='access' and method=='POST':
            if not con.execute('SELECT 1 FROM users WHERE id=?',(b.get('id'),)).fetchone():raise ValueError('Phone user not found.')
            if not isinstance(b.get('enabled'),bool):raise ValueError('Specify enabled true or false.')
            con.execute('INSERT OR REPLACE INTO assistant_access VALUES(?,?)',(b['id'],int(b['enabled'])))
            if not b['enabled']:con.execute("UPDATE assistant_jobs SET status='revoked',payload='{}',result='{}' WHERE user_id=? AND status IN ('pending','working')",(b['id'],))
            bridge.audit(con,'phone-ai-access','desktop',b['id']+':'+str(b['enabled']));return dict(ok=True)
        if b.get('book_id')!=bridge.get(con,'book_id'):raise Problem('These are different books.',409)
        if action=='poll' and method=='POST':
            context=b.get('context',{})
            if not isinstance(context,dict) or len(json.dumps(context).encode())>40000:raise ValueError('Invalid assistant context.')
            bridge.set(con,'assistant_seen',now);bridge.set(con,'assistant_context',json.dumps(context))
            rows=[]
            for r in con.execute("SELECT * FROM assistant_jobs WHERE status='pending' OR (status='working' AND lease<?) ORDER BY created LIMIT 5",(now-20,)).fetchall():
                if not allowed(con,r['user_id']):continue
                con.execute("UPDATE assistant_jobs SET status='working',lease=? WHERE id=?",(now,r['id']))
                rows.append(dict(id=r['id'],user_id=r['user_id'],payload=json.loads(r['payload']),created=r['created']))
            return dict(jobs=rows)
        if action=='result' and method=='POST':
            row=con.execute('SELECT * FROM assistant_jobs WHERE id=?',(b.get('id'),)).fetchone()
            if not row:raise ValueError('Request not found.')
            if row['status'] in ('revoked','expired','received'):return dict(ok=True)
            result=b.get('result',{})
            raw=json.dumps(result,allow_nan=False)
            if len(raw.encode())>6*1024*1024:raw=json.dumps(dict(error='This report is too large for the phone. Export it on the PC.'))
            con.execute("UPDATE assistant_jobs SET status='complete',result=?,payload='{}' WHERE id=?",(raw,row['id']))
            return dict(ok=True)
        raise ValueError('Unknown desktop assistant operation.')
    if action=='status' and method=='GET':return status(bridge,con,user)
    if not allowed(con,user['id']):raise Problem('Ask the owner to enable Full accounting AI for this phone in PC Cloud sync.',403)
    if action=='submit' and method=='POST':
        ident=b.get('id','');payload=b.get('payload')
        if not isinstance(ident,str) or not re.fullmatch(r'[a-zA-Z0-9-]{20,64}',ident):raise ValueError('Invalid request identifier.')
        if not isinstance(payload,dict) or payload.get('operation') not in ('action','report','inspect'):raise ValueError('Unsupported request.')
        raw=json.dumps(payload,sort_keys=True,allow_nan=False)
        if len(raw.encode())>2*1024*1024:raise ValueError('Split this request into a smaller batch.')
        checksum=hashlib.sha256(raw.encode()).hexdigest()
        old=con.execute('SELECT * FROM assistant_jobs WHERE id=?',(ident,)).fetchone()
        if old:
            if old['user_id']!=user['id'] or old['digest']!=checksum:raise Problem('Request changed or belongs to another user.',409)
            return dict(id=ident,status=old['status'])
        if time.time()-float(bridge.get(con,'assistant_seen','0'))>=30:raise Problem('Open updated Folio on your PC and keep cloud sync on.',409)
        if con.execute("SELECT COUNT(*) FROM assistant_jobs WHERE status IN ('pending','working','complete')").fetchone()[0]>=100:raise Problem('Too many pending requests. Collect earlier results first.',429)
        con.execute('INSERT INTO assistant_jobs VALUES(?,?,?,?,?,?,?,?)',(ident,user['id'],checksum,raw,'pending','{}',now,0))
        return dict(id=ident,status='pending')
    ident=q.get('id') or b.get('id')
    row=con.execute('SELECT * FROM assistant_jobs WHERE id=? AND user_id=?',(ident,user['id'])).fetchone()
    if not row:raise Problem('Request not found.',404)
    if action=='result' and method=='GET':return dict(id=row['id'],status=row['status'],result=json.loads(row['result']))
    if action=='ack' and method=='POST':
        if row['status']=='complete':con.execute("UPDATE assistant_jobs SET status='received',result='{}',payload='{}' WHERE id=?",(ident,))
        return dict(ok=True)
    raise ValueError('Unknown phone assistant operation.')
