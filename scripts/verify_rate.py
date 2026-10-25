"""Summarize a genuine five-minute request log; fail if interval/limits lack evidence."""
from collections import Counter,deque
from datetime import datetime
from pathlib import Path
import argparse,json

parser=argparse.ArgumentParser()
parser.add_argument('path')
parser.add_argument('--output',default='evidence/rate-compliance.json')
args=parser.parse_args()
records=[json.loads(l) for l in Path(args.path).read_text(encoding='utf-8').splitlines() if l.startswith('{')]
records=sorted(records,key=lambda r:r['ts'])
window=deque(); peak=0
for r in records:
    t=datetime.fromisoformat(r['ts']).timestamp()
    while window and window[0]<=t-1:window.popleft()
    window.append(t);peak=max(peak,len(window))
span=(datetime.fromisoformat(records[-1]['ts'])-datetime.fromisoformat(records[0]['ts'])).total_seconds()
report=dict(start=records[0]['ts'],end=records[-1]['ts'],seconds=span,requests=len(records),
    peak_sliding_one_second=peak,mean_requests_per_second=len(records)/span,
    http_statuses=dict(Counter(str(r['status']) for r in records)),
    changed=sum(r.get('changed',False) for r in records),bytes_received=sum(r.get('bytes',0) for r in records),
    proves_five_minutes=span>=300,below_90_requests_per_second=peak<=90,
    scope='This collector log only; unrelated programs using the same public IP are outside this measurement.')
Path(args.output).parent.mkdir(parents=True,exist_ok=True)
Path(args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
assert report['proves_five_minutes'] and report['below_90_requests_per_second']
