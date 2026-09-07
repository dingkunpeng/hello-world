#!/usr/bin/env python3
import csv, gzip, io, json, requests
from pathlib import Path

API='https://www.okx.com/api/v5/public/market-data-history'
TARGETS=[1771128171025,1771128192274,1771128275429]
WINDOW=1500
OUT=Path('output/candidate005_okx_tbt'); OUT.mkdir(parents=True,exist_ok=True)
params={
    'module':'6','instType':'SPOT','dateAggrType':'daily',
    'begin':'1771113600000','end':'1771200000000','instIdList':'BTC-USDT'
}
r=requests.get(API,params=params,timeout=30); r.raise_for_status(); js=r.json()
groups=js['data'][0]['details'][0]['groupDetails']
item=next(x for x in groups if x['filename']=='BTC-USDT.OK.csv.gz' and x['dateTs']=='1771113600000')
url=item['url']
print('signed_url obtained', item['sizeMB'],'MB')

resp=requests.get(url,stream=True,timeout=90); resp.raise_for_status(); resp.raw.decode_content=False
matches=[]; sample=[]; header=None; count=0
with gzip.GzipFile(fileobj=resp.raw,mode='rb') as gz:
    txt=io.TextIOWrapper(gz,encoding='utf-8',errors='replace',newline='')
    for line in txt:
        count += 1
        s=line.rstrip('\r\n')
        if count<=8: sample.append(s[:5000])
        if count==1: header=s
        # fast timestamp token scan: support epoch ms/us/ns and ISO time
        hit=False
        for t in TARGETS:
            if str(t) in s or str(t*1000) in s or str(t*1000000) in s:
                hit=True; break
        if not hit and ('04:02:5' in s or '04:03:1' in s or '04:04:3' in s):
            hit=True
        if hit and len(matches)<4000:
            matches.append({'line':count,'text':s[:20000]})
        # no early break: timestamp format may be unknown; stream entire file once
print('lines',count,'matches',len(matches))
report={'api_params':params,'filename':item['filename'],'sizeMB':item['sizeMB'],'sample':sample,'matches':matches}
(OUT/'inspect.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
with open(OUT/'matches.txt','w',encoding='utf-8') as f:
    for x in matches: f.write(f"{x['line']} {x['text']}\n")
print(json.dumps({'sample':sample,'first_matches':matches[:40]},ensure_ascii=False,indent=2))
