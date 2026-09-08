#!/usr/bin/env python3
import json,re,requests
from urllib.parse import urljoin
from pathlib import Path

OUT=Path('output/bitget_discovery'); OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0','Accept-Language':'en-US,en;q=0.9'})
PAGE='https://www.bitget.com/data-download'
r=S.get(PAGE,timeout=30); r.raise_for_status(); html=r.text
scripts=re.findall(r'<script[^>]+src=["\']([^"\']+)',html,re.I)
# Prioritize the actual data-download chunk, then inspect all JS chunks that look relevant.
ordered=[]
for src in scripts:
    if 'pages-data-download' in src: ordered.insert(0,src)
    elif src not in ordered: ordered.append(src)

report={'page_status':r.status_code,'html_len':len(html),'scripts':scripts,'files':[],'candidate_endpoints':[]}
endpoint_set=set()
keywords=['depth','download','archive','history','candlestick','transaction','spot','orderbook','order-book','dataDownload','data-download','downloadUrl','fileUrl','fileName','symbol']

for src in ordered:
    full=urljoin(PAGE,src)
    try:
        rr=S.get(full,timeout=30); rr.raise_for_status(); text=rr.text
    except Exception as e:
        report['files'].append({'src':full,'error':str(e)}); continue
    if len(text)>20_000_000: continue
    lower=text.lower()
    if not any(k.lower() in lower for k in keywords): continue
    snippets=[]
    for kw in keywords:
        pos=0
        while True:
            i=lower.find(kw.lower(),pos)
            if i<0: break
            snippets.append({'keyword':kw,'context':text[max(0,i-350):min(len(text),i+800)]})
            pos=i+len(kw)
            if len(snippets)>=250: break
        if len(snippets)>=250: break

    # Extract URL / API-looking literals and nearby literal route fragments.
    pats=[
        r'https?://[^"\'`\\\s)]+',
        r'["\'`](/[^"\'`]{2,220}(?:download|history|depth|archive|data|spot|file)[^"\'`]*)["\'`]',
        r'["\'`]([^"\'`]{0,80}/api/[^"\'`]{1,220})["\'`]',
        r'["\'`]([^"\'`]{0,80}/v[123]/[^"\'`]{1,220})["\'`]',
    ]
    found=[]
    for pat in pats:
        for m in re.finditer(pat,text,re.I):
            val=m.group(1) if m.groups() else m.group(0)
            val=val.replace('\\/','/')
            lv=val.lower()
            if any(k in lv for k in ['download','history','depth','archive','data','spot','file','api']):
                found.append(val[:500])
                if val not in endpoint_set:
                    endpoint_set.add(val); report['candidate_endpoints'].append(val[:500])
    report['files'].append({'src':full,'size':len(text),'snippets':snippets[:250],'found':found[:300]})

# Extra compact list: strings around likely HTTP client calls.
compact=[]
for f in report['files']:
    for s in f.get('snippets',[]):
        c=s['context']
        if any(x in c for x in ['axios','fetch(','request(','get(','post(','http','/api/','/v1/','/v2/','/v3/']):
            compact.append({'src':f['src'],'keyword':s['keyword'],'context':c})
report['http_contexts']=compact[:500]

(OUT/'discovery_deep.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
# Human-readable grep file for quick inspection.
with open(OUT/'endpoint_candidates.txt','w',encoding='utf-8') as f:
    for x in report['candidate_endpoints']:
        f.write(x+'\n')
with open(OUT/'http_contexts.txt','w',encoding='utf-8') as f:
    for x in report['http_contexts']:
        f.write(f"\n### {x['src']} [{x['keyword']}]\n{x['context']}\n")
print(json.dumps({'page_status':r.status_code,'files_scanned':len(report['files']),'candidate_endpoints':report['candidate_endpoints'][:120],'http_context_count':len(report['http_contexts'])},ensure_ascii=False,indent=2))
