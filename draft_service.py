"""Tenant-local queue of unposted phone drafts, removed after PC acknowledgement."""
import hashlib, json, re, time
from draft_payload import clean_payload

def handle(con, desktop, user, method, action, b):
    con.execute('''CREATE TABLE IF NOT EXISTS drafts_queue(id TEXT PRIMARY KEY,user_id INTEGER,kind TEXT,payload TEXT,digest TEXT,status TEXT,created REAL)''')
    con.execute("DELETE FROM drafts_queue WHERE status='delivered' AND created<?",(time.time()-90*86400,))
    if desktop and method=='GET':
        return dict(rows=[dict(r,payload=json.loads(r['payload'])) for r in con.execute("SELECT * FROM drafts_queue WHERE status='pending' ORDER BY created LIMIT 50")])
    if desktop and method=='POST' and action=='ack':
        con.execute("UPDATE drafts_queue SET payload='{}',status='delivered' WHERE id=?",(b.get('id'),))
        return dict(ok=True)
    if not desktop and method=='POST' and action=='drafts':
        ident=b.get('id','');kind=b.get('kind');payload=b.get('payload')
        if not isinstance(ident,str) or not re.fullmatch(r'[a-zA-Z0-9-]{20,64}',ident):raise ValueError('Invalid draft identifier.')
        if kind not in ('invoice','purchase','journal','money') or not isinstance(payload,dict):raise ValueError('Unsupported draft.')
        payload=clean_payload(kind,payload)
        raw=json.dumps(payload,sort_keys=True,allow_nan=False)
        if len(raw.encode())>100000:raise ValueError('Split this draft into smaller entries.')
        digest=hashlib.sha256((kind+raw).encode()).hexdigest()
        old=con.execute('SELECT * FROM drafts_queue WHERE id=?',(ident,)).fetchone()
        if old:
            if old['user_id']!=user['id'] or old['digest']!=digest:raise ValueError('Draft changed after sending. Create a new draft instead.')
            return dict(ok=True,status=old['status'])
        if con.execute("SELECT COUNT(*) FROM drafts_queue WHERE status='pending'").fetchone()[0]>=1000:raise ValueError('Open the PC to collect pending drafts first.')
        con.execute('INSERT INTO drafts_queue VALUES(?,?,?,?,?,?,?)',(ident,user['id'],kind,raw,digest,'pending',time.time()))
        return dict(ok=True,status='pending')
    raise ValueError('Unknown draft request.')
