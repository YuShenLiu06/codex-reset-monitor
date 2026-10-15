# Windows 测试版

这是供他人试用的 Windows x64 预览版。支持 Windows 10/11 x64；ARM64 暂未验证。

## 三步开始

1. 下载 Release 中的 `codex-reset-installer-windows-x64-preview.exe`。
2. 双击下载的 EXE。首次会显示“正在准备本地组件”，完成后点“开始设置”。
3. 按窗口提示连接 GitHub 和飞书。最后先收一条“安装测试”，确认收到后才开启提醒。

无需安装 Python、GitHub CLI 或 .NET，无需管理员权限。窗口需要你确认时才继续；你可以随时点“暂停设置，稍后继续”。重新打开 EXE，会继续上次的设置。首次准备可取消；取消后重新打开即可重试。

## 已知问题

- **测试版尚未签名**：Windows SmartScreen 或公司策略可能阻止启动。请保留系统保护，不要关闭 SmartScreen；将提示截图反馈给维护者，由维护者安排可信测试环境或签名版本。
- **GitHub App 已公开**：发行包复用维护者的 `codex-reset-installer`，已只读核对公开标识及最小权限。跨账号的完整安装尚待实际试用；如果安装页打不开，请暂停并反馈，无需自己创建 GitHub App，也无需提供密钥。
- **离线验收使用模拟服务**：已检查真实窗口、打包 worker、中文路径、输入修正、取消和恢复。此结果不等于真实 GitHub/飞书账号已端到端验证，也不代表真实通知已经送达。
- 暂停不会撤回已提交的操作。保存或提交时断开，下次会要求先核对 GitHub 的结果，避免重复发送。
- 运行组件按版本缓存在 `%LOCALAPPDATA%\Codex Reset Installer\packages`；每次启动校验完整性，损坏时重新准备。新版使用独立缓存，旧设置与进度继续保留。无需手动解压，也不要求管理员权限。
- 进度保存在 `%LOCALAPPDATA%\Codex Reset Installer`。该文件只保存阶段和任务编号，不保存 token 或机器人密钥。先不要手动删除它。
- 更新项目代码后，旧包可能提示 `PROJECT_FILES_MISMATCH`。请使用与仓库代码匹配的新包；程序不会覆盖仓库。

## 反馈

到 [GitHub Issues](https://github.com/YuShenLiu06/codex-reset-monitor/issues) 新建问题，附 Windows 版本、EXE 文件名、停在哪一步、窗口中的问题标识；可附隐去个人信息的截图。不要上传授权码、token、密钥、完整服务响应或个人进度文件。

该工具提供第三方概率提醒，不代表 OpenAI 官方保证。
