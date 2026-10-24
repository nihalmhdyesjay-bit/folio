"""Constrained provider response: data only, no executable commands."""
def obj(properties,required=()):return dict(type='OBJECT',properties=properties,**({'required':list(required)} if required else {}))
def text(**kw):return dict(type='STRING',**kw)
line=obj({**{k:text() for k in ('item','ledger_name','line_narration')},**{k:dict(type='NUMBER') for k in ('qty','rate','gst','dr','cr')}})
payload=text(description='JSON-encoded accounting object using the fields in the system instruction. For invoice and purchase include no, date, party_name and all lines with item, qty, rate, gst. Include every legible detail. Omit unknown fields. This string will be parsed as JSON by the app.')
SCHEMA=obj({
 'reply':text(),
 'actions':dict(type='ARRAY',items=obj({'name':text(),'arguments':text(description='JSON-encoded arguments object matching the desktop business_tools catalog')},('name','arguments'))),
 'execute':dict(type='BOOLEAN',description='True only when the user requests saving or changing entries now. False for questions, previews, examples and hypothetical requests.'),
 'drafts':dict(type='ARRAY',items=obj({'kind':text(enum=['invoice','purchase','journal','money']),'payload':payload},('kind','payload'))),
 'reports':dict(type='ARRAY',items=obj({'kind':text(enum=['sales','purchases','cash','bank','tb','pl','bs','ledger','mis']),'from':text(),'to':text(),'format':text(enum=['chat','xlsx'])},('kind','from','to','format'))),
 'edit_lookup':obj({'kind':text(enum=['invoice','purchase']),'query':text()},('kind','query'))
},('reply','drafts','reports'))

