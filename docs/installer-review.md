# 免 clone 安装流程审查稿

本页记录安装器的授权、安全与命令行打包设计，不作为当前下载入口。当前发行与测试状态见[首页](../README.md#start)和[macOS 测试状态](macos-installer-plan.md)。已有本人安装跑通反馈，完整真实流程仍待按[验收清单](user-acceptance.md)核对；尚未发布正式 Release。

## 用户入口

维护者审查代码并将对应版本合入主仓库后，构建 Release 的 `codex-reset-install.pyz`、`codex-reset-install-skill.zip` 和 `SHA256SUMS`。不要求用户 clone。live 安装器目前支持 macOS/Linux；用户需有 Python 3.10+ 和来自官方来源的 GitHub CLI（用于本地加密 Secrets）。默认在交互终端询问开始并引导首次公开配置；非交互默认退出为预览。无副作用预览使用 --plan：

```bash
python3 codex-reset-install.pyz --plan
```

未来已注册真实 GitHub App 后，用户在本机交互终端运行：

```bash
python3 codex-reset-install.pyz --run \
  --client-id PUBLIC_CLIENT_ID --app-slug PUBLIC_APP_SLUG
```

公开 client_id 与 slug 可以由维护者发布在安装说明中，无需用户搬运私钥或 client_secret。Skill 包内的 launcher 调用同一个 `.pyz`；代理只可运行默认离线计划，默认交互入口会引导本人在官方预填页面创建 GitHub App，只保存公开 Client ID/slug；真实授权和最终敏感配置确认由用户在自己的终端完成，不将凭据或完整终端记录贴入聊天。

## 授权、复用与验证

| 步骤 | 用户或程序动作 | 不能省略的边界 |
|---|---|---|
| 仓库选择 | 提示 OWNER/REPO；已有仓库可复用，新建使用 GitHub 官方 Fork 页面。 | 不调用自动 fork API，不创建空仓库上传文件。原同名仓库不覆盖，版本不匹配时停止并提示使用对应审查版本。 |
| GitHub 安装 | 用户在官方 App 安装页面仅选择目标仓库，再进行官方 device 授权。 | App 必须事先真实注册并开启 device flow。只用公开 client_id；没有长期后端、共享 App 私钥或 client_secret。 |
| 精确权限 | 检查安装记录、仅一个可访问目标仓库、用户仓库权限与 API 可用性。 | 安装器申请 Secrets/Actions/Variables:write、Contents/Metadata:read。拒绝 all repositories、Contents:write、Administration 或其他额外权限。飞书授权不覆盖 GitHub。 |
| 项目身份 | 核对项目标识、原项目 fork 来源、main 的 SHA 和包内构建时文件哈希。 | 所有 Python 文件和 workflow 均列入可信清单；拒绝未知可执行文件/工作流和符号链接，不能只检查同名或可修改的标识文件。应信任目标仓库写入者；核验快照不能阻止写入者随后改变仓库。 |
| 飞书应用 | 默认扫码新建；明确选择复用时只输入公开 App ID，再走同一官方注册确认流程。使用官方 SDK/CLI 的 PersonalAgent 协议与 addons 最小机器人模板。 | 两种模式均从官方注册响应取得同应用本人 open_id；复用返回的 App ID 必须匹配。不会自动重建失败会话；退出后 Secret 不保存，以公开 App ID 重新官方扫码复用。无 QR 云服务依赖；官方授权页可在手机浏览器完成，包本身不生成二维码。 |
| 发送资格 | 用户在飞书后台核验机器人能力、`im:message:send_as_bot`、已审批发布版本及本人可用范围，程序检验应用认证。 | 人工核验记录与真实能力验证区分：取得 token 或注册完成都不证明发送权限、范围或送达。需要额外权限的管理 API 不自动申请。 |
| 最终确认 | 显示目标仓库、5 项 Secret 名称和用途，用户再次输入仓库名。 | 首次仍有官方授权/审批、权限核验及目标确认；不能称零操作。Secret 值不会显示，经官方 `gh secret set` stdin 本地加密上传，后端选择标记最后上传。 |
| 初始状态 | 安装器关闭发送门控并 dispatch 固定 prepare workflow。 | 临时 `GITHUB_TOKEN` 在 workflow 中用 Contents:write 初始化状态；安装器不获 Contents:write。已有状态保留，已初始化却缺失状态时停止。 |
| 完整安全检查 | 等待 prepare 和 preview 指定 job 成功，核验 run 的提交、事件、随机关联 ID，检查状态结构。 | 此阶段保持关闭。随后用户输入 SEND_TEST 才发送一次独立安装测试；收到匹配请求标识后输入 RECEIVED 与 ENABLE 才开启。调度不承诺精准或永久在线。 |

仓库级 Actions 被禁用或组织策略阻止时，安装器停止；用户在 GitHub 官方仓库 Actions/设置页面开启或请求管理员处理。不会申请 Administration 权限代为开启。禁用 fork 的单个 workflow 可在既有 Actions:write 内启用。

GitHub fork API 需要更宽的账户仓库访问，因此不作为默认部署方式。device flow 的 `offline_access` 只请求可过期用户 token，不扩大 GitHub App 仓库权限。token 必须是可过期的 GitHub App 用户 token；refresh token 丢弃，不建立后台刷新服务。

## 失败续接与凭据生命周期

`.codex-reset-setup/state.local.json` 只存目标仓库、阶段、任务关联 ID / run ID、注册是否已开始、未确认操作类型。文件用 0600 原子写入，目录初建 0700；另存非敏感 numeric GitHub user ID 用于账号绑定和旧失败 run 历史；不存 token、App Secret、open_id、device/user code 或消息。不要把自己的进度文件提交给公开仓库。

- 已知 run ID：重新授权原 GitHub 用户、重新核验仓库后等待原任务，不再 dispatch；numeric user ID 不同会停止。
- 原 workflow 明确失败：用户修复原因后通过 `--retry-failed-stage prepare|preview` 确认安全重验，核验失败 run 身份并保留旧 run 历史，不自动重试或发消息。
- dispatch 回应丢失：停止；用户在官方 Actions 页面核验 run，然后通过 `--resolve-run prepare|preview|test RUN_ID` 明确关联。仍检查提交、关联 ID 和所需 job，不能猜一个成功任务。
- Secrets 部分上传：发送门控保持关闭，检查 GitHub 上的 Secret 名称后，以 `--acknowledge-partial-secrets` 明确续接并复用同一飞书应用，不盲目重试。已成功上传的 Secret 无法从 GitHub 读回；中断后重新安全输入是必要恢复步骤。
- 已开始新注册：保存的是非敏感标志；中断后引导复用应用。没有自动再次创建、重新发消息或清空去重状态。

程序只输出固定阶段与脱敏错误码；上游响应、stderr、请求对象均不写诊断。GitHub 401/403/404/409/429 区分为安全错误类别。错误或中断后释放 token 和 Secret 对象引用；Python 不能保证内存取证级擦除，程序结束让 OS 回收进程内存。官方 gh 子进程使用空临时 HOME/config，禁用调试环境，不读已有登录文件，不把明文凭据写入该目录。

安装完成或停止后，提示用户撤销安装工具的 GitHub 安装及用户授权。仅安装器需要这些权限；已配置监控使用临时 GITHUB_TOKEN 和飞书应用 Secrets。程序无法只凭公开 client_id 调用需客户端凭据的 token 撤销 API，因此撤销由用户在官方页面完成。

## 维护者审查与打包

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py
python3 runtime/release-build/codex-reset-install.pyz
```

构建脚本只打包显式 allowlist，不带 `.git`、runtime、配置、父环境授权目录或任意递归工作区文件。Skill 包内 `.pyz` 与 Release `.pyz` 按字节一致；校验和用于核对下载完整性，不替代可信发布来源或独立签名。首次正式 Release 前应将代码审查合入 main，打包与目标仓库内容对应，且必须完成真实 GitHub App 注册；本次只提交审查后源码，不发布 Release 或执行真实授权。

主要验证覆盖：应用协议、身份绑定、token 和消息返回验证；发送前持久 pending / 未知阻塞；同周期跨后端去重；仓库来源/文件完整性；最小安装权限；未知操作续接；首次状态初始化与已有状态保护；Release/Skill 包隔离；模拟用户端端到端与凭据引用清理。模拟验证不能替代真实账号验收；实际飞书送达、权限与组织策略应按验收清单逐项记录。

## 官方依据

- [GitHub App device flow](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app#using-the-device-flow-to-generate-a-user-access-token)：公开 client_id、设备授权、用户 token 的权限交集与有效期。
- [GitHub fork API](https://docs.github.com/en/rest/repos/forks#create-a-fork)：自动 fork 的授权限制。
- [用户可访问的 App 安装与仓库](https://docs.github.com/en/rest/apps/installations)、[workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)。
- [GitHub CLI Secret 本地加密](https://cli.github.com/manual/gh_secret_set)。
- [飞书官方 CLI 注册实现](https://github.com/larksuite/cli/blob/main/internal/auth/app_registration.go)：表单协议、默认 5 秒/600 秒、降速/过期处理；官方 SDK 也公开提供 registerApp/register_app；本项目以标准库实现兼容流程，变更时停止并更新。
- [飞书应用消息 API](https://open.feishu.cn/document/server-docs/im-v1/message/create)：应用机器人、发信权限、生效版本、收件人范围；与群 Webhook 不同。

维护者首次 App 字段、权限与 manifest 预填限制见 [GitHub App 创建步骤](github-app-maintainer.md)。

用户本人验收、脱敏反馈及恢复步骤见 [验收清单](user-acceptance.md)。

官方 SDK 对照：[Node registration/addons](https://github.com/larksuite/node-sdk/blob/main/scene/registration/addons.ts)、[Python register_app](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/README.zh.md)。addons 使用 gzip + URL-safe Base64 的 JSON，明确 preset=false，只申请 tenant 的 im:message:send_as_bot，无事件、回调或 user scopes。模板请求不证明权限已生效，仍需用户核验发布与范围。测试通知使用独立随机请求标识及远端写前记录，pending/unknown 无自动重试，也不消耗预测冷却。
