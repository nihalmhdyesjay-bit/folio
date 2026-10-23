"""Constrained provider response: data only, no executable commands."""
def obj(properties,required=()):return dict(type='OBJECT',properties=properties,**({'required':list(required)} if required else {}))
def text(**kw):return dict(type='STRING',**kw)
line=obj({**{k:text() for k in ('item','ledger_name','line_narration')},**{k:dict(type='NUMBER') for k in ('qty','rate','gst','dr','cr')}})
payload=obj({**{k:text() for k in ('no','date','party_name','vehicle','notes','pay_mode','pay_account','jv_no','ref','narration','mode','type','account_name','head')},**{k:dict(type='NUMBER') for k in ('paid_now','amount')},'lines':dict(type='ARRAY',items=line)})
SCHEMA=obj({
 'reply':text(),
 'drafts':dict(type='ARRAY',items=obj({'kind':text(enum=['invoice','purchase','journal','money']),'payload':payload},('kind','payload'))),
 'reports':dict(type='ARRAY',items=obj({'kind':text(enum=['sales','purchases','cash','bank','tb','pl','bs','ledger']),'from':text(),'to':text()},('kind','from','to'))),
 'edit_lookup':obj({'kind':text(enum=['invoice','purchase']),'query':text()},('kind','query'))
},('reply','drafts','reports'))

