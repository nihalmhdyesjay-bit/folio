"""Tenant-isolated, acknowledgement-driven Folio relay. No transaction archive."""
import json,os,re,time,secrets,sqlite3,threading,hmac,shutil
from pathlib import Path
from contextlib import contextmanager
from server import Bridge,Problem,LOCK,hash_token,stamp

RETENTION=90*86400
CATALOG_TTL=86400

class RelayBridge(Bridge):
    def catalog(self,con):
        if time.time()-float(self.get(con,'catalog_time','0'))>CATALOG_TTL:
            raise Problem('Catalog expired. Open desktop Folio and sync before creating new entries.',409)
        return super().catalog(con)

    def canonical(self,con,b,user,seq):
        cursor=self.catalog(con).get('cursor',0)
        if con.execute("SELECT 1 FROM events WHERE status='applied' AND seq>? LIMIT 1",(cursor,)).fetchone():
            raise Problem('The desktop is updating balances. Please sync again shortly.',409)
        return super().canonical(con,b,user,seq)

    def event(self,row):
        event=super().event(row)
        event['delivered']=row['status']=='applied' and row['payload']=='{}'
        return event

    def cleanup(self):
        with LOCK,self.connect() as con:
            cutoff=time.strftime('%Y-%m-%dT%H:%M:%S',time.gmtime(time.time()-RETENTION))
            con.execute("DELETE FROM events WHERE status='applied' AND created<?",(cutoff,))
            con.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            con.execute('DELETE FROM attempts WHERE until<?',(time.time()-86400,))
            con.execute('DELETE FROM audit WHERE ts<?',(cutoff,))
            if time.time()-float(self.get(con,'catalog_time','0'))>CATALOG_TTL:
                con.execute("DELETE FROM config WHERE key='catalog'")

    def api(self,method,path,b,token,query,address):
        if path in ('/v1/drafts','/v1/desktop/drafts','/v1/desktop/drafts/ack'):
            import draft_service
            with LOCK,self.connect() as con:
                desktop=path.startswith('/v1/desktop/')
                user=self.authenticate(con,token,desktop)
                return draft_service.handle(con,desktop,user,method,path.rsplit('/',1)[-1],b)
        if path.startswith(('/v1/ai/','/v1/desktop/ai/')):
            import ai_service
            with LOCK,self.connect() as con:
                user=self.authenticate(con,token,path.startswith('/v1/desktop/'))
                scope=str(self.folder)+':'+('desktop' if user=='desktop' else str(user['id']))
            return ai_service.handle(scope,method,path.rsplit('/',1)[-1],b,query)
        with LOCK:
            # Authenticate and bound-check before any read or mutation.
            with self.connect() as con:
                user=self.authenticate(con,token,path.startswith('/v1/desktop/')) if path!='/v1/login' else None
                if path=='/v1/desktop/catalog' and method=='POST':
                    c=b.get('catalog',{})
                    if len(json.dumps(c,allow_nan=False).encode())>2*1024*1024:raise Problem('Catalog exceeds the 2 MB business limit. Contact your supplier.',413)
                    def check(value,depth=0):
                        if depth>12:raise ValueError('Catalog is too deeply nested.')
                        if isinstance(value,str) and len(value)>2000:raise ValueError('Catalog text is too long.')
                        if isinstance(value,list):
                            if len(value)>5000:raise ValueError('Catalog list exceeds 5,000 rows.')
                            for v in value:check(v,depth+1)
                        if isinstance(value,dict):
                            if len(value)>100:raise ValueError('Catalog contains too many fields.')
                            for v in value.values():check(v,depth+1)
                    check(c)
                if path=='/v1/events' and method=='POST':
                    created=b.get('created_at')
                    if not isinstance(created,(float,int)) or isinstance(created,bool) or created>time.time()+300 or created<time.time()-RETENTION:
                        raise Problem('This phone entry is older than 90 days or has an invalid creation time. Review it manually on the PC; do not recreate it without checking the books.',409)
                    existing=con.execute('SELECT 1 FROM events WHERE id=?',(str(b.get('id','')),)).fetchone()
                    if not existing and con.execute("SELECT COUNT(*) FROM events WHERE status!='applied'").fetchone()[0]>=1000:
                        raise Problem('Pending storage is full. Sync the desktop before uploading more entries.',429)
                    if not existing and shutil.disk_usage(self.folder).free<200*1024*1024:raise Problem('Server storage is low. Your phone will retain this entry; contact your supplier.',503)
                if path=='/v1/desktop/documents':
                    raise Problem('This relay does not retain invoice documents. Use the PC for final documents.',410)
                if path=='/v1/desktop/ack' and method=='POST':
                    raw=b.get('result',{})
                    if not isinstance(raw,dict):raise ValueError('Invalid acknowledgement.')
                    result={k:raw[k] for k in ('id','invoice_id') if isinstance(raw.get(k),int)}
                    if b.get('status')=='rejected':result['error']=str(raw.get('error','Review this entry on the PC.'))[:500]
                    b={**b,'result':result}
                if path.startswith('/v1/document/'):
                    row=con.execute('SELECT status FROM events WHERE id=? AND user_id=?',(path.rsplit('/',1)[-1],user['id'])).fetchone()
                    if row and row[0]=='applied':raise Problem('Details were removed after PC delivery. Use the phone copy or the PC document.',410)
            result=super().api(method,path,b,token,query,address)
            with self.connect() as con:
                if path=='/v1/desktop/catalog' and method=='POST':self.set(con,'catalog_time',time.time())
                if path=='/v1/desktop/ack' and method=='POST' and b.get('status')=='applied':
                    # Keep identifiers only. No invoice numbers, parties, amounts, lines or PDF.
                    receipt={k:b.get('result',{}).get(k) for k in ('id','invoice_id') if isinstance(b.get('result',{}).get(k),int)}
                    con.execute("UPDATE events SET payload='{}',result=? WHERE id=?",(json.dumps(receipt),b['id']))
                if path=='/v1/desktop/status':result.update(relay=True,retention_days=90,catalog_ttl_hours=24)
            return result

class Relay:
    def __init__(self,folder=None):
        self.folder=Path(folder or os.environ['FOLIO_DATA']);self.folder.mkdir(parents=True,exist_ok=True)
        self.registry=self.folder/'businesses.db'
        self.admin_key=os.environ.get('FOLIO_DESKTOP_KEY','')
        if len(self.admin_key)<30:raise RuntimeError('A private operator key is required.')
        with self.connect() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS licenses(hash TEXT PRIMARY KEY,label TEXT,created REAL,tenant TEXT,fingerprint TEXT);
CREATE TABLE IF NOT EXISTS businesses(id TEXT PRIMARY KEY,name TEXT,created REAL,active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS limits(id TEXT PRIMARY KEY,count INTEGER,reset REAL);''')

    @contextmanager
    def connect(self):
        con=sqlite3.connect(self.registry,timeout=30);con.row_factory=sqlite3.Row
        con.execute('PRAGMA journal_mode=WAL');con.execute('PRAGMA secure_delete=ON')
        try:
            with con:yield con
        finally:con.close()

    def bridge(self,tenant):
        if not re.fullmatch('[a-f0-9]{12}',tenant):raise Problem('Business connection not found.',404)
        with self.connect() as con:
            row=con.execute('SELECT active FROM businesses WHERE id=?',(tenant,)).fetchone()
        if not row or not row['active']:raise Problem('Business connection unavailable.',404)
        return RelayBridge(self.folder/'businesses'/tenant)

    def limit(self,address):
        with self.connect() as con:
            key=hash_token(address);r=con.execute('SELECT * FROM limits WHERE id=?',(key,)).fetchone();now=time.time()
            count=r['count']+1 if r and r['reset']>now else 1;reset=r['reset'] if r and r['reset']>now else now+60
            if count>30:raise Problem('Please wait a minute before trying again.',429)
            con.execute('INSERT OR REPLACE INTO limits VALUES(?,?,?)',(key,count,reset))
            con.execute('DELETE FROM limits WHERE reset<?',(now-60,))

    def register(self,b):
        code=str(b.get('activation',''));key=str(b.get('desktop_key',''));name=str(b.get('company','')).strip()
        if not 1<=len(name)<=120 or not 30<=len(key)<=200:raise ValueError('Enter your business name and a valid setup key.')
        from protocol import identifier
        book=identifier(b.get('book_id'))
        fingerprint=hash_token(json.dumps(b,sort_keys=True,separators=(',',':')))
        with LOCK,self.connect() as con:
            license=con.execute('SELECT * FROM licenses WHERE hash=?',(hash_token(code),)).fetchone()
            if not license:raise Problem('Invalid activation code. Ask your Folio supplier.',403)
            if license['tenant']:
                if not hmac.compare_digest(license['fingerprint'],fingerprint):raise Problem('This activation code is already used. Restore the original desktop connection backup.',409)
                tenant=license['tenant']
            else:
                if con.execute('SELECT COUNT(*) FROM businesses').fetchone()[0]>=int(os.environ.get('FOLIO_MAX_BUSINESSES','100')):raise Problem('Business capacity reached. Contact your supplier.',503)
                tenant=secrets.token_hex(6);bridge=RelayBridge(self.folder/'businesses'/tenant)
                with bridge.connect() as db:
                    bridge.add_user(db,dict(username=b.get('username'),password=b.get('password'),name=b.get('name')))
                    bridge.set(db,'desktop_hash',hash_token(key));bridge.set(db,'service_id',secrets.token_hex(16));bridge.set(db,'book_id',book)
                con.execute('INSERT INTO businesses(id,name,created) VALUES(?,?,?)',(tenant,name,time.time()))
                con.execute('UPDATE licenses SET tenant=?,fingerprint=? WHERE hash=?',(tenant,fingerprint,hash_token(code)))
        with self.bridge(tenant).connect() as db:
            identity=self.bridge(tenant).get(db,'service_id')
        return dict(ok=True,business_code=tenant,service_id=identity,path='/b/'+tenant)

    def cleanup(self):
        with self.connect() as con:tenants=[r[0] for r in con.execute('SELECT id FROM businesses')]
        for tenant in tenants:
            try:self.bridge(tenant).cleanup()
            except Problem:pass

    def __call__(self,env,start):
        path=env.get('PATH_INFO','');method=env.get('REQUEST_METHOD','GET');status=200
        try:
            if path=='/health':result=dict(ok=True,version='2.6.0',multibusiness=True)
            elif path.startswith('/b/'):
                parts=path.split('/',3);tenant=parts[2];subpath='/'+(parts[3] if len(parts)>3 else '')
                bridge=self.bridge(tenant)
                if subpath=='/health':
                    with bridge.connect() as con:result=dict(ok=True,version='2.6.0',initialized=True,service_id=bridge.get(con,'service_id'),business_code=tenant)
                elif subpath.startswith('/v1/'):
                    if subpath=='/v1/login':self.limit(env.get('REMOTE_ADDR',''))
                    child=dict(env,PATH_INFO=subpath)
                    return bridge(child,start)
                else:raise Problem('Use the Folio Android app to connect to this business.',404)
            else:
                size=int(env.get('CONTENT_LENGTH') or 0)
                if size<0 or size>8192:raise Problem('Request too large.',413)
                if path not in ('/register','/admin/licenses','/admin/businesses'):raise Problem('Not found.',404)
                if path.startswith('/admin/'):
                    token=env.get('HTTP_AUTHORIZATION','').removeprefix('Bearer ')
                    if not hmac.compare_digest(token,self.admin_key):raise Problem('Operator authorization required.',401)
                else:self.limit(env.get('REMOTE_ADDR',''))
                b=json.loads(env['wsgi.input'].read(size)) if size else {}
                if not isinstance(b,dict):raise ValueError('Expected an object.')
                if path=='/register' and method=='POST':result=self.register(b)
                elif path=='/admin/licenses' and method=='POST':
                    code='FOLIO-'+secrets.token_urlsafe(24)
                    with self.connect() as con:con.execute('INSERT INTO licenses(hash,label,created) VALUES(?,?,?)',(hash_token(code),str(b.get('label',''))[:120],time.time()))
                    result=dict(activation=code)
                elif path=='/admin/businesses' and method=='GET':
                    with self.connect() as con:result=dict(businesses=[dict(r) for r in con.execute('SELECT id,name,active FROM businesses')])
                else:raise Problem('Method not allowed.',405)
        except Problem as e:status=e.status;result=dict(error=str(e))
        except (ValueError,KeyError,TypeError):status=400;result=dict(error='Invalid setup request. Check the required fields.')
        except Exception:
            status=500;result=dict(error='Setup could not complete. Retry with the same activation details.')
        from http.client import responses
        data=json.dumps(result).encode();start(f'{status} {responses[status]}',[('Content-Type','application/json'),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Content-Length',str(len(data)))]);return [data]

def create_app():
    relay=Relay()
    def cleaner():
        while True:
            try:relay.cleanup()
            except Exception:pass
            time.sleep(3600)
    threading.Thread(target=cleaner,daemon=True).start()
    return relay
