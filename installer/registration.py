"""Public-only GitHub App configuration and user-operated registration guide."""
import json
import os
from installer.resources import read_bundled
from pathlib import Path
import re
import tempfile
import urllib.parse
import webbrowser
from installer.github_api import SetupError


def public_config(client_id, app):
    if not isinstance(client_id,str) or not re.fullmatch(r'Iv[0-9A-Za-z.]{8,126}',client_id):
        raise SetupError('INVALID_PUBLIC_CLIENT_ID','use the public GitHub App Client ID, not App ID or a secret')
    if not isinstance(app,str):raise SetupError('INVALID_APP_SLUG')
    if app.startswith('https://'):
        parsed=urllib.parse.urlsplit(app)
        if parsed.netloc!='github.com' or parsed.query or parsed.fragment or not re.fullmatch(r'/(?:apps|settings/apps)/[a-z0-9][a-z0-9-]{0,99}/?',parsed.path):
            raise SetupError('INVALID_APP_URL')
        app=parsed.path.rstrip('/').split('/')[-1]
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}',app):raise SetupError('INVALID_APP_SLUG')
    return {'version':1,'client_id':client_id,'app_slug':app}


def registration_url(*,public=False):
    # Only parameters explicitly documented by GitHub's URL registration guide.
    query={'name':'Codex Reset Installer','description':'Install Codex reset monitor on one selected repository',
           'url':'https://github.com/YuShenLiu06/codex-reset-monitor','public':'true' if public else 'false',
           'webhook_active':'false',
           'contents':'read','actions':'write','secrets':'write','variables':'write'}
    return 'https://github.com/settings/apps/new?'+urllib.parse.urlencode(query)


def load(path):
    if not path.exists():return None
    try:
        raw=json.loads(path.read_text())
        if set(raw)!={'version','client_id','app_slug'} or raw['version']!=1:raise ValueError()
        return public_config(raw['client_id'],raw['app_slug'])
    except Exception:raise SetupError('INVALID_PUBLIC_CONFIG','inspect the public-only installer config; it will not be overwritten') from None


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.public-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as handle:
            json.dump(value,handle);handle.flush();os.fsync(handle.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def configure(path,emit,client_id=None,app=None,ui=None):
    ask=ui.ask if ui is not None else input
    if bool(client_id)!=bool(app):raise SetupError('PUBLIC_CONFIG_PAIR_REQUIRED')
    if client_id:return public_config(client_id,app)
    bundled=read_bundled('public_app.json')
    if bundled is not None:
        try:
            raw=json.loads(bundled)
            if set(raw)!={'version','client_id','app_slug'} or raw['version']!=1:raise ValueError()
            result=public_config(raw['client_id'],raw['app_slug'])
        except Exception:raise SetupError('INVALID_BUNDLED_PUBLIC_CONFIG') from None
        emit('github','使用发行包内公开安装器 App：'+result['app_slug'])
        return result
    existing=load(path)
    if existing:
        emit('github','继续使用已保存的公开 App：'+existing['app_slug'])
        return existing
    choice=ask('首次配置 GitHub App：新建 [回车] / 已有 existing / 取消 cancel：').strip() or 'new'
    if choice=='cancel':raise SetupError('USER_CANCELLED')
    if choice=='new':
        purpose=ask('用途：仅本人 [回车] / 供其他账号安装 public / 取消 cancel：').strip() or 'private'
        if purpose=='cancel':raise SetupError('USER_CANCELLED')
        if purpose not in ('private','public'):raise SetupError('INVALID_APP_PURPOSE')
        emit('github','将打开官方预填注册页，由你核对并创建。名称如重名请改为唯一名称；权限仅 Contents read、Actions/Secrets/Variables write，Metadata read 自动必需。')
        emit('github','页面还需勾选 Enable Device Flow，并保留 Expire user authorization tokens；Webhook 保持关闭，不生成私钥或 client secret。请核对用途对应的 Only on this account / Any account。')
        url=registration_url(public=purpose=='public')
        emit('github',url)
        try:opened=webbrowser.open(url,new=2)
        except Exception:opened=False
        if not opened:emit('github','浏览器未自动打开，请打开上面的官方链接。')
        emit('github','创建后在 App 设置复制公开 Client ID，在公开 App 页面取得 URL/slug；输入 cancel 可退出，下次选 existing 继续。')
    elif choice!='existing':raise SetupError('INVALID_APP_CHOICE')
    if ui is not None:
        result=ui.public_fields()
    else:
        client=ask('公开 Client ID（不是 App ID/Secret；cancel 取消）：').strip()
        if client=='cancel':raise SetupError('USER_CANCELLED')
        app=ask('App URL https://github.com/apps/SLUG 或 slug：').strip()
        if app=='cancel':raise SetupError('USER_CANCELLED')
        result=public_config(client,app)
    save(path,result)
    emit('github','公开配置已保存；下一步核验实际安装与权限。')
    return result
