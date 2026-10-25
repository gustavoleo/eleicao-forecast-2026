"""Copy a STOPPED collector's DuckDB plus optional WAL, then verify the copy.

The caller must stop the dashboard container first. Source volume is mounted
read-only. Recovery/checkpoint occurs only in the new backup directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import duckdb

parser=argparse.ArgumentParser()
parser.add_argument('source',type=Path)
parser.add_argument('destination',type=Path)
args=parser.parse_args()
args.destination.mkdir(parents=True,exist_ok=False)
files=[args.source/'eleicoes.duckdb']
if files[0].with_suffix('.duckdb.wal').exists():
    files.append(files[0].with_suffix('.duckdb.wal'))
copied={}
for source in files:
    destination=args.destination/source.name
    # Windows bind mounts may reject Linux timestamp/ownership metadata.
    # The database bytes and hashes are the backup invariant.
    shutil.copyfile(source,destination)
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    assert source_hash==hashlib.sha256(destination.read_bytes()).hexdigest()
    copied[source.name]=dict(bytes=destination.stat().st_size,sha256=source_hash)
connection=duckdb.connect(str(args.destination/'eleicoes.duckdb'))
counts={name:connection.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0]
        for (name,) in connection.execute('SHOW TABLES').fetchall()}
connection.execute('CHECKPOINT')
connection.close()
report=dict(copied_files=copied,verified_table_counts=counts,checkpointed=True,
            source_modified=False)
(args.destination/'verification.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
