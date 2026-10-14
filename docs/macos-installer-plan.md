# macOS 双击版最小实施选择

选择：SwiftUI 薄窗口 + 复用 installer/core.py 的本地工作进程，`.app` 内打包受支持 Python、固定版本 gh 及可信公开 App 配置。图形入口以按钮/字段取代 CLI input，不模拟键盘输入或解析终端输出。授权仍打开官方页面；后台任务结果通过固定消息协议返回。现有仓库、权限、去重与验收检查保留。

用户路径：下载 Release → 解压双击 → 官方授权/必要配置 → 点击测试 → 确认收到并开启。普通用户包必须内置维护者公开 App 配置；缺配置才显示维护者初始化页。无需用户安装 Python/gh，无 sudo，不写系统目录。此文是实施评估，图形版尚未实现或发布。

构建依赖：macOS Swift/Command Line Tools、本机受支持 Python、固定版本 PyInstaller（打包运行时）、固定版本官方 gh 分发包。本机为 arm64，已有 Swift/CLT 与 gh，默认系统 Python 3.9.6，无 PyInstaller。尚未安装额外工具；需先授权项目隔离构建依赖。为降低变量先出 arm64；Intel 需独立架构构建/实测，不能把 arm64 包称 universal2。

发行限制：Developer ID 签名、hardened runtime、Apple notarization/stapling 应由维护者在其受控环境完成；本次未读取/创建签名身份或苹果凭据。PyInstaller 的 ad-hoc 签名不能替代 Developer ID 和公证。未签名本地工程不能宣称已达到公开 Release 下载双击体验，不指导关闭 Gatekeeper。还需审计打包运行时和 gh 的许可证、校验下载、确认所有嵌套可执行文件签名。

优先完成当前启动 bug 和实际包回归，再实现 GUI 适配层；不重写远端部署架构，不引入常驻服务或共享敏感凭据。

依据：[PyInstaller macOS .app 与架构/签名参数](https://pyinstaller.org/en/stable/usage.html)、[Apple 软件公证](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)。
