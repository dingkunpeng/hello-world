#!/usr/bin/env python3
import json,re,requests
from pathlib import Path
from urllib.parse import urljoin

OUT=Path('output/bitget_probe'); OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0','Accept':'application/json,text/plain,*/*','Referer':'https://www.bitget.com/data-download'})
PAGE='https://www.bitget.com/data-download'
html=S.get(PAGE,timeout=30).text
scripts=re.findall(r'<script[^>]+src=["\']([^"\']+)',html,re.I)
page_chunk=next((urljoin(PAGE,s) for s in scripts if 'pages-data-download' in s),None)
report={'page_chunk':page_chunk,'contexts':{},'call_contexts':[],'module55167':[],'probes':[]}
if page_chunk:
    js=S.get(page_chunk,timeout=30).text
    for needle in ['/statistics/public/download/getPublicDataV2','/statistics/public/download/getSymbolList']:
        arr=[]; pos=0
        while True:
            i=js.find(needle,pos)
            if i<0: break
            arr.append(js[max(0,i-1800):min(len(js),i+2600)])
            pos=i+len(needle)
        report['contexts'][needle]=arr[:20]
    # A is getPublicDataV2, D is getSymbolList in this chunk. Capture call sites after aliases are declared.
    for pat,label in [(r'\bA\s*\(','A_call'),(r'\bD\s*\(','D_call'),(r'await\s+A\s*\(','await_A'),(r'await\s+D\s*\(','await_D')]:
        for m in re.finditer(pat,js):
            c=js[max(0,m.start()-1200):min(len(js),m.start()+2600)]
            # Exclude obvious component/function definitions if no data-download fields nearby.
            if any(k in c for k in ['startTime','endTime','symbol','product','type','dataType','period','date','file','download']):
                report['call_contexts'].append({'label':label,'context':c})
            if len(report['call_contexts'])>=120: break
    # Capture the helper module that constructs requests, to infer host/method/body semantics.
    for m in re.finditer(r'55167\(',js):
        report['module55167'].append(js[max(0,m.start()-3000):min(len(js),m.start()+7000)])
    # Also search literal module declaration forms.
    for needle in ['55167(e,a,t)','55167(e,t,n)','55167:','55167(e,']:
        i=js.find(needle)
        if i>=0: report['module55167'].append(js[max(0,i-3000):min(len(js),i+9000)])

bases=['https://www.bitget.com','https://api.bitget.com']
paths=['/statistics/public/download/getPublicDataV2','/statistics/public/download/getSymbolList']
payloads=[{}, {'type':'spot'}, {'businessType':'spot'}, {'productType':'spot'}, {'category':'spot'}, {'symbol':'BTCUSDT'}, {'coin':'BTCUSDT'}, {'type':'spot','symbol':'BTCUSDT'}]
for base in bases:
  for path in paths:
    url=base+path
    for method in ['GET','POST']:
      for p in payloads:
        try:
          r=S.get(url,params=p,timeout=15) if method=='GET' else S.post(url,json=p,timeout=15)
          report['probes'].append({'url':url,'method':method,'payload':p,'status':r.status_code,'content_type':r.headers.get('content-type'),'text':r.text[:1000]})
        except Exception as e: report['probes'].append({'url':url,'method':method,'payload':p,'error':str(e)})

(OUT/'probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
with open(OUT/'call_contexts.txt','w',encoding='utf-8') as f:
  for x in report['call_contexts']: f.write(f"\n===== {x['label']} =====\n{x['context']}\n")
with open(OUT/'request_helper.txt','w',encoding='utf-8') as f:
  for x in report['module55167']: f.write('\n===== 55167 =====\n'+x+'\n')
with open(OUT/'responses.txt','w',encoding='utf-8') as f:
  for x in report['probes']: f.write(json.dumps(x,ensure_ascii=False)+'\n')
print(json.dumps({'page_chunk':page_chunk,'call_context_count':len(report['call_contexts']),'module_context_count':len(report['module55167'])},ensure_ascii=False,indent=2))
