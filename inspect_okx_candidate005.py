#!/usr/bin/env python3
import io, json, os, re, tarfile, requests
from pathlib import Path

URL = 'https://static.okx.com/cdn/okx/match/orderbook/L2/5000lv/daily/20260215/BTC-USDT-L2orderbook-5000lv-2026-02-15.tar.gz'
TARGET_MS = 1771128171025
OUT = Path('output/candidate005_okx')
OUT.mkdir(parents=True, exist_ok=True)
ARCHIVE = Path('/tmp/okx5000.tar.gz')

with requests.get(URL, stream=True, timeout=60) as r:
    r.raise_for_status()
    with open(ARCHIVE, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024*1024):
            if chunk:
                f.write(chunk)
print('downloaded', ARCHIVE.stat().st_size)

report = {'url': URL, 'archive_bytes': ARCHIVE.stat().st_size, 'members': [], 'samples': {}, 'matches': []}
num_re = re.compile(r'(?<!\d)(\d{13,16})(?!\d)')
with tarfile.open(ARCHIVE, 'r:gz') as tf:
    members = [m for m in tf.getmembers() if m.isfile()]
    report['members'] = [{'name':m.name,'size':m.size} for m in members]
    print('members', report['members'])
    for m in members:
        raw = tf.extractfile(m)
        if raw is None: continue
        txt = io.TextIOWrapper(raw, encoding='utf-8', errors='replace', newline='')
        sample=[]
        line_no=0
        local_matches=[]
        for line in txt:
            line_no += 1
            s=line.rstrip('\n\r')
            if line_no <= 5:
                sample.append(s[:5000])
            hit=False
            if '2026-02-15 04:02:51' in s or '2026-02-15T04:02:51' in s or '1771128171' in s:
                hit=True
            if not hit:
                for tok in num_re.findall(s):
                    try:
                        v=int(tok)
                    except: continue
                    # normalize microseconds to ms if needed
                    vm = v//1000 if v>10**14 else v
                    if abs(vm-TARGET_MS) <= 2500:
                        hit=True; break
            if hit and len(local_matches) < 500:
                local_matches.append({'member':m.name,'line':line_no,'text':s[:12000]})
            if len(local_matches) >= 500:
                break
        report['samples'][m.name] = sample
        report['matches'].extend(local_matches)
        print(m.name, 'lines', line_no, 'matches', len(local_matches))

(OUT/'inspect.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
with open(OUT/'matches.txt','w',encoding='utf-8') as f:
    for x in report['matches']:
        f.write(f"{x['member']}:{x['line']} {x['text']}\n")
print('total matches', len(report['matches']))
print(json.dumps({'members':report['members'],'samples':report['samples'],'first_matches':report['matches'][:20]},ensure_ascii=False,indent=2))
