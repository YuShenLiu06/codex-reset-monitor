#!/usr/bin/env python3
"""User-run deployment helper. Never uses argv or a file for secret values."""
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from github_state import BRANCH, REPOSITORY
from monitor import Config, MonitorError, StateStore, empty_state, validate_sender_config


def gh(arguments, *, body=None, allow_missing=False):
    command = ['gh'] + arguments
    if body is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=None if body is None else json.dumps(body).encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
    if result.returncode:
        if allow_missing and b'HTTP 404' in result.stderr:
            return None
        raise RuntimeError('GitHub operation failed; verify existing login and repository permissions')
    return result.stdout


def api(path, *, body=None, missing=False):
    raw = gh(['api', '-X', 'GET' if body is None else 'POST',
              f'repos/{REPOSITORY}/{path}'], body=body, allow_missing=missing)
    return None if raw is None else json.loads(raw)


def initialize_state():
    existing = api(f'git/ref/heads/{BRANCH}', missing=True)
    if existing is not None:
        print('状态分支已存在，保留原状态。')
        return
    # Explicit initialization only; monitoring itself never creates missing state.
    marker = api('actions/variables/MONITOR_INITIALIZED', missing=True)
    if marker is not None:
        raise RuntimeError('Previous initialization recorded but state branch missing; restore history manually')
    tree = api('git/trees', body={'tree':[{
        'path':'state.json', 'mode':'100644', 'type':'blob',
        'content':json.dumps(empty_state(), sort_keys=True, indent=2)+'\n'}]})
    commit = api('git/commits', body={'message':'Initialize non-secret delivery ledger', 'tree':tree['sha'], 'parents':[]})
    api('git/refs', body={'ref':f'refs/heads/{BRANCH}', 'sha':commit['sha']})
    api('actions/variables', body={'name':'MONITOR_INITIALIZED','value':'true'})
    print('已初始化公开去重状态；未开启发送。')


def configure_secrets():
    import os
    url = getpass.getpass('飞书 Webhook（隐藏输入，不保存到本地）：')
    secret = getpass.getpass('飞书签名密钥（隐藏输入）：')
    # Validate before changing either secret; preserve process environment after check.
    original = {k:os.environ.get(k) for k in ('FEISHU_WEBHOOK_URL','FEISHU_SIGNING_SECRET')}
    try:
        os.environ.update(FEISHU_WEBHOOK_URL=url, FEISHU_SIGNING_SECRET=secret)
        validate_sender_config(Config(sending_enabled=True))
    finally:
        for key, value in original.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key]=value
    for name, value in (('FEISHU_WEBHOOK_URL',url), ('FEISHU_SIGNING_SECRET',secret)):
        result = subprocess.run(['gh','secret','set',name,'--repo',REPOSITORY],
                                input=value.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=60, check=False)
        if result.returncode:
            raise RuntimeError('Secret upload failed; monitoring remains disabled; configure both again')
    print('已通过 stdin 写入 GitHub Secrets；未发送飞书消息。')


def enable():
    existing = api('contents/state.json?ref='+BRANCH)
    import base64, tempfile
    raw = base64.b64decode(existing['content'])
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'state.json'; path.write_bytes(raw)
        state=StateStore(path).read()
    if any(v['status'] in ('pending','unknown') for v in state['alerts'].values()):
        raise RuntimeError('Unresolved delivery blocks enable; reconcile manually first')
    names={item['name'] for item in api('actions/secrets')['secrets']}
    if not {'FEISHU_WEBHOOK_URL','FEISHU_SIGNING_SECRET'} <= names:
        raise RuntimeError('Both repository secrets are required before enable')
    gh(['variable','set','MONITOR_ENABLED','--repo',REPOSITORY,'--body','true'])
    print('已开启每小时调度发送门控。是否实际运行/送达需查看 Actions 和飞书群；阈值未超时不会发消息。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare',action='store_true',help='First setup: initialize non-secret state then run safe preview')
    mode.add_argument('--configure',action='store_true',help='Hidden input for user-created bot secrets; does not enable sends')
    mode.add_argument('--enable',action='store_true',help='Explicitly enable scheduled notifications after preview review')
    mode.add_argument('--disable',action='store_true',help='Disable sending; retain all state')
    args=parser.parse_args()
    try:
        metadata=json.loads(gh(['api',f'repos/{REPOSITORY}']))
        if metadata.get('full_name') != REPOSITORY or metadata.get('default_branch') != 'main':
            raise RuntimeError('Unexpected target repository or default branch')
        if args.prepare:
            initialize_state()
            gh(['workflow','run','monitor.yml','--repo',REPOSITORY,'--ref','main','-f','dry_run=true'])
            print(f'已请求安全预览，结果请查看 https://github.com/{REPOSITORY}/actions 。')
        elif args.configure:
            if not sys.stdin.isatty():
                raise RuntimeError('Run configuration in an interactive terminal; hidden input is required')
            current=api('actions/variables/MONITOR_ENABLED',missing=True)
            if current and current.get('value') == 'true':
                raise RuntimeError('Disable monitoring before replacing credentials')
            configure_secrets()
        elif args.enable:
            enable()
        else:
            gh(['variable','set','MONITOR_ENABLED','--repo',REPOSITORY,'--body','false'])
            print('已关闭发送门控；已在运行的任务仍需在 Actions 页面停止或等待结束。')
    except (RuntimeError, MonitorError) as exc:
        # These are our own safe messages, never raw gh stderr or secret values.
        print(f'部署停止：{exc}', file=sys.stderr)
        return 2
    except (OSError, subprocess.TimeoutExpired, KeyError, ValueError):
        print('部署步骤失败，未确认的操作请在 GitHub 设置检查；不要删除去重状态或直接重发。',file=sys.stderr)
        return 2
    return 0


if __name__=='__main__':
    sys.exit(main())
