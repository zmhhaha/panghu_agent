# 统一整片 Kubernetes 接线

`hyperframes_film_k8s.KubeFilmRenderer` 复用现有 `ExecutionJournal`、Kubernetes 客户端和执行 API；新 schema 走独立分支，旧单镜与 `MixedMotionRenderer` 合同不变。它不创建新的账本或长期服务，也不替 Agent profile、人工试听或技能发布作出准入决定。

## 开关与固定配置

默认没有新 schema 执行能力。只有显式设置 `VIDEO_HYPERFRAMES_FILM_ENABLED=1`，并提供下列全部受信配置，执行服务才创建新 renderer：

| 环境变量 | 含义 |
| --- | --- |
| `VIDEO_HYPERFRAMES_FILM_NAMESPACE` | Job 命名空间；PVC 必须存在于同一命名空间 |
| `VIDEO_HYPERFRAMES_FILM_PVC_NAME` | 该命名空间中的 PVC 名称 |
| `VIDEO_HYPERFRAMES_FILM_PVC_ROOT` | 执行服务已挂载该 PVC 的绝对根目录 |
| `VIDEO_HYPERFRAMES_FILM_PVC_WORKLOAD_SELECTOR` | 可信 JSON matchLabels；Job 必须与该同 namespace 的 PVC 持有工作负载同节点 |
| `VIDEO_HYPERFRAMES_FILM_IMAGE` | 普通完整源码构建的 `latest@sha256` 镜像 |
| `VIDEO_HYPERFRAMES_FILM_RELEASE` | 可追溯源码版本 |
| `VIDEO_HYPERFRAMES_FILM_RUNTIME_FILE` | 只读 JSON：镜像对应的真实 runtime fingerprint |
| `VIDEO_HYPERFRAMES_FILM_HOSTS_FILE` | 只读 JSON：镜像内六个可信宿主的 SHA 清单 |
| `VIDEO_HYPERFRAMES_FILM_ADMISSION_FILE` | 操作者根据真实技术执行签出的只读准入记录 |

Kubernetes API、CA 与 token 复用现有执行服务配置，但 namespace 单独指定，避免把另一个命名空间的同名 PVC 当成同一份数据。执行服务仍需要这个 namespace 的有限 Job／Pod／NetworkPolicy 权限，业务服务与模型不持有 Kubernetes 凭据。

已有 Ceph RBD PVC 使用 RWO，不能只按 arm64 调度并期待偶然同节点。资源生成器的可信 `pvc_workload_selector` 会产生 required podAffinity，固定 namespace 与 hostname topology，并纳入 Job binding；执行服务的启用配置要求明确提供该 selector。独立工具调用若不提供 selector，只适用于操作者另行确认能够跨节点并发挂载的存储，不能据此部署现有 RWO 卷。

准入记录的 schema 是 `video.hyperframes-film-execution-admission.v1`，`status=admitted`，scope 固定为 `technical_execution_only_not_agent_profile_or_human_approval`。镜像、runtime、宿主摘要必须与受信配置完全一致；`evidence` 必须含 `job`、`media`、`cancellation` 的持久引用和 SHA。记录自身 `sha256` 是其余字段的 canonical digest。代码只验证这份操作者记录，不自动生成 admitted，不因某个 fixture 通过而启用服务。

## 持久意图、恢复与取消

每个 owner／operation 复用原 operation key，只增加一个 `key:film` scene handle。完整快照独立验证后，先持久化确定性 Job 名与 binding，再准备精确 PVC 输入／输出目录；在 POST 前将 handle 写为 `submitting`。输出目录明确赋给 UID／GID 10001；Job 的 fsGroup 同时保证工作用 emptyDir 可写。

POST 响应未知时仅查询同名 Job。若仍是 404，保留 `submitting`，不再次提交，不创建新 operation 或另一 Job。若 Job 已出现，则独立核对完整固定资源绑定，再记录 `running`。旧 Job 已观察过而后消失时，明确失败并清理，不悄悄重开制作。

取消和失败先写 `cleanup_pending`，再用已绑定 Job 的 UID 前置条件执行 foreground deletion，并等待 Job 与其 Pods 都不存在。未知提交后的 404 不足以证明没有未来提交，故继续保持 pending；迟到 Job 被观察到时，先持久化消除未知状态，再删除，避免删除成功后遇到网络故障而丢失恢复依据。清理失败、绑定冲突或遗留 Pod 均保留 pending，不宣称终态。

## 输出及独立验证

成功必须先观察到唯一 Pod 使用固定 image digest 且退出码为零，再从精确 operation 输出目录核对完整结果。恢复校验强制传完整可信快照及外部身份；执行服务另行用本机 ffprobe 检查尺寸、24fps、精确帧数、时长、零起点和 48kHz 双声道 AAC，并用 ffmpeg 完整解码。不会从每镜 MP4 拼接，也不从当前项目头重选录音。

只有验证后的完整成片复制到原服务的 `<operation key>/result/video.mp4`，下载 API 无需改变。复制中断留下的已知 `video.pending` 在普通文件／单链接检查后可从可信 PVC 重建，不重新提交 Job。公共结果只返回 film、project、bundle、时钟与回执摘要；不暴露服务文件路径、完整捕获表或凭据。

HTTP PUT 在读取 body 前用全局非阻塞锁串行准入，大请求忙时直接返回 429。新整片验证／解包亦用单个锁串行执行。提交前按所有 outstanding operation 每项 768MiB 预留 JSON／WAL、压缩输入、成片与回执容量，同时统计挂载的整片 PVC；仅增加请求限额不能通过准入。已有同一 operation 的重放不再占新预算。

NetworkPolicy 采用规则相加后的实际含义：必须存在匹配的双向 deny，任何可能匹配 renderer 或 Kubernetes Job 自动标签的额外允许规则都会拒绝执行。

## 验证边界

本地回归覆盖 intent-before-POST、丢失响应的同名恢复、不确定提交不重投、迟到 Job 取消、Job 已删除但 Pods 未消失时不终态、外来 binding 不删除、结果回读和复制崩溃恢复、owner 隔离、新 schema 默认关闭、读 body 前拒绝忙请求及队列存储预留。调度 fixture 使用显式非媒体字节和假的 Kubernetes 客户端，不把这些回归当成真实集群、媒体编码、Agent profile、人工采用或业务上线证据。真实 Job、成片与取消记录须另外取得后再由操作者签出 admission。
