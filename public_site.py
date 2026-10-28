"""Serve only packaged public website files; all API requests retain their routing."""
from pathlib import Path
import hashlib,mimetypes

class PublicSite:
    def __init__(self,app,root=None):
        self.app=app;self.files={}
        root=Path(root or Path(__file__).parent/'website')
        for p in root.rglob('*'):
            if not p.is_file():continue
            rel=p.relative_to(root).as_posix()
            if rel not in ('index.html','style.css','site.js','download-links.js') and not rel.startswith(('assets/','downloads/')):continue
            mime=mimetypes.guess_type(rel)[0] or 'application/octet-stream'
            # Installers stream from disk instead of occupying the sync server's RAM.
            if rel.endswith(('.exe','.apk')):
                with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
                self.files['/'+rel]=(p,mime,'"'+digest+'"')
            else:
                data=p.read_bytes()
                self.files['/'+rel]=(data,mime,'"'+hashlib.sha256(data).hexdigest()+'"')
        if '/index.html' in self.files:self.files['/']=self.files['/index.html']

    def __call__(self,env,start):
        path=env.get('PATH_INFO','');method=env.get('REQUEST_METHOD','GET')
        record=self.files.get(path)
        if record is None or method not in ('GET','HEAD'):return self.app(env,start)
        data,mime,etag=record
        headers=[('Content-Type',mime),('Cache-Control','no-cache' if path in ('/','/index.html','/download-links.js') or path.endswith('/stable.json') else 'public, max-age=300'),('ETag',etag),('X-Content-Type-Options','nosniff'),('Referrer-Policy','strict-origin-when-cross-origin'),('X-Frame-Options','DENY')]
        if env.get('HTTP_IF_NONE_MATCH')==etag:start('304 Not Modified',headers);return []
        size=data.stat().st_size if isinstance(data,Path) else len(data)
        start('200 OK',headers+[('Content-Length',str(size))])
        if method=='HEAD':return []
        if isinstance(data,Path):
            def chunks():
                with data.open('rb') as f:
                    while chunk:=f.read(128*1024):yield chunk
            return chunks()
        return [data]
