const CACHE='folio-mobile-2.2.0';
const SHELL=['/','/index.html','/app.css','/app.js','/manifest.webmanifest','/assets/logo.png','/assets/icon-192.png','/assets/icon-512.png','/assets/Inter.ttf'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL))));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('folio-mobile-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{
 const url=new URL(e.request.url);if(url.origin!==location.origin||url.pathname.startsWith('/v1/')||e.request.method!=='GET')return;
 if(e.request.mode==='navigate'){e.respondWith(fetch(e.request).catch(()=>caches.match('/index.html')));return}
 if(SHELL.includes(url.pathname))e.respondWith(caches.match(e.request).then(c=>c||fetch(e.request)));
});
