#!/usr/bin/env python3
"""Measure uncached validation latency; no generation or model calls."""
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT/'src'))
from osg_validator import OsgClient,evaluate


def main():
    excluded={v['task'] for v in json.loads((ROOT/'tools/osg-validator/excluded.json').read_text())}
    normal=sorted((ROOT/'dataset/prompts').glob('[0-9]*.txt'),key=lambda p:int(p.stem))
    erp=sorted((ROOT/'dataset/prompts').glob('erp_*.txt'))
    tasks=[p.name for p in normal if p.name not in excluded][:5]+[p.name for p in erp if p.name not in excluded][:5]
    client=OsgClient();latencies=[]
    try:
        cold=time.monotonic()
        source=(ROOT/'dataset/abap_canonical_solution/z_humaneval_000.abap').read_text()
        assert evaluate(client,'0.txt',source)[0]['unit_pass']
        cold=time.monotonic()-cold
        start=time.monotonic()
        for _ in range(3):
            for task in tasks:
                n=task.removesuffix('.txt');n=n if n.startswith('erp_') else n.zfill(3)
                source=(ROOT/'dataset/abap_canonical_solution'/f'z_humaneval_{n}.abap').read_text()
                tier,_=evaluate(client,task,source)
                assert tier['unit_pass'],(task,tier['error_message'])
                latencies.append(tier['elapsed_seconds'])
        elapsed=time.monotonic()-start
    finally:client.close()
    summary={'workers':1,'tasks':tasks,'repetitions':3,'measured_answers':len(latencies),'all_pass':True,'cold_start_answer_seconds':round(cold,4),'warm_wall_seconds':round(elapsed,4),'warm_answers_per_second':round(len(latencies)/elapsed,4),'median_answer_seconds':round(statistics.median(latencies),4),'max_answer_seconds':max(latencies),'note':'No verdict cache; immutable compiled library cache present. Each answer still creates a fresh runtime child and SQLite database.'}
    path=ROOT/'tools/osg-validator/throughput-summary.json'
    path.write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
