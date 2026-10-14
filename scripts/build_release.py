#!/usr/bin/env python3
"""Build reviewed allowlist artifacts locally; never publishes a Release."""
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
              'installer/__init__.py','installer/core.py','installer/github_api.py','installer/cli.py')


def build(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        staging=Path(directory)
        for name in BUNDLE_FILES:
            target=staging/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
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
    print(json.dumps(build(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'runtime/release-build'),sort_keys=True))
