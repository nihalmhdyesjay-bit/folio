"""A4 mobile documents. Accepted invoices are replaced by the desktop's PDF."""
import io
from html import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

def document_pdf(event):
    p=event.get('result',{}).get('current') or event['payload'];company=p['company'];styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='SmallFolio',fontName='Helvetica',fontSize=9,leading=13,textColor=colors.HexColor('#242B36')))
    def text(s,style='SmallFolio'):return Paragraph(escape(str(s or '')).replace('\n','<br/>'),styles[style])
    buf=io.BytesIO();doc=SimpleDocTemplate(buf,pagesize=A4,rightMargin=35,leftMargin=35,topMargin=35,bottomMargin=40)
    story=[text(company['company'],'Title'),text(company.get('address')),text('GSTIN: '+company.get('gstin','')+' | Phone: '+company.get('phone','')),Spacer(1,16)]
    title='SALES BILL' if event['kind']=='invoice' else 'PAYMENT RECEIPT'
    story += [text(title,'Heading2'),text(p['no']+' | '+p['date'])]
    if event['status']!='applied':story += [text('PENDING DESKTOP SYNC - subject to stock and accounts validation.','Heading3')]
    if event['kind']=='invoice':
        story += [Spacer(1,10),text('Bill to: '+p['customer']['name']),text(p['customer'].get('address')),Spacer(1,14)]
        rows=[['Description / HSN','Qty','Rate','GST %','Amount']]
        for l in p['lines']:rows.append([text(l['item']+'\nHSN '+str(l.get('hsn',''))),text('%g %s'%(l['qty'],l['unit'])),text('%.2f'%l['rate']),text('%g'%l['gst']),text('%.2f'%l['total'])])
        table=Table(rows,colWidths=[230,64,75,55,101],repeatRows=1,hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#17243A')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#DDE4EF')),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9)]))
        story += [table,Spacer(1,16)]
        t=p['totals']
        for label,key in [('Taxable value','taxable'),('CGST','cgst'),('SGST','sgst'),('IGST','igst'),('Round off','round_off'),('TOTAL INR','grand')]:story.append(text('%s: %.2f'%(label,t[key]),'Heading3' if key=='grand' else 'SmallFolio'))
        story += [text('Received now: INR %.2f (%s)'%(p['paid_now'],p['mode'])),text('Balance: INR %.2f'%(t['grand']-p['paid_now'])),Spacer(1,12),text(company.get('terms'))]
    else:
        story += [Spacer(1,18),text('Received from: '+p['party']),text('Against invoice: '+p['invoice_no']),text('Amount received: INR %.2f'%p['amount'],'Heading2'),text('Payment method: '+p['mode']),text('Reference: '+p.get('reference','')),text(p.get('notes'))]
    def footer(canvas,document):
        canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#667085'))
        canvas.drawString(35,24,'Folio | Dottedline Imaginations');canvas.drawRightString(A4[0]-35,24,'Page %s'%document.page)
    doc.build(story,onFirstPage=footer,onLaterPages=footer);return buf.getvalue()
