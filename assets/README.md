# 项目标识

`logo-v1.png` 是用户委托生成的透明 PNG 原图，保留原始字节。它是本项目标识，不代表 OpenAI 官方产品。

macOS 构建只用系统 `sips` 生成各尺寸 PNG，再用 `iconutil` 合成 ICNS；不重绘原图。原图同时随包保留，BUILD-CHECKS.json 记录 SHA256。
