"""Cloud entrypoint: one business per service, persistent SQLite, authenticated migration."""
import base64,hmac,io,json,os,sqlite3,tempfile
from contextlib import closing
from pathlib import Path
from server import Bridge,LOCK,hash_token

def prepare(bridge):
    with LOCK,bridge.connect() as con:
        if bridge.get(con,'desktop_hash'):return
        key=os.environ.get('FOLIO_DESKTOP_KEY','')
        if len(key)<30:raise RuntimeError('Set a private FOLIO_DESKTOP_KEY of at least 30 characters.')
        mode=os.environ.get('FOLIO_SETUP_MODE','').lower()
        if mode=='migrate':return
        if mode!='new':raise RuntimeError('Set FOLIO_SETUP_MODE to new or migrate.')
        bridge.add_user(con,dict(username=os.environ.get('FOLIO_FIRST_USERNAME',''),password=os.environ.get('FOLIO_FIRST_PASSWORD',''),name=os.environ.get('FOLIO_FIRST_NAME','Sales')))
        import secrets
        bridge.set(con,'desktop_hash',hash_token(key));bridge.set(con,'service_id',secrets.token_hex(16))

class Cloud:
    def __init__(self,bridge):self.bridge=bridge
    def __call__(self,env,start):
        path=env.get('PATH_INFO','')
        if path not in ('/health','/admin/restore'):
            return self.bridge(env,start)
        status=200
        try:
            if path=='/health':
                with self.bridge.connect() as con:sid=self.bridge.get(con,'service_id')
                result=dict(ok=True,version='2.4.0',service_id=sid,initialized=bool(sid))
            else:
                token=env.get('HTTP_AUTHORIZATION','').removeprefix('Bearer ')
                key=os.environ.get('FOLIO_DESKTOP_KEY','')
                if len(key)<30 or not hmac.compare_digest(token,key):
                    status=401;raise ValueError('Invalid migration connection key.')
                if env.get('REQUEST_METHOD')!='POST' or os.environ.get('FOLIO_SETUP_MODE')!='migrate':
                    status=403;raise ValueError('Migration is not enabled.')
                size=int(env.get('CONTENT_LENGTH') or 0)
                if size<1 or size>64*1024*1024:raise ValueError('Migration snapshot must be smaller than 64 MB.')
                body=json.loads(env['wsgi.input'].read(size));raw=base64.b64decode(body['database'],validate=True)
                with LOCK:
                    with self.bridge.connect() as con:
                        if self.bridge.get(con,'desktop_hash'):
                            status=409;raise ValueError('This server is already initialized. Existing records cannot be overwritten.')
                    with tempfile.TemporaryDirectory(dir=self.bridge.folder) as folder:
                        snapshot=Path(folder)/'snapshot.db';snapshot.write_bytes(raw)
                        with closing(sqlite3.connect(snapshot)) as src:
                            if src.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Damaged migration snapshot.')
                            tables={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                            if not {'config','users','sessions','events','audit','attempts'}<=tables:raise ValueError('Not a Folio sync snapshot.')
                            config=dict(src.execute('SELECT key,value FROM config'))
                            if not config.get('service_id') or not hmac.compare_digest(config.get('desktop_hash',''),hash_token(key)):raise ValueError('Snapshot does not match the original desktop connection key.')
                            with self.bridge.connect() as dst:src.backup(dst)
                result=dict(ok=True,service_id=config['service_id'])
        except (ValueError,KeyError,TypeError,sqlite3.DatabaseError):
            if status==200:status=400
            result=dict(error='Migration could not be accepted. Check the original connection key, setup mode and snapshot; existing server data is never overwritten.')
        data=json.dumps(result).encode();from http.client import responses
        start(str(status)+' '+responses[status],[('Content-Type','application/json'),('Content-Length',str(len(data))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff')]);return [data]

def create_app():
    if not os.environ.get('FOLIO_DATA'):
        raise RuntimeError('Set FOLIO_DATA to the persistent volume mount before starting Folio.')
    bridge=Bridge();prepare(bridge);return Cloud(bridge)
