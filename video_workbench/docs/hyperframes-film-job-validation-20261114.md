# 整片 HyperFrames：镜像、真实 Job 与恢复验证

2026-11-14。承接[正式接入次序](hyperframes-film-production-integration.md)。本轮完成可组合模块与执行接线底座，并实测镜像及 PVC Job；正式制作主流程尚未切换，H.3 保持未完成。

## 实现与独立审查

`bce71a5` 新增 `video.animation-film-module.v1`、自足工程基础 Skill、精确前任及录音时钟合同、独立网关和禁用候选目录。Agent 自由输出 `mount/renderAt/dispose` 多文件模块，宿主计算摘要并采集真实 Skill 工具证据；没有自动包裹旧单镜源码或启用新角色。

`642fe32` 增加受限归档、完整外部身份复验、PVC 原子 staging、固定镜像内 CLI 与普通完整源码 renderer。后续控制器接线增加同一 `ExecutionJournal` 的整片 schema、稳定 Job 意图、同名恢复、取消排空、独立媒体回读、串行大请求准入与容量预留。独立应用适配器将确切已收集模块组装为一个工程，HTTP 客户端要求冻结快照、48kHz 双声道和整片回执。它们仍是可调用代码，未接入 `ProductionRunStore`，未部署新的执行 API。

审查发现并修复了 Playwright 绝对目录 ESM 导入、跨 Debian 版本复制 CPython 的 GLIBC 不兼容、结果仅内部互相绑定、崩溃残留复制文件，以及网络策略自动 Job 标签和不确定匹配的问题。原样片、旧 profile 和未关联改动保留。修复后 Python 使用 Node 基础系统内的独立 venv，不复制另一发行版的 Python；实际镜像构建及启动验证通过。

## 普通镜像构建

服务器从完整、干净 Git `642fe32e856d696b11785bb71a8794db3fba0cca` 同步，测量 `agent_service` 和 `video_workbench` 的 Git 文件并生成可读构建上下文。依赖可复用正常 Docker 层缓存，不使用旧应用镜像拼接或源码挂载。

- 镜像：`arm-cluster-master:5000/video-motion-renderer:latest@sha256:701fea4765af8808647ac9f2defc63d0f3cf89a74279a0bf6455b4b30045aef8`。
- 全部应用源码清单 SHA：`d35ca45ab82de7c38773629f47c7c5e20189ff295c7d4dcd056f21477b6e05e4`。
- 构建上下文 SHA：`736d0cc0ead18b074babfbd51f21b7b0e8f5a2d011907b411aee250e8acc1077`。

构建内实际导入 stdlib 动态模块、Pydantic、HTTPX、执行入口和明确的 Playwright `index.mjs`。推送后以固定 digest、UID/GID 10001、断网及只读根再次导入镜像内应用并读取六个宿主摘要。该回执不等于业务部署或新 Agent 模块准入。

## 冻结输入、PVC 与真实执行

复用此前经过核验的三引擎技术图形及三段纯音原 WAV，重新绑定新镜像 runtime；没有生成讲解、美术作品或调用 TTS。原绘制源码、模块数量和 WAV 保持，快照为 `37b6d75da2f8727f2c92dc928d0a4a209d11cf38b337755d71eb068d551b0ad6`，含四个模块（含转场）及三份 24k／44.1k／48kHz 录音，640×360、24fps、3 秒。

预留预算为最多两个导出 Job（其中最多一个取消专项）、零模型／TTS、零自动重试。运行前发现现有 `video-motion-executor` PVC 为 Ceph RBD／ReadWriteOnce。r1 尚未提交任何导出 Job；保留原冻结，在 r2 为 Job 加入与唯一技术 helper 同节点的 required podAffinity，并将可信 selector 纳入 binding。r2 输入 SHA 为 `bfb33e54de5a758c9d565133b5d229ec3d1fedf93e8f537d04672cde91712ed0`，录音／工程快照及预算没有改变。控制器侧 affinity 字段不改变镜像内固定执行入口；本次资源生成器的更新与镜像源码身份分别记录。

技术 helper 使用同一新镜像，在 UID 10001 下真实调用 `stage_film_execution_snapshot()`，不是源代码覆盖。Job 仅挂精确输入和输出 subPath，输入只读、无服务账号、无外网、只读根、非 root、2CPU／2GiB 限制，fsGroup 保证工作目录可写。实际命名空间原允许策略只匹配旧 executor，未命中新 film app；新增匹配 film app 的双向 deny 后执行。没有更改原部署、Service、域名、业务数据库或任何采用指针。

首个 Job `hf-film-fdd94c0c5dd5395a89ab6406-37b6d75da2f8` 实际以固定 image digest 成功退出，约 38 秒容器运行、42 秒 Job 生命周期。一个工程通过真实 HyperFrames 完成 72 帧和 75 次定位、一次编码及原声混合；结果 SHA 为 `da58c9764472d438bc6c9a44bf6a5ad77cc47b5679886c2db2e736efce1eb24f`。随后在 helper 内重新调用恢复入口，并将 exporter 设置为禁止再次执行，已有结果严格复验并返回，没有重渲染。

第二个 Job `hf-film-d1652fc249fa76833f237252-37b6d75da2f8` 观察到 Pod Running 后持久化取消意图，执行 foreground deletion。另行确认 Job 和 Pod 均不存在，精确输出目录为空且没有 `completed.json`。这是固定资源及 runner 的真实取消检查；不是新 API／业务取消已上线的声明。

## 独立实际媒体核验

取回现成 MP4 和完整回执到既有 D 盘研究目录，独立验证可信快照与结果身份、72 帧、音视频零起点、3 秒精确时长、48kHz 双声道及完整解码。三段 330／440／550Hz 音调、绝对排程和静音窗通过，AAC 解码为 144384 样本，有效部分 144000、填充 384，未截断或变速原 WAV。

MP4 SHA 为 `5071e2047dc1d48ad9d6d263188e1d490178832dd3ca9705e424476c0c6772e5`，独立检查回执 SHA 为 `4e3160fcd894570485ac9dca32ddf21eb75af0f7e782d4584aea32c8fe47d259`。相同技术输入与此前离线导出产生相同 MP4 字节；本次输入归档不含成片，输出目录原先为空，新的实际 Job 捕获、编码与回执另存，没有复制旧成片充当新运行。

完整技术 Job、helper 和本批新建策略已确认移除，PVC 输入／结果仅保留证据。首次清理确认时 helper 尚在终止；随后 Kubernetes 省略空 ingress／egress 数组导致严格字典比较停止。两次原始记录保留，只修正清理检查器的 API 默认值比较后确认完成，没有再次渲染或重开预算。没有测量峰值 CPU／RSS、长片性能或人耳试听，不写效果认可。

## 回归、证据与剩余工作

根任务最终将 snapshot、PVC 执行、Kubernetes／API、组装、HTTP 客户端及旧执行服务一起运行：185 passed、1 skipped。跳过为 Windows 缺少符号链接权限的文件系统检查；实际目标 Job 另按 Linux 环境验证。新旧网关、catalog 和模块合同也已分别通过，重复运行的数字不累加为唯一测试总数。合成客户端／调度 fixture 不记为真实新 API 已部署或新的模型产物已验收。

服务器既有 OPS 下 `hyperframes-film-h3-release-r1/` 保存普通构建回执；`hyperframes-film-h3-job-r1/` 保存 Job、staging、恢复、取消、清理及独立媒体回执。D 盘既有 scratch 下对应 `hyperframes-film-h3-build-evidence-r1/`、`hyperframes-film-h3-job-evidence-r1/` 保存副本，冻结 r1／r2 输入及操作器历史另存。临时记录不加入运行 Skill。

H.3 仍需新 Agent 模块的独立真实产物准入、正式制作持久编排接入、一个完整工程的草稿／预览／下载，以及真实内容的边界与整片审阅。统一 mixer 仍明确 `sound_effects_mixed=false`，不能用本次技术原声或实验室音效补验代替整片音效完成。新的服务配置、业务切换、Skill 发布、开发者认可和主线合入均未执行。
