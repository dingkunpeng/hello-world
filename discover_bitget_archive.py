#!/usr/bin/env python3
import json,re,requests
from urllib.parse import urljoin
from pathlib import Path

OUT=Path('output/bitget_discovery'); OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0'})
url='https://www.bitget.com/data-download'
r=S.get(url,timeout=30); print('page',r.status_code,len(r.text)); r.raise_for_status(); html=r.text
scripts=re.findall(r'<script[^>]+src=["\']([^"\']+)',html,re.I)
report={'page_status':r.status_code,'html_len':len(html),'scripts':scripts,'html_hits':[],'js_hits':[]}
patterns=['data-download','download','depth','history','archive','/api/','api.bitget','static','oss']
for p in patterns:
    for m in re.finditer(re.escape(p),html,re.I):
        report['html_hits'].append({'pattern':p,'context':html[max(0,m.start()-180):m.start()+500]})
        if len(report['html_hits'])>80: break

for src in scripts[:80]:
    full=urljoin(url,src)
    try:
        rr=S.get(full,timeout=20)
        if rr.status_code!=200 or len(rr.text)>12_000_000: continue
        text=rr.text
        if any(k.lower() in text.lower() for k in ['data-download','depth','download']):
            hits=[]
            # extract URL-like strings and endpoint-like strings around useful keywords
            for pat in [r'https?://[^"\'\s)]+',r'/api/[^"\'\s)]+',r'[^"\']{0,100}data-download[^"\']{0,250}',r'[^"\']{0,100}depth[^"\']{0,250}',r'[^"\']{0,100}download[^"\']{0,250}']:
                for x in re.findall(pat,text,re.I)[:100]:
                    if isinstance(x,tuple): x=' '.join(x)
                    if any(k in str(x).lower() for k in ['download','depth','history','api','archive']): hits.append(str(x)[:1000])
            if hits:
                report['js_hits'].append({'src':full,'size':len(text),'hits':hits[:120]})
    except Exception as e:
        report['js_hits'].append({'src':full,'error':str(e)})

(OUT/'discovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'page_status':report['page_status'],'html_len':report['html_len'],'script_count':len(scripts),'html_hits':report['html_hits'][:15],'js_hit_files':[{'src':x.get('src'),'size':x.get('size'),'hits':x.get('hits',[])[:15]} for x in report['js_hits'] if x.get('hits')]},ensure_ascii=False,indent=2))
