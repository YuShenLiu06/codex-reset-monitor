"""Interactive user entry, shared by release zipapp and the Skill launcher."""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import sys

from installer.core import (SOURCE_REPOSITORY, Progress, Installer, SetupError,
    device_authorize, expected_manifest, APP_SECRET_NAMES)
from installer.github_api import GitHubAPI
from feishu_app import (AppCredentials, begin_registration, poll_registration,
                        readiness_record, tenant_token, validate_credentials)
from monitor import MonitorError


def require(prompt,word):
    if input(prompt).strip()!=word:raise SetupError('USER_CANCELLED')


def manifest():
    # Installed zipapp uses a build-time allowlist, never untrusted remote hashes.
    import pkgutil
    try:bundled=pkgutil.get_data('installer','trusted_files.json')
    except FileNotFoundError:bundled=None
    if bundled is not None:return json.loads(bundled)
    return expected_manifest(Path(__file__).resolve().parents[1])


def collect_feishu(progress,emit):
    choice=input('官方扫码新建 [默认 new] / 明确复用 reuse：').strip() or 'new'
    existing=None
    if choice=='new':
        if progress.data['app_created']:raise SetupError('REUSE_PREVIOUS_APP','previous registration started; explicitly reuse it rather than create another')
        require('将启动官方注册并预填最小发送权限，输入 CREATE：','CREATE')
        progress.data['app_created']=True;progress.save()
    elif choice=='reuse':
        existing=input('要复用的公开 App ID（不需要手填 Secret/open_id）：').strip()
        require('将对该应用扫码授权最小发送权限，输入 REUSE：','REUSE')
    else:raise SetupError('INVALID_APP_CHOICE')
    session=begin_registration(create_only=choice=='new',existing_app_id=existing)
    emit('feishu','官方验证链接（可在飞书/手机打开，勿记录公开日志）：'+session.verification_url)
    credentials=poll_registration(session)
    emit('feishu','请在飞书后台核验机器人能力、im:message:send_as_bot、已审批发布版本及本人可用范围；注册和 token 均不证明能发送。')
    require('本人身份、发送权限、生效版本和可用范围均已核验，输入 VERIFIED：','VERIFIED')
    record=readiness_record(credentials,self_confirmed=True,published_bot_confirmed=True,
        self_availability_confirmed=True,send_scope='im:message:send_as_bot')
    tenant_token(credentials)
    progress.mark('feishu')
    return dict(zip(APP_SECRET_NAMES,(credentials.app_id,credentials.app_secret,credentials.open_id,
                                     json.dumps(record,ensure_ascii=False),'app')))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retry-failed-stage',choices=('prepare','preview'),help='Explicit user-confirmed safe revalidation after fixing a failed workflow')
    parser.add_argument('--run',action='store_true',help='User-only live interactive flow; default is offline plan')
    parser.add_argument('--client-id',help='Public client_id of a real device-enabled GitHub App; no fake default')
    parser.add_argument('--app-slug',help='Public slug of that installer GitHub App')
    parser.add_argument('--target',help='OWNER/REPO; prompted if omitted')
    parser.add_argument('--progress',type=Path,default=Path('.codex-reset-setup/state.local.json'))
    parser.add_argument('--resolve-run',nargs=2,metavar=('STAGE','RUN_ID'),help='User-verified prepare/preview/test run for a previously unknown dispatch')
    parser.add_argument('--acknowledge-partial-secrets',action='store_true',help='User inspected partial upload and will reuse same app')
    args=parser.parse_args(argv)
    emit=lambda stage,message:print(f'[{stage}] {message}',flush=True)
    emit('plan','官方网页 fork → 仅目标仓库安装 GitHub App → device 授权 → 核验兼容性 → 飞书本人授权 → 最终配置确认 → prepare → 安全 dry-run。')
    emit('plan','安装器权限：Contents:read、Metadata:read、Secrets/Actions/Variables:write；不自动 fork、不覆盖源码、不申请 Administration/Contents:write。')
    emit('plan','默认禁用；用户可在安全预览后确认一次测试通知，确认收到再明确启用。真实安装器 App 尚需维护者注册。')
    if not args.run:return 0
    api=None;values=None;token=None
    try:
        if sys.platform not in ('darwin','linux'):raise SetupError('PLATFORM_UNSUPPORTED','live installer currently supports macOS/Linux only')
        if not sys.stdin.isatty() or not sys.stdout.isatty():raise SetupError('INTERACTIVE_USER_TERMINAL_REQUIRED')
        if not args.client_id or not args.app_slug or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}',args.app_slug):
            raise SetupError('REAL_INSTALLER_APP_REQUIRED','explicit public client_id and app slug needed')
        if not shutil.which('gh'):raise SetupError('GH_CLI_REQUIRED','install the official GitHub CLI first; no authorization started')
        target=args.target or input('目标仓库 OWNER/REPO：').strip()
        progress=Progress(args.progress,target)
        policy=input('目标仓库策略：复用已有 [默认 reuse] / 官方页面新建 fork：').strip() or 'reuse'
        if policy not in ('reuse','fork'):raise SetupError('INVALID_REPOSITORY_CHOICE')
        if policy=='fork':
            emit('target','请在 https://github.com/'+SOURCE_REPOSITORY+'/fork 点击官方 Fork，并将名称设为目标 '+progress.target+'；程序不会自动创建或覆盖仓库。')
        else:
            emit('target','复用目标：'+progress.target+'；稍后严格核验项目来源和文件，若不兼容可退出后改选另一个新 fork，不能覆盖同名仓库。')
        emit('github','在 https://github.com/apps/'+args.app_slug+'/installations/new 仅选择 '+progress.target+'，不要选所有仓库。')
        require('已完成官方仓库选择与 App 安装，输入完整目标仓库继续：',progress.target)
        progress.mark('target')
        token=device_authorize(args.client_id,emit)
        api=GitHubAPI(token);progress.mark('github')
        installer=Installer(api,progress,manifest(),emit)
        installer.check(args.app_slug)
        require('确认当前授权 GitHub 用户，输入 '+installer.github_login+'：',installer.github_login)
        if args.retry_failed_stage:
            require('已修复失败原因，只重新验证 prepare/dry-run（不发消息），输入 RETEST：','RETEST')
            installer.retry_failed_stage(args.retry_failed_stage)
        if args.resolve_run:
            stage,run=args.resolve_run
            if stage not in ('prepare','preview','test') or not run.isdigit() or int(run)<=0 or progress.data['pending']!=stage:
                raise SetupError('INVALID_RUN_RESOLUTION')
            require('已在官方 Actions 页面核验此 run 对应未知 dispatch，输入 RESOLVE：','RESOLVE')
            progress.data['runs'][stage]=int(run);progress.data['pending']=None;progress.save()
        if progress.data['pending']=='secrets':
            if not args.acknowledge_partial_secrets:raise SetupError('PARTIAL_SECRETS_REQUIRE_REVIEW')
            require('已检查 Secret 名称且将复用同一应用重新配置，输入 REUSE：','REUSE')
            progress.data['pending']=None;progress.save()
        if 'secrets' not in progress.data['completed'] or args.acknowledge_partial_secrets:
            values=collect_feishu(progress,emit)
            emit('secrets','将写入 '+progress.target+'：'+', '.join(APP_SECRET_NAMES)+'；只向授权本人通知。')
            require('最终敏感配置确认：输入完整目标仓库名：',progress.target)
            installer.check(args.app_slug)
            installer.configure(values)
        installer.validate()
        if input('是否明确授权一次安装测试通知？输入 SEND_TEST，否则保持关闭退出：').strip()!='SEND_TEST':
            emit('complete','保持关闭；未测试通知、未开启监控。');return 0
        installer.test_notification(user_authorized=True)
        request_id=progress.data['test_request_id']
        emit('test','请在飞书核对「安装测试」消息及请求标识：'+request_id)
        require('确认本人收到对应标识，输入 RECEIVED '+request_id+'：','RECEIVED '+request_id)
        require('明确开启此仓库每小时预测提醒，输入 ENABLE '+progress.target+'：','ENABLE '+progress.target)
        installer.enable_after_received(received_confirmed=True,enable_authorized=True)
        return 0
    except (SetupError,MonitorError) as exc:
        # Only stable local errors; never print auth response, request or credential values.
        emit('stopped',str(exc));return 2
    except KeyboardInterrupt:
        emit('stopped','用户取消；进度保留，已开始注册的应用请复用。');return 130
    except Exception:
        emit('stopped','UNEXPECTED_ERROR：敏感详情省略；检查进度和官方页面，勿盲目重试。');return 2
    finally:
        if values is not None:values.clear()
        if api is not None:api.close()
        token=None
        emit('cleanup','已释放本地短期凭据引用，不保存 refresh token。请在 https://github.com/settings/installations 撤销安装工具安装，并在 https://github.com/settings/apps/authorizations 撤销用户授权。')


if __name__=='__main__':raise SystemExit(main())
