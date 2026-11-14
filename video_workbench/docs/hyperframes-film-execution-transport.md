# 统一整片快照与 PVC 运输

这是 H.3 的执行输入底座，代码位于 `hyperframes_film_snapshot.py` 与 `hyperframes_film_execution.py`。它复用现有整片工程、浏览器宿主和一次编码导出器，不扩大旧七文件 Job 或 ConfigMap 合同，也不新建长期执行服务、数据库或另一套任务账本。当前实现与本地合同检查不等于集群运输、取消、部署或正式制作验收完成。

## 冻结与独立校验

`freeze_film_execution_snapshot()` 接收一个已冻结浏览器工程包、外部 bundle 摘要、执行端 runtime、镜像 `latest@sha256` 和可信宿主摘要清单。快照记录总工程、全片时钟、模块、全部原 WAV、项目版本、制作序列、超时及压缩档身份；不会从当前项目头重新选录音或把单镜包改名。

`decode_film_execution_snapshot()` 必须同时收到外部可信 snapshot SHA、执行端 runtime、镜像和镜像内宿主摘要。它先核对身份，再解包、按包内清单核验并通过已有纯编译器重编译。只信包中自报的宿主 SHA 不足以执行；宿主来自镜像内 `video_workbench/motion_renderer`，输入不能覆盖它们。原录音的版本、PCM 字节、采样率和完整时钟仍由原整片验证器核对，不裁尾、拉伸或回退 MP4 拼接。

归档只有普通相对路径文件，不使用 `extractall()`。拒绝路径逃逸、反斜线、重复／大小写冲突、符号或硬链接、目录／设备／管道成员、未声明文件、改动的 WAV、异常 gzip／tar 和解压预算越界。tarfile 读前缓冲内藏在终止块后的非零内容也会拒绝；元数据与实际文件均纳入有界读取。

| 边界 | 上限及来源 |
| --- | --- |
| 文件数 | 568：512 个工程文件＋6 个宿主＋配置／入口＋最多 47 个模块 realm＋浏览器 manifest |
| 原始资源 | 205MiB：既有 204MiB 浏览器资源上限＋最多 1MiB manifest |
| 单文件 | 32MiB，不扩大既有模块、资产或原 WAV 自身更小的分类限制 |
| 压缩档 | 64MiB，确定性 gzip／tar；文件路径、数据、总量和元数据分别受限 |
| JSON 请求 | `4 * ceil(64MiB / 3) + 4MiB`，约 89.34MiB，小于已有执行端 192MiB 总请求上限 |
| 整片导出 | 保持已有 60 秒／1440 帧上限；更长输入明确拒绝，不静默裁片 |

HTTP 入口应在读取 body 前串行预占大请求准入，解包也应单任务执行，避免并发大 body 与重复 base64／JSON 副本耗尽内存。队列及 journal 保存压缩档会占存储，提交前须为 journal、输入归档、最多 128MiB 成片和三个有界回执预留容量；不要只提高请求限额。Job 配置 1GiB 请求／2GiB 内存上限及 2GiB 临时空间，具体峰值仍须在目标环境实测。

## PVC 与固定 Job

执行服务先将快照 staging 到自己已挂载 PVC 的 `hyperframes-films/inputs/<snapshot SHA>/`，写入去 base64 的 `snapshot.json` 和唯一 `input.tar.gz`，不保存第二份大 base64 文件。独立校验成功后原子重命名；重放读取已有精确版本，不覆盖或修复已损坏输入。读取检查目录、普通文件、链接、大小、原始 JSON、归档及外部身份。

`film_job_resources()` 只产生固定 Job 和匹配该 Job 标签的 deny-all NetworkPolicy，不调用 Kubernetes。输入使用精确 PVC subPath 只读挂载；输出使用同一现有 PVC 的 `hyperframes-films/results/<operation key>/`，不会挂载整个 PVC 根。输出目录须由执行控制器预创建并赋予 UID/GID 10001 权限。工作目录为有界 emptyDir；不挂服务账号 token，无外网、只读根、非 root、丢弃 capabilities，`backoffLimit=0`，有实际执行超时及终止宽限。

固定入口为：

```text
python3 -m video_workbench.hyperframes_film_execution \
  --expected-snapshot-sha256 <trusted snapshot SHA> \
  --image <registry/name:latest@sha256:...>
```

路径固定为 `/input`、`/output`、`/work`。镜像从普通完整源码构建，应用包放 `/app`，显式使用 `/opt/renderer` 的 pinned Node／Chromium／HyperFrames 依赖，不接受输入提供命令、宿主、模块路径或 URL。runtime 在 Job 内实时采集并匹配镜像身份；SIGTERM 进入导出器的进程组清理，Pod cgroup 是最终边界。输出写入视频和三份回执后才写 `completed.json`；完整、身份一致的已有结果可回读而不再渲染，部分或改动结果拒绝采用且不擅自重跑。

捕获入口通过 Node ESM 的动态 `import()` 加载 Playwright，因此显式模块为 `/opt/renderer/node_modules/playwright-core/index.mjs`，不能填包目录。实际 Node 解析回归确认目录导入报 `ERR_UNSUPPORTED_DIR_IMPORT`、明确的 `.mjs` 文件可导入；CLI 配置另有回归。这个回归只验证模块加载形态，不声明已经启动浏览器或部署镜像。

`verify_completed_film_output()` 的恢复接口必须收到完整可信快照、外部 snapshot SHA 和执行端 release 身份。除文件与回执内部摘要外，还把 film／bundle／project、24fps 与 48kHz 全片时钟、尺寸、逐帧捕获与宿主 core、全部原 WAV 及混音采样边界重新绑定到冻结输入。仅有 `completed.json` 中自报的 snapshot SHA 不足以恢复；即使同时改动输出身份并重算所有内部摘要，也不能替换可信输入。

## 根业务接入点

现有 `MotionExecutionService`／`ExecutionJournal` 接新 schema 分支时，应在事务中保存 owner、稳定 operation key、精确 snapshot SHA 与确定性 Job 名，再创建资源。响应未知时查询同一 Job，不创建新 operation。取消先持久化意图，再删除该 Job／Pod并等待不存在，不能把 API 删除响应当资源已清理。结果从该 operation 的精确 PVC 目录读取并独立验证媒体／时长／原声，不能凭 `completed.json` 或模型声明写人工采用。失败与清理待办留在原 journal，有限预算不重开。

部署还需完成：执行端与 Job 的同一 PVC 挂载；新 schema 的 API 验证、存储预留和串行准入；现有 journal 的整片 Job 创建／找回／取消／清理；普通 renderer 镜像内应用 Python 包及依赖；结果回读到制作服务；新 Agent 模块及 `ProductionRunStore` 调度。这些接线由根任务实现，当前纯 helper 不宣称已完成。

## 本地验证范围

专项检查覆盖多个模块与三份混合采样率原 WAV 的完整往返、外部 SHA 与执行端宿主授权、归档安全与预算、输入原子 staging／不覆盖、确定性 Job 隔离资源、完成标记重放、部分／改动结果拒绝，以及取消不发布产物。结果写入使用明确的非媒体 exporter fixture，只验证运输和持久边界，不当成真实编码、HyperFrames、试听或集群取消证据。测试临时目录位于既有 D 盘研究目录，没有下载依赖、调用模型或部署服务。
