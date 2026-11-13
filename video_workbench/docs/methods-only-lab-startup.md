# 动画效果库启动模式

> 2026-11-13：本文保留 methods-only 入口和启动边界。Wiki、实验室标准 Skill 和正式主动读取已接入代码；新角色实际准入／部署、精确发布确认前端与真实发布／采用尚未验收。整片 HyperFrames 工程仍由[当前设计](agent-creation-effect-library-plan.md)承接。

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

## 新 Wiki／Skill 协议的配置

私有执行器 `--method-protocol skills` 为新代码默认值，要求独立准入的
`video.animation-method-author.v2`／`video.animation-method-review.v2` 与它们的专用
调用凭据。新角色、资源、镜像测量与接受记录须成套验证后再部署；旧角色令牌和准入
不能借用。仅恢复既有 v1 测试时显式选 `--method-protocol legacy`，不会自动降级。
启动、GET、知识页修正和精确人工认可接口不提交模型任务。

methods-only 代理在 `/api/method-lab` 下提供知识摘要、单页／单经验、已发布目录、
精确方法版本、发布就绪检查，以及 `POST /validations/<id>/accept`。这些接口沿用
开发者身份、Host／Origin 和 CSRF。认可必须绑定确切方法内容、验证目标与媒体摘要；
代表片与迁移分别认可，有声音效时另需实际试听。不能用旧页面的泛化发布框补签。

正式工作台新增独立配置 `VIDEO_SKILL_PRODUCTION_AGENT_URL`、
`VIDEO_SKILL_PRODUCTION_AGENT_TOKEN_FILE`（或对应 `..._TOKEN`，仅选一种）与
`VIDEO_SKILL_PRODUCTION_PROFILE_REVISION`。角色是 `video.animation-production.v3`，
缺配置／未就绪会明确不可用，不静默回退到旧方法作者。

`VIDEO_METHOD_LAB_URL` 与独立 token 继续连接私有方法 API。
多用户共用开发者已发布技能时，由服务端设置 `VIDEO_METHOD_LAB_PUBLISHER_OWNER`；
它只改变新 Skill 路径读取已发布目录／包的来源，终端任务、消费者及冻结归属仍各自
隔离，不能通过浏览器参数改变发布者。未设置时读取各用户自己的发布库。
旧方法交付／旧联合策划仍按原 owner 合同恢复，不重解释历史收据。
