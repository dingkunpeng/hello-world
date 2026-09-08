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
report={'page_chunk':page_chunk,'contexts':{},'probes':[]}
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

bases=['https://www.bitget.com','https://api.bitget.com']
paths=['/statistics/public/download/getPublicDataV2','/statistics/public/download/getSymbolList']
# Generic probes to learn method/validation error fields.
payloads=[
    {},
    {'type':'spot'},
    {'businessType':'spot'},
    {'productType':'spot'},
    {'category':'spot'},
    {'symbol':'BTCUSDT'},
    {'coin':'BTCUSDT'},
    {'type':'spot','symbol':'BTCUSDT'},
]
for base in bases:
  for path in paths:
    url=base+path
    for method in ['GET','POST']:
      for p in payloads:
        try:
          if method=='GET': r=S.get(url,params=p,timeout=15)
          else: r=S.post(url,json=p,timeout=15)
          report['probes'].append({'url':url,'method':method,'payload':p,'status':r.status_code,'content_type':r.headers.get('content-type'),'text':r.text[:3000]})
        except Exception as e:
          report['probes'].append({'url':url,'method':method,'payload':p,'error':str(e)})

(OUT/'probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
with open(OUT/'contexts.txt','w',encoding='utf-8') as f:
  for k,arr in report['contexts'].items():
    f.write('\n===== '+k+' =====\n')
    for x in arr: f.write(x+'\n\n')
with open(OUT/'responses.txt','w',encoding='utf-8') as f:
  for x in report['probes']:
    f.write(json.dumps(x,ensure_ascii=False)+'\n')
print(json.dumps({'page_chunk':page_chunk,'context_counts':{k:len(v) for k,v in report['contexts'].items()},'interesting':[x for x in report['probes'] if x.get('status') not in (404,403)][:30]},ensure_ascii=False,indent=2))
