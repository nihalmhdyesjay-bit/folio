"""Small, dependency-free validation shared with Folio desktop."""
import datetime as dt
import math
import uuid
from decimal import Decimal, ROUND_HALF_UP

def money(v):
    return float(Decimal(str(v)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))

def number(v, label, positive=False, maximum=1e10):
    try:
        n = float(v)
    except (ValueError, TypeError):
        raise ValueError('Enter a valid ' + label + '.')
    if not math.isfinite(n) or n < 0 or n > maximum or (positive and n <= 0):
        raise ValueError('Enter a valid positive ' + label + '.')
    return n

def identifier(v):
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError):
        raise ValueError('Invalid record identity.')

def today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()

def sale_date(value, catalog):
    date = dt.date.fromisoformat(str(value)).isoformat()
    if not catalog['year']['start'] <= date <= min(catalog['year']['end'], today()):
        raise ValueError('Choose a date in the active financial year, no later than today.')
    if catalog['year']['locked']:
        raise ValueError('This financial year is locked on the desktop.')
    return date

def totals(lines, intra, round_off):
    result = dict(taxable=0., cgst=0., sgst=0., igst=0., total=0.)
    for l in lines:
        taxable = money(l['qty'] * l['rate'])
        cgst = money(taxable * l['gst'] / 200) if intra else 0.
        sgst = cgst
        igst = 0. if intra else money(taxable * l['gst'] / 100)
        l.update(taxable=taxable, cgst=cgst, sgst=sgst, igst=igst, total=money(taxable+cgst+sgst+igst))
        for k in result:
            result[k] += l[k]
    result = {k:money(v) for k,v in result.items()}
    result['grand'] = float(Decimal(str(result['total'])).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if round_off else result['total']
    result['tax'] = money(result['cgst']+result['sgst']+result['igst'])
    result['round_off'] = money(result['grand']-result['total'])
    return result
