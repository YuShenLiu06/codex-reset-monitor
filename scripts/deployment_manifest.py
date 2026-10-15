"""Keep installer build identity separate from explicitly reviewed deployment code."""
import hashlib
from pathlib import Path
import re
import subprocess
from installer.core import TRUSTED_FILES, expected_manifest


def build_manifest(root, deployment_commit=None):
    root=Path(root)
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=root)
    source=git('rev-parse','HEAD').decode().strip()
    if deployment_commit is None:
        return expected_manifest(root),source
    # Only a full immutable commit is accepted, never a moving remote reference.
    if not re.fullmatch(r'[0-9a-f]{40}',deployment_commit):raise ValueError('Expected reviewed full deployment commit')
    if git('rev-parse',deployment_commit+'^{commit}').decode().strip()!=deployment_commit:
        raise ValueError('Deployment identity mismatch')
    entries={}
    for record in git('ls-tree','-r','-z',deployment_commit).split(b'\0'):
        if not record:continue
        header,name=record.split(b'\t',1);mode,kind,oid=header.decode().split();name=name.decode('utf-8')
        path=Path(name)
        if name in TRUSTED_FILES or (not set(path.parts)&{'runtime','.git','__pycache__','.codex-reset-setup'} and
            (name.endswith('.py') or name.startswith('.github/workflows/') and name.endswith(('.yml','.yaml')))):
            if mode not in ('100644','100755') or kind!='blob':raise ValueError('Unsafe deployment file type')
            entries[name]=hashlib.sha256(git('cat-file','blob',oid)).hexdigest()
    if not set(TRUSTED_FILES)<=set(entries):raise ValueError('Incomplete deployment manifest')
    return dict(sorted(entries.items())),deployment_commit
