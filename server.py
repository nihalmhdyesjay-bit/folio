"""Folio single-business online bridge. Serve behind HTTPS; no desktop port exposure."""
from contextlib import contextmanager
import base64
import datetime as dt
import getpass
import hashlib
import hmac
import io
import json
import mimetypes
import os
import secrets
import sqlite3
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs
from protocol import identifier, number, money, sale_date, totals

ROOT = Path(__file__).parent
LOCK = threading.RLock()
SCHEMA = '''
CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,username TEXT UNIQUE NOT NULL,salt TEXT NOT NULL,hash TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,seq INTEGER UNIQUE NOT NULL,user_id TEXT NOT NULL,kind TEXT NOT NULL,request_hash TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',result TEXT NOT NULL DEFAULT '{}',created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,ts TEXT NOT NULL,action TEXT NOT NULL,actor TEXT NOT NULL,detail TEXT NOT NULL);
'''

class Problem(Exception):
    def __init__(self, text, status=400):
        super().__init__(text); self.status = status

def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()

class Bridge:
    def __init__(self, directory=None):
        self.folder = Path(directory or os.environ.get('FOLIO_DATA', str(ROOT/'data')))
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder/'folio-online.db'
        with self.connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA secure_delete=ON')
        try:
            with con:
                yield con
        finally:
            con.close()

    def get(self, con, key, default=''):
        r = con.execute('SELECT value FROM config WHERE key=?',(key,)).fetchone()
        return r[0] if r else default

    def set(self, con, key, value):
        con.execute('INSERT OR REPLACE INTO config VALUES(?,?)',(key,str(value)))

    def audit(self, con, action, actor, detail):
        con.execute('INSERT INTO audit(ts,action,actor,detail) VALUES(?,?,?,?)',(stamp(),action,actor,str(detail)[:1000]))

    def init(self, username, password, name):
        with LOCK, self.connect() as con:
            if self.get(con,'desktop_hash'):
                raise ValueError('Already initialized. Use the desktop Mobile sync screen to manage salespeople.')
            self.add_user(con,dict(username=username,password=password,name=name))
            key = secrets.token_urlsafe(40)
            self.set(con,'desktop_hash',hash_token(key))
            self.set(con,'service_id',secrets.token_hex(16))
            return key

    def add_user(self, con, b):
        username = str(b.get('username','')).strip().casefold()
        password = str(b.get('password',''))
        if not 2 <= len(username) <= 80 or not 10 <= len(password) <= 128:
            raise ValueError('Use a username of 2–80 characters and a password of 10–128 characters.')
        salt = secrets.token_hex(16); uid = secrets.token_hex(16)
        try:
            con.execute('INSERT INTO users(id,name,username,salt,hash) VALUES(?,?,?,?,?)',
                        (uid,str(b.get('name') or username).strip()[:100],username,salt,password_hash(password,salt)))
        except sqlite3.IntegrityError:
            raise ValueError('That username already exists.')
        self.audit(con,'create-user','desktop',uid)
        return dict(id=uid)

    def next_sequence(self,con):
        value=max(int(self.get(con,'sequence','0')),con.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0])+1
        self.set(con,'sequence',value)
        return value

    def event(self, row):
        return dict(id=row['id'],seq=row['seq'],kind=row['kind'],payload=json.loads(row['payload']),
                    status=row['status'],result={k:v for k,v in json.loads(row['result']).items() if k!='pdf'},created=row['created'])

    def authenticate(self, con, token, desktop=False):
        if desktop:
            stored=self.get(con,'desktop_hash')
            if not stored or not hmac.compare_digest(stored,hash_token(token)):
                raise Problem('Invalid desktop connection key.',401)
            return 'desktop'
        row=con.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>? AND u.active=1',(hash_token(token),time.time())).fetchone()
        if not row:raise Problem('Sign in again to sync.',401)
        return row

    def login(self, con, b, address):
        username=str(b.get('username','')).strip().casefold()[:80]
        key=hash_token(username+'|'+address)
        attempt=con.execute('SELECT * FROM attempts WHERE key=?',(key,)).fetchone()
        if attempt and attempt['until']>time.time():raise Problem('Please wait 30 seconds before trying again.',429)
        u=con.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        salt=u['salt'] if u else '00'*16
        valid=hmac.compare_digest(password_hash(str(b.get('password',''))[:129],salt),u['hash'] if u else '0'*64)
        if not u or not valid or not u['active']:
            count=(attempt['count'] if attempt else 0)+1
            con.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',(key,0 if count>=5 else count,time.time()+30 if count>=5 else 0))
            con.commit()
            raise Problem('Incorrect username or password.',401)
        con.execute('DELETE FROM attempts WHERE key=?',(key,))
        con.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        token=secrets.token_urlsafe(40);expires=time.time()+30*86400
        con.execute('INSERT INTO sessions VALUES(?,?,?)',(hash_token(token),u['id'],expires))
        return dict(token=token,user=dict(id=u['id'],name=u['name']),expires=expires,service_id=self.get(con,'service_id'))

    def catalog(self, con):
        raw=self.get(con,'catalog')
        if not raw:raise Problem('Connect desktop Folio and publish your customers and items first.',409)
        return json.loads(raw)

    def canonical(self, con, b, user, seq):
        c=self.catalog(con);date=sale_date(b.get('date'),c)
        mode=b.get('mode','Credit')
        if mode not in ('Credit','Cash','UPI','Bank'):raise ValueError('Choose Cash, UPI, Bank or Credit.')
        account=None
        if mode!='Credit':
            account_mode='Bank' if mode in ('UPI','Bank') else 'Cash'
            key=b.get('account_key') or 'bank:'+account_mode
            choices=c.get('money_accounts',[dict(key='bank:Bank',name='Bank',mode='Bank'),dict(key='bank:Cash',name='Cash',mode='Cash')])
            account=next((a for a in choices if a['key']==key and a['mode']==account_mode),None)
            if not account:raise ValueError('The selected payment account is unavailable. Refresh your accounts.')
        kind=b.get('kind')
        if kind=='invoice':
            customer=next((x for x in c['customers'] if x['id']==b.get('party_id')),None)
            if not customer:
                raw=b.get('customer') or {};name=' '.join(str(raw.get('name','')).split())
                if not name or len(name)>120:raise ValueError('Choose a customer or enter a new customer name.')
                customer=next((x for x in c['customers'] if x['name'].strip().casefold()==name.casefold()),None)
                if not customer:
                    state=raw.get('state') or c['company']['state']
                    if state not in c['states']:raise ValueError('Choose a valid customer state.')
                    customer=dict(id=None,name=name,phone=str(raw.get('phone',''))[:40],address=str(raw.get('address',''))[:500],state=state,state_code=c['states'][state],gstin='')
            source=b.get('lines') or []
            if not 1<=len(source)<=100:raise ValueError('Add between 1 and 100 item lines.')
            items={i['id']:i for i in c['items']};lines=[];need={}
            for line in source:
                it=items.get(line.get('item_id'))
                if not it:raise ValueError('An item is no longer available. Refresh items.')
                qty=number(line.get('qty'),'quantity',True,1e7)
                # Pricing and tax are set on the desktop, not accepted from the phone.
                lines.append(dict(item_id=it['id'],item=it['name'],unit=it['unit'],hsn=it['hsn'],qty=qty,rate=it['rate'],gst=it['gst']))
                need[it['id']]=need.get(it['id'],0)+qty
            for row in con.execute("SELECT payload FROM events WHERE kind='invoice' AND status!='rejected' AND seq>?",(c.get('cursor',0),)):
                for l in json.loads(row[0])['lines']:
                    if l['item_id'] in need:need[l['item_id']]+=l['qty']
            for iid,qty in need.items():
                if items[iid]['kind']=='product' and qty>items[iid]['stock']+1e-8:
                    raise ValueError('Insufficient available stock for '+items[iid]['name']+'. Refresh after the desktop updates stock.')
            t=totals(lines,int(customer['state_code'])==int(c['company']['state_code']),c['company']['round_off'])
            if 'expected_total' in b and abs(number(b['expected_total'],'expected total')-t['grand'])>.004:raise ValueError('Prices or tax changed since this bill was prepared. Discard this unsent bill and create it again with refreshed prices.')
            paid=money(number(b.get('paid_now',0),'amount received')) if mode!='Credit' else 0.
            if mode!='Credit' and (paid<=0 or paid>t['grand']+.004):raise ValueError('Amount received must be above zero and no more than the bill total.')
            return dict(date=date,no='M/%s/%06d'%(c['year']['label'],seq),customer=customer,lines=lines,totals=t,mode=mode,paid_now=paid,account_key=account['key'] if account else None,account_name=account['name'] if account else '',
                        notes=str(b.get('notes',''))[:1000],vehicle=str(b.get('vehicle',''))[:80],company=c['company'],year=c['year'])
        if kind!='receipt':raise ValueError('Choose an invoice or receipt.')
        if mode=='Credit':raise ValueError('Choose Cash, UPI or Bank for the receipt.')
        target=str(b.get('target',''));inv=None
        if target.startswith('desktop:'):
            inv=next((x for x in c['invoices'] if str(x['id'])==target[8:]),None)
        elif target.startswith('mobile:'):
            row=con.execute("SELECT * FROM events WHERE id=? AND kind='invoice' AND user_id=? AND status!='rejected'",(target[7:],user['id'])).fetchone()
            if row:
                p=json.loads(row['payload']);r=json.loads(row['result'])
                inv=next((x for x in c['invoices'] if x['id']==r.get('id')),None)
                if not inv and row['status']=='applied' and c.get('cursor',0)>=row['seq']:raise ValueError('This invoice is already settled or unavailable. Refresh your invoice list.')
                if not inv:inv=dict(id=None,no=p['no'],party=p['customer']['name'],party_id=p['customer']['id'],balance=money(p['totals']['grand']-p['paid_now']))
        if not inv:raise ValueError('Select an open invoice. Refresh the invoice list.')
        pending=0.
        for row in con.execute("SELECT payload FROM events WHERE kind='receipt' AND status!='rejected' AND seq>?",(c.get('cursor',0),)):
            p=json.loads(row[0])
            if p['invoice_no']==inv['no']:pending+=p['amount']
        value=money(number(b.get('amount'),'receipt amount',True))
        if value>inv['balance']-pending+.004:raise ValueError('Receipt exceeds the remaining invoice balance. Refresh before trying again.')
        return dict(date=date,no='R/%s/%06d'%(c['year']['label'],seq),target=target,invoice_no=inv['no'],party=inv['party'],party_id=inv['party_id'],amount=value,mode=mode,account_key=account['key'],account_name=account['name'],
                    reference=str(b.get('reference',''))[:150],notes=str(b.get('notes',''))[:1000],company=c['company'])

    def api(self, method, path, b, token, query, address):
        with LOCK, self.connect() as con:
            if path=='/v1/login' and method=='POST':return self.login(con,b,address)
            desktop=path.startswith('/v1/desktop/')
            user=self.authenticate(con,token,desktop)
            if desktop:
                book=b.get('book_id') if method=='POST' else query.get('book_id')
                bound=self.get(con,'book_id')
                if book and bound and book!=bound:raise Problem('This service is paired with different books. Do not merge separate businesses.',409)
                if path=='/v1/desktop/catalog' and method=='POST':
                    if not book:raise ValueError('Book identity is required.')
                    self.set(con,'book_id',identifier(book));c=b['catalog'];c['published']=stamp()
                    self.set(con,'catalog',json.dumps(c,allow_nan=False));return dict(ok=True,service_id=self.get(con,'service_id'))
                if path=='/v1/desktop/events' and method=='GET':
                    if not book or not bound:raise ValueError('Publish the desktop catalog first.')
                    return dict(events=[self.event(r) for r in con.execute('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 100',(int(query.get('after',0)),))])
                if path=='/v1/desktop/ack' and method=='POST':
                    if b.get('status') not in ('applied','rejected'):raise ValueError('Invalid result status.')
                    r=con.execute('SELECT * FROM events WHERE id=?',(b['id'],)).fetchone()
                    if not r:raise ValueError('Unknown mobile record.')
                    result={**json.loads(r['result']),**b['result']}
                    if b['status']=='applied':
                        result={k:result[k] for k in ('id','invoice_id') if isinstance(result.get(k),int)}
                        con.execute("UPDATE events SET status='applied',payload='{}',result=? WHERE id=?",(json.dumps(result),b['id']))
                    else:con.execute('UPDATE events SET status=?,result=? WHERE id=?',(b['status'],json.dumps(result),b['id']))
                    self.audit(con,b['status'],'desktop',b['id']);return dict(ok=True)
                if path=='/v1/desktop/documents' and method=='POST':
                    for update in b.get('updates',[]):
                        row=con.execute("SELECT result FROM events WHERE id=? AND status='applied'",(update['id'],)).fetchone()
                        if not row:continue
                        result=json.loads(row[0]);result.update(current=update.get('current'),voided=bool(update.get('voided')))
                        if update.get('pdf'):result['pdf']=update['pdf']
                        if result['voided']:result.pop('pdf',None)
                        con.execute('UPDATE events SET result=? WHERE id=?',(json.dumps(result),update['id']))
                    return dict(ok=True)
                if path=='/v1/desktop/status':
                    return dict(service_id=self.get(con,'service_id'),book_id=bound,published=json.loads(self.get(con,'catalog','{}')).get('published'),
                                counts={r[0]:r[1] for r in con.execute('SELECT status,COUNT(*) FROM events GROUP BY status')},
                                issues=[self.event(r) for r in con.execute("SELECT * FROM events WHERE status='rejected' ORDER BY seq DESC LIMIT 40")])
                if path=='/v1/desktop/retry' and method=='POST':
                    seq=self.next_sequence(con)
                    con.execute("UPDATE events SET status='pending',seq=?,result='{}' WHERE id=? AND status='rejected'",(seq,b['id']))
                    return dict(ok=True)
                if path=='/v1/desktop/team' and method=='GET':return dict(users=[dict(r) for r in con.execute('SELECT id,name,username,active FROM users ORDER BY name')])
                if path=='/v1/desktop/team' and method=='POST':return self.add_user(con,b)
                if path=='/v1/desktop/team/update' and method=='POST':
                    uid=b['id'];con.execute('UPDATE users SET active=? WHERE id=?',(1 if b.get('active') else 0,uid))
                    if b.get('password'):
                        if not 10<=len(b['password'])<=128:raise ValueError('Password must have 10–128 characters.')
                        salt=secrets.token_hex(16);con.execute('UPDATE users SET salt=?,hash=? WHERE id=?',(salt,password_hash(b['password'],salt),uid))
                    con.execute('DELETE FROM sessions WHERE user_id=?',(uid,));self.audit(con,'update-user','desktop',uid);return dict(ok=True)
            else:
                if path=='/v1/logout' and method=='POST':
                    con.execute('DELETE FROM sessions WHERE hash=?',(hash_token(token),));return dict(ok=True)
                if path=='/v1/catalog' and method=='GET':return self.catalog(con)
                if path=='/v1/events' and method=='GET':return dict(events=[self.event(r) for r in con.execute('SELECT * FROM events WHERE user_id=? ORDER BY seq DESC LIMIT 500',(user['id'],))])
                if path=='/v1/events' and method=='POST':
                    eid=identifier(b.get('id'));fingerprint=hash_token(json.dumps(b,sort_keys=True,separators=(',',':'),allow_nan=False))
                    old=con.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
                    if old:
                        if old['user_id']!=user['id'] or old['request_hash']!=fingerprint:raise Problem('This record identity was already used. Do not change a submitted bill.',409)
                        return self.event(old)
                    seq=self.next_sequence(con)
                    p=self.canonical(con,b,user,seq)
                    con.execute('INSERT INTO events(id,seq,user_id,kind,request_hash,payload,created) VALUES(?,?,?,?,?,?,?)',(eid,seq,user['id'],b['kind'],fingerprint,json.dumps(p),stamp()))
                    self.audit(con,'submit',user['id'],eid)
                    return self.event(con.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone())
                if path.startswith('/v1/document/') and method=='GET':
                    eid=identifier(path.rsplit('/',1)[1]);r=con.execute('SELECT * FROM events WHERE id=? AND user_id=?',(eid,user['id'])).fetchone()
                    if not r:raise Problem('Document not found.',404)
                    result=json.loads(r['result'])
                    if result.get('voided'):raise Problem('This record was cancelled or removed on the desktop.',409)
                    if r['status']=='rejected':raise Problem('Resolve this record on the desktop before issuing a document.',409)
                    if result.get('pdf'):return base64.b64decode(result['pdf'])
                    from documents import document_pdf
                    return document_pdf(self.event(r))
            raise Problem('Unknown request.',404)

    def __call__(self, env, start_response):
        path=env.get('PATH_INFO','/');method=env.get('REQUEST_METHOD','GET')
        headers=[('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),('Cache-Control','no-store'),
                 ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")]
        status=200;ctype='application/json'
        try:
            if path=='/health':result=dict(ok=True,version='2.2.0')
            elif path.startswith('/v1/'):
                size=int(env.get('CONTENT_LENGTH') or 0)
                if size>8*1024*1024:raise Problem('Request too large.',413)
                b=json.loads(env['wsgi.input'].read(size)) if size else {}
                if not isinstance(b,dict):raise ValueError('Expected an object.')
                token=env.get('HTTP_AUTHORIZATION','').removeprefix('Bearer ')
                q={k:v[0] for k,v in parse_qs(env.get('QUERY_STRING','')).items()}
                result=self.api(method,path,b,token,q,env.get('REMOTE_ADDR',''))
                if isinstance(result,bytes):ctype='application/pdf';headers.append(('Content-Disposition','inline; filename="Folio-document.pdf"'))
            else:
                if method!='GET':raise Problem('Method not allowed.',405)
                rel='index.html' if path=='/' else path.lstrip('/')
                file=(ROOT/'mobile'/rel).resolve();base=(ROOT/'mobile').resolve()
                if base not in file.parents or not file.is_file():raise Problem('Not found.',404)
                result=file.read_bytes();ctype=mimetypes.guess_type(file)[0] or 'application/octet-stream'
                if file.suffix=='.webmanifest':ctype='application/manifest+json'
                if file.name=='sw.js':headers.append(('Service-Worker-Allowed','/'))
            data=result if isinstance(result,bytes) else json.dumps(result,allow_nan=False).encode()
        except (ValueError,KeyError,TypeError) as e:
            status=400;data=json.dumps(dict(error=str(e))).encode()
        except Problem as e:
            status=e.status;data=json.dumps(dict(error=str(e))).encode()
        except Exception:
            import traceback;traceback.print_exc()
            status=500;data=b'{"error":"The sync service could not complete this request. Try again."}'
        from http.client import responses
        start_response('%d %s'%(status,responses.get(status,'Error')),headers+[('Content-Type',ctype),('Content-Length',str(len(data)))])
        return [data]


