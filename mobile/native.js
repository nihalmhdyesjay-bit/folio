let nativeSequence=0;
const nativePending=new Map();
window.folioReply=(id,response)=>{
 const pending=nativePending.get(String(id));if(!pending)return;nativePending.delete(String(id));
 if(response.status<200||response.status>=300){let message=response.error;try{message=message||JSON.parse(response.body).error}catch(e){}const error=Error(message||'Could not reach Folio server.');error.status=response.status;pending.reject(error);return}
 try{pending.resolve(response.pdf?response.body:JSON.parse(response.body))}catch(e){pending.reject(Error('Invalid response from Folio server.'))}
};
function nativeRequest(path,body,raw=false){return new Promise((resolve,reject)=>{
 const id=String(++nativeSequence);nativePending.set(id,{resolve,reject});
 FolioNative.request(id,path,body===undefined?'GET':'POST',body===undefined?'':JSON.stringify(body),session?.token||'');
})}
function offlineDocument(record){
 const p=eventData(record),invoice=record.body.kind==='invoice',t=p.totals||{};
 const title=invoice?'SALES BILL':'PAYMENT RECEIPT';
 const rows=(p.lines||[]).map(l=>`<tr><td>${esc(l.item)}<br><small>HSN ${esc(l.hsn||'')}</small></td><td>${esc(l.qty)} ${esc(l.unit)}</td><td>${cash(l.rate)}</td><td>${esc(l.gst)}%</td><td>${cash(l.total??l.qty*l.rate*(1+l.gst/100))}</td></tr>`).join('');
 return `<!doctype html><html><head><meta charset="utf-8"><style>@page{size:A4;margin:14mm}body{font:11pt sans-serif;color:#17243a}h1{font-size:22pt}h2{font-size:16pt}p{line-height:1.5}table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{padding:8px;border-bottom:1px solid #ccd3dd;text-align:right;overflow-wrap:anywhere}th:first-child,td:first-child{width:36%;text-align:left}thead{display:table-header-group}tr{break-inside:avoid}.pending{padding:12px;background:#fff2cb;color:#614600}footer{margin-top:30px;font-size:9pt}</style></head><body><h1>${esc(p.company?.company||'Folio')}</h1><p>${esc(p.company?.address||'')}<br>GSTIN: ${esc(p.company?.gstin||'')} · Phone: ${esc(p.company?.phone||'')}</p><h2>${title}</h2><p class="pending">OFFLINE COPY — VERIFY WITH DESKTOP<br>Stock, payments and any later changes must be checked when online sync completes.</p><p>Reference: ${esc(p.no||'Phone-'+record.id)}<br>Date: ${esc(p.date)}<br>Customer: ${esc(p.customer?.name||p.party||'')}</p>${invoice?`<table><thead><tr><th>Item / HSN</th><th>Qty</th><th>Rate</th><th>GST</th><th>Amount INR</th></tr></thead><tbody>${rows}</tbody></table><p>Taxable: INR ${cash(t.taxable)}<br>CGST: INR ${cash(t.cgst)} · SGST: INR ${cash(t.sgst)} · IGST: INR ${cash(t.igst)}<br>Round off: INR ${cash(t.round_off)}</p><h2>Total: INR ${cash(t.grand)}</h2><p>Received: INR ${cash(p.paid_now)} (${esc(p.mode)})<br>Balance at entry: INR ${cash((t.grand||0)-(p.paid_now||0))}</p>`:`<p>Against: ${esc(p.invoice_no)}</p><h2>Received: INR ${cash(p.amount)}</h2><p>Method: ${esc(p.mode)}<br>Reference: ${esc(p.reference||'')}</p>`}<p>${esc(p.notes||'')}</p><footer>Folio · Dottedline Imaginations<br>Phone record ${esc(record.id)}</footer></body></html>`;
}
