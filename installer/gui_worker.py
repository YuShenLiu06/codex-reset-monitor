"""Private stdin/stdout UI protocol; no local web server, no credential logs."""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from installer.github_api import SetupError
from monitor import MonitorError

TITLES={'plan':'设置 Codex 提醒','github':'连接 GitHub','target':'选择存放位置',
        'compatibility':'检查设置','feishu':'连接飞书','secrets':'保存设置',
        'prepare':'准备提醒','preview':'检查提醒','complete':'检查完成',
        'test':'发一条测试','enabled':'提醒已开启','stopped':'暂时停在这里','cleanup':'收尾'}

class GUI:
    def __init__(self,reader=None,writer=None):
        self.reader=reader or sys.stdin;self.writer=writer or sys.stdout;self.sequence=0
    def send(self,data):
        self.writer.write(json.dumps(data,ensure_ascii=False)+'\n');self.writer.flush()
    def request(self,title,text,choices=None,placeholder='',expected=None,error='',back=False,initial=''):
        self.sequence+=1
        self.send({'type':'prompt','id':self.sequence,'title':title,'text':text,
                   'choices':choices or [],'placeholder':placeholder,'error':error,'back':back,'initial':initial})
        line=self.reader.readline(8193)
        if not line or len(line)>8192:raise SetupError('USER_CANCELLED')
        try:value=json.loads(line)
        except ValueError:raise SetupError('INVALID_UI_RESPONSE') from None
        if not isinstance(value,dict) or value.get('id')!=self.sequence or not isinstance(value.get('value'),str):raise SetupError('INVALID_UI_RESPONSE')
        answer=value['value']
        if choices and answer not in [v['value'] for v in choices]:raise SetupError('INVALID_UI_RESPONSE')
        if expected is not None and answer!=expected:raise SetupError('USER_CANCELLED')
        return answer
    def field(self,title,text,placeholder,validator,explanation,back=False,initial=''):
        error=''
        while True:
            answer=self.request(title,text,placeholder=placeholder,error=error,back=back,initial=initial).strip()
            if answer=='cancel':raise SetupError('USER_CANCELLED')
            if back and answer=='__back__':return None
            try:return validator(answer)
            except (SetupError,MonitorError,ValueError):error=explanation

    def public_fields(self):
        from installer.registration import public_config
        client=None
        while True:
            client=self.field('复制公开编号','在 GitHub 应用设置复制 Client ID，以 Iv 开头。不要填写 Client Secret、App ID 或私钥。',
                'Iv…',lambda value:public_config(value,'validation-only')['client_id'],
                '这里需要以 Iv 开头的公开 Client ID。请回到应用设置核对后重新填写。',initial=client or '')
            while True:
                app=self.field('复制应用链接','复制 GitHub 应用公开页或个人应用设置页地址，也可以只填应用短名称。不要填写密钥。',
                    'https://github.com/apps/应用短名称',lambda value:(public_config(client,value),value)[1],
                    '请填 https://github.com/apps/my-installer、https://github.com/settings/apps/my-installer，或 my-installer。不能填写仓库链接、其他路径或带 ?、# 的地址。',back=True)
                if app is None:break
                if app is not None:
                    result=public_config(client,app)
                    if app.startswith('https://github.com/settings/apps/'):
                        choice=self.request('已识别应用设置页','将使用应用公开地址：\nhttps://github.com/apps/'+result['app_slug']+'\n这里只转换了地址；应用是否存在、安装和权限仍需后续核验。',
                            [{'label':'使用此应用，继续','value':'continue'},{'label':'返回修改','value':'back'}])
                        if choice=='back':continue
                    return result

    def ask(self,prompt):
        def choices(*items):return [{'label':label,'value':value} for label,value in items]
        if prompt.startswith('开始'):
            return 'Y'  # The native window starts this worker only after its Start button.
        if prompt.startswith('首次配置'):
            return self.request('准备连接 GitHub','这个测试包还没有配置公开连接应用。只有维护者首次设置需要创建；普通用户请等待完整发行包。',choices(('第一次使用','new'),('我已经创建过','existing'),('取消','cancel')))
        if prompt.startswith('用途'):
            return self.request('谁会使用这个安装器？','一般选“只有我”。选择供别人使用才会创建公开应用。',choices(('只有我','private'),('也供别人使用','public'),('取消','cancel')))
        if prompt.startswith('公开 Client'):
            return self.request('复制公开编号','在 GitHub 页面核对最小权限，勾选 Enable Device Flow，保留 Expire user authorization tokens，Webhook 关闭。不生成私钥或密码。创建后复制 Client ID。',placeholder='Iv…')
        if prompt.startswith('App URL'):
            return self.request('复制应用链接','打开刚创建应用的公开页面，复制地址。',placeholder='https://github.com/apps/你的应用名')
        if prompt.startswith('目标仓库 OWNER'):
            from installer.core import validate_target
            return self.field('设置放在哪里？','填写 GitHub 用户名/仓库名。', '用户名/codex-reset-monitor',validate_target,'请填用户名/仓库名，例如 my-name/codex-reset-monitor；不要粘贴整个网页地址。')
        if prompt.startswith('目标仓库策略'):
            return self.request('准备存放设置的地方','如果还没有这个项目，先复制一份到自己的账号。',choices(('已经有了','reuse'),('还没有，复制一份','fork')))
        if prompt.startswith('官方扫码新建'):
            return self.request('连接飞书','打开飞书官方页面，创建或连接你自己的提醒机器人。',choices(('创建一个提醒机器人','new'),('连接已有机器人','reuse')))
        if prompt.startswith('提醒机器人名称'):
            from feishu_app import DEFAULT_APP_NAME,validate_app_name
            return self.field('给提醒机器人起个名字','这是你在飞书里看到的名称。可以改成自己喜欢的名字，稍后会填入飞书官方创建页；也可以在那里修改。',
                '例如：我的 Codex 小助手',validate_app_name,'请填 1–20 个字符的名称，不要换行。',initial=DEFAULT_APP_NAME)
        if prompt.startswith('要复用的公开'):
            return self.field('填写机器人编号','从飞书应用设置复制 App ID，不需要填写密码或密钥。','cli_…',self.app_id,'请填写 cli_ 开头的飞书 App ID；不要填写密钥或网页地址。')
        if prompt.startswith('是否明确授权一次'):
            return self.request('发一条测试','现在向你发送一条写着“安装测试”的消息。它不是重置预警。',choices(('发一条测试','SEND_TEST'),('先不开启','')))
        raise SetupError('UNSUPPORTED_UI_STEP')
    def confirm(self,prompt,word):
        title='继续设置';text='请核对后继续。';label='确认，继续'
        if word in ('CREATE','REUSE'):
            title='连接飞书';text='下一步会打开飞书官方授权页面，只有你本人可以确认。';label='打开飞书授权'
        elif word=='VERIFIED':
            title='机器人准备好了吗？';text='请在飞书应用后台确认：机器人已启用；发送消息权限已生效；应用已发布且你在可使用范围内。';label='已核对，继续'
        elif word.startswith('RECEIVED '):
            title='你收到测试了吗？';text='请在飞书找到“安装测试”，核对这串标识：\n'+word.split(' ',1)[1];label='我收到了这条测试'
        elif word.startswith('ENABLE '):
            title='开启提醒';text='开启后每小时检查一次，符合条件才提醒。\n存放位置：'+word.split(' ',1)[1];label='开启提醒'
        elif '最终敏感配置' in prompt:
            title='保存提醒设置';text='保存到 GitHub 仓库：\n'+word+'\n保存机器人编号、密钥、本人收件编号、发送资格记录和机器人模式，共 5 项加密设置。下一步先检查，不会直接开启。';label='保存并检查'
        elif '当前授权 GitHub 用户' in prompt:
            title='这是你的账号吗？';text=word;label='是我的账号'
        elif '官方 Fork' in prompt:
            title='复制一份到你的账号';text='点击官方页面完成 Fork，名称应对应：\n'+word;label='已经复制，继续'
        elif 'App 安装' in prompt:
            title='完成 GitHub 连接';text='请在官方页面只选择这个仓库：\n'+word+'\n完成安装后再继续。';label='已经完成，继续'
        elif word=='RETEST':
            title='重新检查';text='仅重新运行准备或检查步骤，不发送消息。';label='确认重新检查'
        elif word=='RESOLVE':
            title='关联上次的任务';text='仅等待你核对的任务，不重新提交。';label='确认，继续等待'
        return self.request(title,text,[{'label':label,'value':word},{'label':'取消','value':'cancel'}],expected=word)
    def installation_issue(self,error,slug):
        from installer.registration import public_config
        slug=public_config('Iv1.ValidationID',slug)['app_slug']
        names={'actions':'Actions（运行检查）','secrets':'Secrets（保存加密设置）',
               'actions_variables':'Variables（开关设置）','contents':'Contents（读取代码）',
               'metadata':'Metadata（仓库基本信息）','administration':'Administration（管理仓库）',
               'workflows':'Workflows（修改工作流）','unknown_permission':'未识别的额外权限',
               'permissions':'权限字段','additional_permissions':'还有其他额外权限（已省略）'}
        levels={'read':'只读','write':'读写','none':'无访问权限','missing':'未授予',
                'invalid':'响应格式无法识别','absent':'不应申请','read/write map':'权限字段表'}
        if error.code=='INSTALLATION_PERMISSIONS_MISMATCH':
            title='GitHub 权限需要核对'
            rows=[names.get(v['name'],v['name'])+'：现在 '+levels[v['actual']]+' → 需要 '+levels[v['expected']]
                  for v in error.permission_diff]
            text='GitHub 返回的安装权限与最小配置不一致：\n'+'\n'.join(rows)+'\n\n请先核对应用的 Permissions & events，再到安装设置确认。应用权限改过时，旧安装可能仍等待本人批准更新。不要重新创建应用，也不要额外授予仓库管理或代码写权限。'
        else:
            title='核对 GitHub 安装范围'
            text={'BROAD_INSTALLATION_REJECTED':'当前选择了所有仓库。请到安装设置改为 Only select repositories，只选择目标仓库。',
                  'OTHER_REPOSITORIES_ACCESSIBLE':'当前安装可访问其他仓库，或没有包含目标仓库。请只保留目标仓库。',
                  'TARGET_INSTALLATION_REQUIRED':'当前授权没有找到目标应用的唯一安装。请在安装设置核对该应用已安装到目标仓库。'}[error.code]
        self.send({'type':'notice','stage':'github','title':title,'text':'',
                   'links':['https://github.com/settings/apps/'+slug+'/permissions','https://github.com/settings/installations']})
        return self.request(title,text+'\n\n修正后点“重新检查”，只读取状态，不重新授权、不写设置。可选中文字提供权限差异，无需提供任何密钥。',
            [{'label':'我已核对，重新检查','value':'retry'},{'label':'暂停，稍后继续','value':'cancel'}])

    @staticmethod
    def app_id(value):
        if not re.fullmatch(r'cli_[a-zA-Z0-9_-]{8,128}',value):raise ValueError()
        return value

    @staticmethod
    def run_id(value):
        if not value.isascii() or not value.isdigit() or int(value)<=0:raise ValueError()
        return value

    def recover(self,progress):
        result={};pending=progress.data['pending']
        if pending=='secrets':
            self.request('上次保存中断了','请先在 GitHub 仓库设置查看 Secret 名称。确认后将重新连接同一个飞书机器人，重新保存设置；不会自动开启提醒。',
                [{'label':'我已检查，重新保存','value':'reviewed'},{'label':'先退出','value':'cancel'}],expected='reviewed')
            result['secrets']=True
        elif pending in ('prepare','preview','test'):
            run=self.field('确认上次的检查','上次提交后没有收到明确结果。请在 GitHub 的 Actions 页面核对任务，填写对应 run ID（地址末尾数字）。不会自动重新提交或发送测试。','任务地址末尾的数字',self.run_id,'这里只填写任务网页地址末尾的正整数，例如 123456；不要填写完整链接。')
            result['resolve']=(pending,run)
        elif pending is None:
            for stage,label in (('prepare','准备'),('preview','检查')):
                if stage in progress.data['runs'] and stage not in progress.data['completed']:
                    answer=self.request('继续上次的'+label,'可以继续等待上次任务。只有在 GitHub 确认任务失败并修复原因后，才选择重新检查。',
                        [{'label':'继续等待','value':'wait'},{'label':'已修复失败，重新检查','value':'retry'},{'label':'先退出','value':'cancel'}])
                    if answer=='cancel':raise SetupError('USER_CANCELLED')
                    if answer=='retry':result['retry']=stage
                    break
        return result

    def emit(self,stage,message):
        if stage=='plan':return
        if stage=='github' and message.startswith('当前授权 GitHub 用户'):return
        text={'prepare':'正在准备…','preview':'正在检查，不会发送消息…','complete':'检查已通过。新安装尚未开启；恢复中的开启结果以 GitHub 设置为准。',
              'enabled':'设置完成。每小时检查一次，符合条件时会通知你。','cleanup':'你可以在 GitHub 官方设置页撤销安装工具的授权，提醒仍会继续运行。'}.get(stage,'')
        links=[];user_code=''
        for url in re.findall(r'https://[^\s；，]+',message):
            parsed=urlsplit(url)
            if parsed.scheme=='https' and parsed.netloc in ('github.com','open.feishu.cn'):
                links.append(url.rstrip('。'))
        if stage=='github' and 'https://github.com/login/device' in links:
            code=re.search(r'\b[A-Z0-9]{4}-[A-Z0-9]{4}\b',message)
            if code:
                user_code=code.group()
                text='复制下方授权码，再打开 GitHub 官方页面粘贴。\n这个代码只给你本人使用，完成后会自动继续。'
        if stage=='feishu' and links:text='请打开飞书官方页面，按页面提示连接。'
        if stage=='github' and 'Enable Device Flow' in message:text='创建页还需要：勾选 Enable Device Flow，保留 Expire user authorization tokens。不要生成私钥或密码。'
        if stage=='github' and '名称如重名' in message:text='在 GitHub 页面核对后创建。应用名如果重名，请改一个名字。已预填最小权限。'
        if stage=='cleanup':return  # Native UI always provides explicit revocation links after stopping.
        if stage=='stopped':
            candidate=message.split('：',1)[0].split(':',1)[0]
            code=candidate if re.fullmatch(r'[A-Z][A-Z0-9_]{1,63}',candidate) else 'SETUP_FAILED'
            text='已暂停。下次打开可以继续。已提交的操作不会撤回；若刚确认开启，提醒可能已开启，请在 GitHub 核对。' if code=='USER_CANCELLED' else '暂时没有完成。请保留这项问题标识：'+code
            if code=='UNEXPECTED_ERROR':text=message
        if text or links:self.send({'type':'notice','stage':stage,'title':TITLES.get(stage,'继续设置'),'text':text,'links':links,'user_code':user_code})


def progress_directory():
    if sys.platform=='win32':
        base=os.environ.get('LOCALAPPDATA')
        if not base:raise SetupError('LOCAL_APP_DATA_REQUIRED')
        return Path(base)/'Codex Reset Installer'
    return Path.home()/'Library'/'Application Support'/'Codex Reset Installer'


def main():
    from installer.cli import main as install
    directory=progress_directory()
    gui=GUI()
    code=install(['--progress',str(directory/'state.local.json'),'--public-config',str(directory/'app.local.json')],ui=gui)
    gui.send({'type':'done','code':code})
    return code

if __name__=='__main__':raise SystemExit(main())
