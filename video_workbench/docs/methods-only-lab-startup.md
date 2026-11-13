# 动画效果库启动模式

> 2026-11-13：本文保留 methods-only 入口和启动边界。正式方法读取接线后续已交付，但开发者认可、真实发布与正式新内容采用闭环仍需证据；Skill 主动发现和统一 HyperFrames 工程是[当前设计](agent-creation-effect-library-plan.md)的待实施目标。

生产使用 `--mode methods`。现有启动命令只要配置了 `--creation-url` 和
`--creation-token-file`，默认的 `--mode auto` 也会进入此模式。

```powershell
python -m video_workbench.animation_lab --mode methods --data-dir <reserved-directory> --creation-url <private-executor-origin> --creation-token-file <private-token-file> --proxy-mode --listen 0.0.0.0 --public-origin https://video-service.panghuer.top --base-path /dev/animations --developer-user <trusted-developer-principal>
```

此模式只提供完整效果库页面、方法实验 API 和验证后的媒体／源码下载代理。
实验记录、方法版本、发布记录和渲染均由私有执行器管理。
UI 进程不创建旧的 `animation-lab.db`、`animation-experiments.db`，不编译固定样例，
也不读取 `--assets-dir` 或 `--evaluations-dir`。`--data-dir` 参数保留用于启动命令一致性，
该模式不会访问或创建此目录。

旧的样例、标注、回归、实验和旧制作接口，以及其脚本与预览资源，在此模式下返回
404。鉴权、开发者名单、固定 Host／Origin、CSRF、媒体字节范围和源码下载边界保持。
健康检查 `/healthz` 仍可供探针读取。服务不会清除磁盘上的已有旧数据。

本地维护旧功能可显式使用 `--mode legacy --assets-dir <renderer-assets>`。
不配置执行器时，`--mode auto` 也保留原本的本地开发行为并要求资源目录。
直接构造 `AnimationLab(...)` 默认保留旧模式；调用方选择
`methods_only=True, method_client=...` 才启用新模式。

该阶段发布对象是版本化方法知识；后续已有正式读取接线，不能据此推断已完成真实认可／发布／采用闭环。当前进展与新增目标以主设计及服务端任务为准。
