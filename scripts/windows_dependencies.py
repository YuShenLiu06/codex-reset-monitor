#!/usr/bin/env python3
"""Download only hash-locked official Windows build inputs; no authentication."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def fetch(item,path):
    algorithm='sha512' if 'sha512' in item else 'sha256'
    digest=hashlib.new(algorithm);size=0
    with urllib.request.urlopen(item['url'],timeout=60) as response,path.open('wb') as output:
        while True:
            chunk=response.read(1024*1024)
            if not chunk:break
            size+=len(chunk)
            if size>512*1024*1024:raise ValueError('Dependency size limit')
            digest.update(chunk);output.write(chunk)
    if digest.hexdigest()!=item[algorithm]:
        path.unlink();raise ValueError('Official dependency checksum mismatch')


def main(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    inputs=json.loads((ROOT/'windows/dependencies.json').read_text(encoding='utf-8'))
    sdk=directory/'sdk.zip';fetch(inputs['sdk'],sdk)
    with zipfile.ZipFile(sdk) as archive:
        for info in archive.infolist():
            path=Path(info.filename)
            if path.is_absolute() or '..' in path.parts:raise ValueError('Invalid SDK archive path')
        archive.extractall(directory/'dotnet')
    gh=directory/'gh.zip';fetch(inputs['gh'],gh)
    (directory/'tools').mkdir(exist_ok=True)
    with zipfile.ZipFile(gh) as archive:
        for name,out in [('bin/gh.exe','gh.exe'),('LICENSE','GitHub-CLI-LICENSE.txt')]:
            (directory/'tools'/out).write_bytes(archive.read(name))
    fetch(inputs['python_license'],directory/'Python-LICENSE.txt')
    feed=directory/'nuget';feed.mkdir(exist_ok=True)
    for item in inputs['nuget']:
        fetch(item,feed/(item['name'].lower()+'.'+item['version']+'.nupkg'))
    print('Verified Windows x64 SDK, GitHub CLI and Python license')


if __name__=='__main__':main(sys.argv[1])
