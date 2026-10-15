#!/usr/bin/env python3
"""Fetch reviewed official build inputs and verify their fixed hashes."""
import hashlib
from pathlib import Path
import sys
import urllib.request
import zipfile

GH_URL='https://github.com/cli/cli/releases/download/v2.95.0/gh_2.95.0_macOS_arm64.zip'
GH_SHA='3677f9c27965825f9c7d50395473c134edaea4b484373ef6b25de653570a0489'
LICENSE_URL='https://raw.githubusercontent.com/python/cpython/v3.13.16/LICENSE'
LICENSE_SHA='78b12c3a81360b357002334f0e70ea0e92eebf7a9b358805c03c48484945f3bb'


def fetch(url,destination,expected):
    with urllib.request.urlopen(url,timeout=60) as response:
        data=response.read(64*1024*1024+1)
    if len(data)>64*1024*1024 or hashlib.sha256(data).hexdigest()!=expected:
        raise SystemExit('Official dependency checksum mismatch')
    destination.write_bytes(data)


def main(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    archive=directory/'gh.zip';fetch(GH_URL,archive,GH_SHA)
    # Copy two explicit files, never blindly extract upstream paths or configs.
    gh=directory/'gh/bin/gh';gh.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        gh.write_bytes(source.read('gh_2.95.0_macOS_arm64/bin/gh'))
        (directory/'gh/LICENSE').write_bytes(source.read('gh_2.95.0_macOS_arm64/LICENSE'))
    gh.chmod(0o755)
    fetch(LICENSE_URL,directory/'Python-LICENSE.txt',LICENSE_SHA)


if __name__=='__main__':main(sys.argv[1])
