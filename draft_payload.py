import math, datetime

def clean_payload(kind, payload):
    fields = {'invoice': 'no date party_id party_name vehicle notes pay_mode paid_now pay_account lines',
              'purchase': 'no date party_id party_name notes lines',
              'journal': 'jv_no date ref narration lines',
              'money': 'date mode type amount party_id party_name account_key account_name head ref narration cleared'}
    numeric = {'party_id','item_id','qty','rate','gst','paid_now','amount','dr','cr'}
    def clean(values, allowed):
        out = {}
        for key in allowed.split():
            if key not in values or key=='lines': continue
            value=values[key]
            if key in numeric:
                if value is None or value=='':out[key]=None if key.endswith('_id') else '';continue
                if isinstance(value,bool):raise ValueError('Enter a number for '+key)
                try:value=float(value)
                except (TypeError,ValueError):raise ValueError('Enter a number for '+key)
                if not math.isfinite(value) or abs(value)>1e12:raise ValueError('Invalid number for '+key)
                if key.endswith('_id'):
                    if value<=0 or value!=int(value):raise ValueError('Choose a valid record for '+key)
                    value=int(value)
            else:
                if not isinstance(value,str):raise ValueError('Invalid text for '+key)
                value=value[:2000]
            out[key]=value
        return out
    result=clean(payload,fields[kind])
    if result.get('date'):
        result['date']=datetime.date.fromisoformat(result['date']).isoformat()
    for key in ('mode','pay_mode'):
        if result.get(key) and result[key] not in ('Cash','Bank','Credit'):raise ValueError('Choose Cash, Bank or Credit.')
    if kind!='money':
        lines=payload.get('lines',[])
        if not isinstance(lines,list) or len(lines)>500 or any(not isinstance(l,dict) for l in lines):raise ValueError('Use up to 500 lines per draft.')
        allowed='account_key ledger_name dr cr line_narration' if kind=='journal' else 'item_id item qty rate gst'
        result['lines']=[clean(line,allowed) for line in lines]
    return result

