#!/usr/bin/env python3
"""Run the actual frozen worker against the exact reviewed public Git snapshot."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.deployment_manifest import build_manifest

ROOT=Path(__file__).resolve().parents[1]

def snapshot(commit):
    manifest,commit=build_manifest(ROOT,commit)
    files={name:base64.b64encode(subprocess.check_output(['git','show',commit+':'+name],cwd=ROOT)).decode() for name in manifest}
    return {'commit':commit,'files':files}

def verify(worker,commit,output):
    with tempfile.TemporaryDirectory(prefix='deployment-pair-') as temporary:
        fixture=Path(temporary)/'public-source.json';fixture.write_text(json.dumps(snapshot(commit)),encoding='utf-8')
        result=subprocess.run([str(Path(worker).resolve()),'--preflight-check',str(fixture)],capture_output=True,text=True,timeout=60)
        if result.returncode:raise ValueError('Packaged manifest pairing failed: '+result.stderr[-2000:])
        report=json.loads(result.stdout);assert report['deployment_commit']==commit and all(v['passed'] for v in report['cases'])
    Path(output).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Actual frozen worker: exact deployment manifest pairing and six read-only preflight cases passed')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('worker');parser.add_argument('deployment_commit');parser.add_argument('output')
    args=parser.parse_args();verify(args.worker,args.deployment_commit,args.output)
