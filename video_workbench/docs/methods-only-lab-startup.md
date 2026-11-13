# 动画效果库启动模式

> 2026-11-14：本文保留 methods-only 入口和启动边界。Wiki、实验室标准 Skill 和正式主动读取已接入代码；首轮三个新角色已独立技术准入，正式 production v3、实验室 v2 与新前端已部署。新宿主的两次原生引擎技术重准入与 CAS 参数切换已通过，真实目录 GET 及浏览器均恢复 `html-svg`、`canvas`、`three` 可用。首个有界真实业务实验因 Skill 版本谱系不匹配失败，没有渲染或样片；`a6098e5…` 已新增 v4 合同并保留 v3 原字节，三个新镜像已构建。author-s3 因启动封装失败在模型前被阻断，调用为零；s4 完成 4 次 HTTP 200 complete 请求，谱系已通过，但新 Wiki 页 `expected_revision=null` 不符合整数 `0` 合同，仍为 `worker_output_invalid`，没有渲染或独立准入。仅内存改单个字段后的完整验证通过属于诊断，原始产物不修补、不代表 CAS 成功。v5 已以 `93e7626…` 的明确 10 文件提交推送，profile／lineage／knowledge／build 为 174 passed、2 skipped，独立目录／升级／准入检查 15 passed，v3／v4／validator 原字节保留。r4 完整源码构建及三个 `latest` 镜像推送已完成，构建回执零模型调用／部署；未运行 v5 新模型候选或部署 v5，本轮实际失败后不追加付费尝试。下一阶段是 v5 新独立技术验证及升级；旧升级脚本和 s4 执行器固定 r3／v4，不能用于新 release，须新脚本、candidate 目录及未保留 UID，失败 s4 不作成功准入。v4 r3 镜像未上线，旧委托未自动重试，后续优化／迁移未执行。W.2 的真实 Wiki CAS 回写／后续消费、样片认可、业务发布和正式采用仍须独立验收。具体阶段见[接通验证记录](wikiskill-rollout-validation.md)，整片 HyperFrames 工程由[当前设计](agent-creation-effect-library-plan.md)承接。

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
