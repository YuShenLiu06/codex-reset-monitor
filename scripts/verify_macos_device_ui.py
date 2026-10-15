#!/usr/bin/env python3
"""Actual Mac test ZIP → native window → offline device and repository preflight."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.verify_deployment_pair import snapshot


def verify(archive,evidence):
    evidence=Path(evidence).resolve();evidence.mkdir(parents=True,exist_ok=True)
    reports=[]
    with tempfile.TemporaryDirectory(prefix='真实产物 空格-') as temporary:
        root=Path(temporary)
        subprocess.run(['/usr/bin/ditto','-x','-k',str(Path(archive).resolve()),str(root)],check=True)
        app=root/'Codex 提醒设置.app';resources=app/'Contents/Resources'
        info=json.loads((resources/'BUILD-INFO.json').read_text())
        for mode in ('post-device','post-device-missing'):
            case=evidence/mode;case.mkdir(exist_ok=True)
            (case/'public-source.json').write_text(json.dumps(snapshot(info['deployment_manifest_commit'])))
            command=[str(app/'Contents/MacOS/CodexResetInstaller'),'--offline-device',mode,str(case),'--offline-ui-check','--smoke-start']
            with (case/'process.log').open('w') as log:
                process=subprocess.Popen(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
                try:
                    deadline=time.monotonic()+30
                    terminal='gui-passed.txt' if mode=='post-device' else 'stopped.txt'
                    while time.monotonic()<deadline and not (case/terminal).exists():
                        if process.poll() is not None:raise ValueError('Native test process exited early')
                        time.sleep(0.1)
                    assert (case/terminal).exists(),'Native offline flow did not reach expected terminal'
                    time.sleep(2) # Let the window paint, capture and worker cancel finish.
                    assert (case/'device.png').is_file() and (case/'identity.png').is_file()
                    if mode=='post-device':
                        assert (case/'after-preflight.png').is_file()
                        progress=json.loads((case/'state.local.json').read_text())
                        assert 'compatibility' in progress['completed'] and progress['pending'] is None
                        proof=json.loads((case/'offline-report.json').read_text())
                        assert proof['http_requests']==proof['oauth_requests']==proof['writes']==0
                    else:
                        text=(case/'stopped.txt').read_text()
                        assert 'PROJECT_FILES_MISSING' in text and 'manifest_pairing' in text
                        assert (case/'stopped.png').is_file()
                    reports.append({'case':mode,'passed':True,'actual_app':True,'real_account':False})
                finally:
                    if process.poll() is None:process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
            # The public source fixture is input, not screenshot evidence; avoid duplicating it in uploads.
            (case/'public-source.json').unlink()
    report={'source_commit':info['source_commit'],'deployment_manifest_commit':info['deployment_manifest_commit'],
            'cases':reports,'limits':['Offline services; real account authorization remains user acceptance']}
    (evidence/'GUI-CHECKS.json').write_text(json.dumps(report,indent=2))
    print('Actual Mac ZIP native offline device → early identity → paired preflight and missing-file diagnostic passed')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('archive');parser.add_argument('evidence')
    args=parser.parse_args();verify(args.archive,args.evidence)
