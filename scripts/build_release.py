#!/usr/bin/env python3
"""Build reviewed allowlist artifacts locally; never publishes a Release."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipapp
import zipfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from installer.core import TRUSTED_FILES, expected_manifest

ROOT=Path(__file__).resolve().parents[1]
BUNDLE_FILES=('acceptance.py','monitor.py','github_state.py','feishu_app.py','bootstrap_state.py','project.json',
              'installer/registration.py','installer/__init__.py','installer/core.py','installer/github_api.py','installer/cli.py')


def build(output,public_app=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        staging=Path(directory)
        for name in BUNDLE_FILES:
            target=staging/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
        if public_app is not None:
            from installer.registration import public_config
            value=public_config(public_app['client_id'],public_app['app_slug'])
            (staging/'installer/public_app.json').write_text(json.dumps(value))
        (staging/'installer/trusted_files.json').write_text(json.dumps(expected_manifest(ROOT),sort_keys=True))
        app=output/'codex-reset-install.pyz'
        zipapp.create_archive(staging,app,interpreter='/usr/bin/env python3',main='installer.cli:main',compressed=True)
    skill=output/'codex-reset-install-skill.zip'
    with zipfile.ZipFile(skill,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(ROOT/'skills/codex-reset-install/SKILL.md','codex-reset-install/SKILL.md')
        archive.write(ROOT/'skills/codex-reset-install/scripts/launch.py','codex-reset-install/scripts/launch.py')
        archive.write(app,'codex-reset-install/assets/codex-reset-install.pyz')
    sums={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (app,skill)}
    (output/'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name,value in sums.items()))
    return sums


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',nargs='?',type=Path,default=ROOT/'runtime/release-build')
    parser.add_argument('--client-id');parser.add_argument('--app-slug')
    args=parser.parse_args()
    if bool(args.client_id)!=bool(args.app_slug):parser.error('public client-id and app-slug are required together')
    public={'client_id':args.client_id,'app_slug':args.app_slug} if args.client_id else None
    print(json.dumps(build(args.output,public),sort_keys=True))
