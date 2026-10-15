# 用户本人验收清单

## 先确认前置条件

当前未发布正式 Release。只有 macOS arm64 本机测试包，未完成 Apple 公证；完整真实流程仍待按本清单验收。已有本人反馈安装跑通，不等于公开发行验收完成。

维护者的个人 App 可能已创建；普通用户包所需的可信共享配置尚未公开发布。若窗口要求 Client ID / App 地址，请联系维护者，注册与权限见[维护者配置](github-app-maintainer.md)。

- **窗口测试包**：内置 Python / gh，不要求另行安装。仅使用维护者提供且来源可核验的测试包。
- **命令行安装器**：支持 macOS/Linux，需要 Python 3.10+ 和官方 GitHub CLI。`--plan` 只预览，不授权。缺依赖或公开 App 配置时，在授权前停止。

以下记录授权与验收要点。窗口测试版按窗口操作；大写确认词及命令参数用于命令行入口。

## 本人完成一次真实流程

1. 运行一次包，回车开始；可信共享配置包直接安装，只有缺公开配置时进入维护者首次初始化。确认计划和权限；`--run` 时选择复用兼容仓库或在官方页面 fork 新仓库，不覆盖原同名仓库。
2. 在 GitHub 官方页面只授权目标仓库，完成 device 授权。确认终端显示的 GitHub 用户和完整目标仓库名是你预期的。
3. 新建飞书应用时仅在官方验证页面授权；已有应用选择复用。核验同应用本人 open_id、机器人能力、发送权限、生效发布版本和可用范围。
4. 最终确认拟写 Secret 名称和仓库；值应隐藏，没有出现在终端参数、文件或公开日志中。
5. 查看 prepare 与 preview 的本次 run：正确目标仓库、提交、关联 ID，所需 job 成功；原 ledger 仍保留。预览后 `MONITOR_ENABLED=false`，没有通知发送。
6. 本人输入 SEND_TEST，授权一次明确标为“安装测试”的通知。在飞书收到匹配请求标识后，输入 RECEIVED 加该标识，再输入 ENABLE 加完整仓库名。任一步不确认均保持关闭；测试结果不明时停止核对，不盲目重发。测试独立于预测，不编造预测数据。
7. 安装后撤销安装工具 GitHub 安装及用户授权。真实结果反馈时仅提供下列脱敏信息。

## 可以反馈什么

提供操作系统与 Python/gh 版本、包 SHA256、失败阶段、固定错误码、目标仓库、公开 Actions run URL、你已核验的权限/发布检查结果。若仓库是私有的，可只说是否同一目标，不公开其名称或完整 URL。

**不要提供** token、App Secret、飞书 Webhook、sign、refresh token、device/user code、openid、完整环境变量、原始 HTTP 响应或完整授权终端录屏。程序已验证对上游错误体和 CLI stderr 脱敏；不要自行开启 debug 或 set -x。

## 失败恢复

- `CLIENT_ID_REQUIRED/REAL_INSTALLER_APP_REQUIRED`：维护者配置尚缺；不会自动注册一个替代 App。
- `GH_CLI_REQUIRED/PLATFORM_UNSUPPORTED`：先按官方来源补依赖或使用受支持机器，未开始授权。
- `PROJECT_* / UNREVIEWED_EXECUTABLE_FILES`：停止，不覆盖；检查包与仓库版本或选择新 fork。
- `PERMISSION_OR_POLICY`：在官方页面核验选中仓库和最小权限；组织策略/仓库 Actions 禁用由用户或管理员处理，不扩大权限绕过。
- `RESUME_IDENTITY_MISMATCH`：用原 GitHub 用户续接；另一用户应使用独立进度文件，不借用原用户已完成阶段。
- `SECRET_UPLOAD_UNCONFIRMED`：门控仍关闭，先检查 GitHub Secret 名称；明确复用同一飞书应用后用 `--acknowledge-partial-secrets`，会要求用户确认。不要重新创建应用。
- `PREVIOUS_DISPATCH_UNKNOWN`：先在 Actions 找到匹配的提交和关联 ID，用 `--resolve-run prepare|preview|test RUN_ID`，不能随便选一个成功 run。
- `WORKFLOW_FAILED`：修复失败原因后使用 `--retry-failed-stage prepare|preview`；会要求用户确认，并保留旧 run 记录。它只重验无消息的 prepare/preview，不重发通知。
- `WORKFLOW_TIMEOUT`：run ID 已保存，续接等待原任务，不再 dispatch。
- `pending/unknown` 通知记录：停止发送并核对飞书，保留 ledger；安装器不会清空它。

本地非敏感进度在 `.codex-reset-setup/state.local.json`。进程结束会释放短期凭据引用；Python 不承诺内存取证级擦除。没有凭据文件可迁移，跨进程续接时可能需要重新官方授权或以公开 App ID 重新官方扫码复用。

## 验证范围

自动化已验证：离线和模拟协议、仓库/权限/身份绑定、状态/防重、workflow 结果关联、打包隔离、依赖预检和固定错误脱敏。

用户待验收：真实 GitHub/飞书账号授权、组织策略与官方 API 可用性、真实 Secrets 写入、飞书权限与发布状态、一次安装测试真实送达、本人确认开启及调度持续运行。我们没有代你执行这些动作。
