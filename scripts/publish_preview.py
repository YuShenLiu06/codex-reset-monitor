#!/usr/bin/env python3
"""Publish only a new preview release after checking the build identity/hash."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.verify_macos_release import validate_public
from scripts.release_assets import assemble, digest, MAC_ZIP, WINDOWS_EXE

def main(mac_directory,windows_directory):
    tag=os.environ['RELEASE_TAG'];sha=os.environ['GITHUB_SHA'];repository=os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'v\d+\.\d+\.\d+-preview\.\d+',tag) or not re.fullmatch(r'[0-9a-f]{40}',sha):
        raise SystemExit('Invalid preview identity')
    if repository!='YuShenLiu06/codex-reset-monitor':raise SystemExit('Unexpected publishing repository')
    public=validate_public(json.loads(Path('macos/public-app.json').read_text()))
    run_url=f'https://github.com/{repository}/actions/runs/{os.environ["GITHUB_RUN_ID"]}'
    assets=assemble(mac_directory,windows_directory,Path('runtime/publish-assets'),sha,public,
                    digest('assets/logo-v1.png'),run_url)
    # Refuse overwrites, including tags pointing to another source revision.
    releases=json.loads(subprocess.check_output(['gh','api','--paginate','--slurp',f'repos/{repository}/releases'],text=True))
    if any(release['tag_name']==tag for page in releases for release in page):raise SystemExit('Release already exists; use a new preview version')
    ref=subprocess.run(['gh','api',f'repos/{repository}/git/ref/tags/{tag}'],capture_output=True,text=True)
    if ref.returncode==0:
        # Workflow tags must be lightweight or annotated tags resolved by git.
        target=subprocess.check_output(['git','ls-remote','origin',f'refs/tags/{tag}',f'refs/tags/{tag}^{{}}'],text=True)
        lines=target.strip().splitlines()
        resolved=next((line.split()[0] for line in lines if line.endswith('^{}')),lines[0].split()[0])
        if resolved!=sha:raise SystemExit('Tag points to another commit')
    elif '404' not in ref.stderr:raise SystemExit('Cannot verify release tag')
    # Each version supplies real user-facing changes; never recycle old claims.
    notes=Path('docs/release-notes',tag+'.md').read_text().strip()
    if not notes or len(notes)>5000 or '{{DOWNLOADS}}' not in notes:
        raise SystemExit('Write concise version-specific notes with a DOWNLOADS placeholder')
    download=f'https://github.com/{repository}/releases/download/{tag}/'
    notes=notes.replace('{{DOWNLOADS}}',f'- [Mac Apple 芯片（ZIP）]({download}{MAC_ZIP})\n- [Windows 64 位（单 EXE）]({download}{WINDOWS_EXE})')
    comparison=json.loads((Path(windows_directory)/'WINDOWS-BUILD-CHECKS.json').read_text())['comparison']
    current=comparison['single_exe'];previous=comparison['baseline_zip']
    repeated=' / '.join(f"{value:.2f}" for value in current['repeat_interactive_seconds'])
    notes+=f'''

<details>
<summary>构建与校验详情（维护者）</summary>

两平台都内置 Python 与 GitHub CLI，Windows 另内置 .NET。Mac 只有 ad-hoc 签名，没有 Developer ID 签名或 Apple 公证；Windows 包尚未签名。

两平台复用同一份[公开 App 配置](https://github.com/{repository}/blob/{sha}/macos/public-app.json)，不含凭据。公开 API 已核对标识及五项最小权限；跨账号完整安装、Device Flow 设置仍待实际验收。程序保留实际权限检查，不自动扩大安装范围。

[构建和解包检查](https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}) · [对应源码](https://github.com/{repository}/tree/{sha})

本次 Windows 标准 runner 实测（不代表用户电脑速度）：单 EXE {current['bytes']/1_000_000:.2f} MB，旧 ZIP {previous['bytes']/1_000_000:.2f} MB。首次准备窗口 {current['first_visible_seconds']:.2f} 秒、首次可交互设置窗口 {current['first_interactive_seconds']:.2f} 秒，再次 {repeated} 秒。旧 ZIP 解压加首次可交互共 {previous['first_unzip_plus_launch_seconds']:.2f} 秒；旧版单独启动数据与环境见报告。均不含下载，首启为空应用缓存，未清空系统磁盘缓存。

下方 SHA256SUMS 包含 Mac ZIP 与 Windows EXE；RELEASE-MANIFEST.json 记录两平台共同源码与资产哈希。BUILD-CHECKS.json / WINDOWS-BUILD-CHECKS.json 保留各平台实际运行证据；WINDOWS-UI-EVIDENCE.zip 保存真实窗口的离线截图。Windows 的完整、取消和恢复使用离线模拟服务，不代替真实账号或通知验收。

</details>
'''
    with tempfile.TemporaryDirectory() as temporary:
        body=Path(temporary)/'notes.md';body.write_text(notes)
        subprocess.run(['gh','release','create',tag,'--repo',repository,'--target',sha,
                        '--title',tag+' · Mac / Windows 测试版','--notes-file',str(body),
                        '--prerelease','--draft','--latest=false',
                        *map(str,assets)],check=True)
    subprocess.run(['gh','release','edit',tag,'--repo',repository,'--draft=false','--prerelease','--latest=false'],check=True)


if __name__=='__main__':main(sys.argv[1],sys.argv[2])
