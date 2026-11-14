# Manim / Anime.js 服务器接入验证（2026-11-14）

本文保留 11 月 14 日各版本的部署与验证历史；这些段落中的“当前”限于当时检查点。11 月 15 日统一资源层的新构建、独立准入与正式消费候选见文末，不能用历史的模块前置暂停状态判断最新实现，也不能将新候选结果当作业务已上线或正常制作入口已验收。

## 能力与发布范围

动画效果库已开放 Anime.js 4.5.0 和 Manim Community 0.22.1。两者均由通用 Agent 自主创作，最后进入同一个 HyperFrames 时间线；平台代码提供时钟、隔离、资源和验证合同。

| 能力 | 作用 | 当前边界 |
| --- | --- | --- |
| Anime.js | 辅助 HTML/SVG、Canvas、Three.js 的运动编排 | 固定本地 ESM 和暂停适配器，HyperFrames 绝对定位；不新增绘制引擎或第二时钟 |
| Manim | Agent 原创 Python 经 Cairo 产生透明 PNG 帧，再由工程消费 | 独立 recipe 角色及离线资源执行；最多 60 帧、640×360 示例；无 TeX/MathTex |

效果库可用、技术通过、实际样片、开发者效果／迁移认可、Skill 发布和正式视频采用分别记录。此次接入不会发布候选 Skill。普通正式视频消费者仍排除需要 Manim producer 的方法，直到其资源再生链单独准入；不能将本次任务的 PNG 包作为通用方法发布。

## 实际部署

- 普通干净 Git 构建；镜像仅使用 `latest`，实际 Deployment 固定 digest，并以标签和注解记录源码。没有运行中覆盖源码。
- 基础镜像与 renderer 来自 `0b6913b53f8af155d4336ed64a5fdca1427bfb8a`；creation、workbench、motion 的兼容性接线来自 `9fab624b8a8029da722198736b727573566c6f37`。
- 四业务部署验证回执为 `authoring-apps-rollout-manim-r1/completed.json`，状态 `deployed_verified`。原项目、冻结记录、媒体、旧凭据与整片配置保持；新增独立 Manim 凭据挂载。
- Manim 角色使用 `36872c3ef70782546d043059e2f405a0ce861ffe` 的完整源码镜像；真实 source-only r3 取得双产物并完成停止与清理后激活，旧角色和数据库记录保留。
- 宿主机访问 Agent Service 受到原网络策略限制。部署预检和原任务查询改为从现有 creation Pod 内走实际业务路径，没有放开网络策略。

## LLM 协议阻断与修复

Manim r2 暴露上游 Responses 返回的未知 token-details 扩展对象被误拒。原失败请求保留为 uncertain，不声称免费或成功。修复继续严格校验主计数和已知数值明细，保留未知 JSON 扩展且不计入主汇总；没有修改模型路由或放宽主计数校验。

隔离分支完整源码 `152cdded8dd5ce2382976b6900f9adad844cf186` 的 155 项回归通过，正常构建并部署 `llm-service:latest@sha256:5ccd604ef305a0d040d6be8f8127b232ad5b6bf734ea98da0d1f1e5ef72f1f5d`。Manim r3 后续六次实际模型请求成功，证明原阻断已解除；不据此改变视频业务任务的费用统计归属。

## 技术与实际样片证据

基础技术准入已分别验证 Anime、HTML/SVG、Canvas、Three/WebGL2 的 96 帧探针，以及 Cairo 48 帧资源和 72 帧 HyperFrames 资源工程。检查包括正／反／乱序／重复定位、空闲稳定、重新加载、半开资源窗口和隔离资源清理。这些探针不冒充 Agent 原创作品或审美认可。

实际业务通过正常效果库 API 分别提交一次初始委托，保留唯一请求、原 Agent 源码、资源、原始 MP4、时钟和停止证据；不手改生成动画。

- Anime 委托：`6b719f65-6aca-4433-95ae-a350f84aa4d3`，目标为“冲击波与群体节奏”。
- Manim 委托：`a66253f0-2e2c-4da8-a367-e5c17166b67b`，目标为“剪切变形中的面积不变”。

Manim 委托已完成：真实 4 秒／96 帧 MP4 SHA256 为 `2bf7ce3ed024eeda3119c654b0c58275f2fd159b4da6d61e9ad71dbc430c4108`。独立核验原 Agent 源码、60 帧透明资源、v5 清单、HyperFrames 0.8.149 和最终解码全部一致；有限边界帧未发现窗口外残留，原任务均已停止清理。高度尺、细线和 b/h 标记偏暗、偏小，不能将技术出片当作目标效果已经获得认可。

Anime 首次委托被 `method_absolute_seek_mismatch` 正确拒收，没有 MP4 或候选。原源码每次重置 Anime 管理的 tween 对象，再执行 seek，破坏了同帧与历史无关的要求。纯状态诊断能复现，原桥接字节未改变；诊断内存里的修改不冒充 Agent 作品。原失败与原预算保留。另以新有限预算、`retry_of_commission_id` 关联同效果，启动修正委托 `5a5ffbd5-a9ae-47a8-b5c9-c53158c8c64b`；仅提供状态所有权反馈，让 Agent 自主重新创作并内化到 Skill 草稿，没有传入手改动画源码。修正稿已完成并独立核验：8 秒／192 帧 MP4 SHA256 为 `5a0bb2a49819f32a8349a7fb363ccca77a0ee4c3a2744780b6fe17d98834ba8f`，原 Agent 源码与实际输入一致，35 条 Anime 轨道承担主体动作；201 个帧／seek 探针与完整 MP4 解码通过。原创方法已写入状态独占规则，没有通过删除验证或手改原动画获得通过。新旧委托在同一个效果下，原失败保留，没有循环新增任务。当前 reviewer 的静帧意见仍不能证明完整节奏、因果动作或审美已经得到用户认可。

原声复用的 workbench／motion 已进一步升级到 `7633be283f3dd8158e1a60479b8fb54646a57f21`、镜像 `video-workbench:latest@sha256:65c3b69d7f1b34c6cc3a6139f94c4d76a12ebb7619d84f03648694eb8663c499`。creation／lab 保持原部署，原整片配置不变；通过真实 HTTP 字段和 readiness 检查。该升级不生成语音或媒体。

浏览器安全检查服务当前不可用，页面验收尚未完成；服务器 API／部署核验不替代页面验收。

## 失败原因展示修复

creation 进一步从完整 Git `aa4de3229cc6abe5f34e27c451c72da9bd156b01` 普通构建并部署为 `video-animation-creation:latest@sha256:70c8903dfb9a90516cd7568bf5135f1ee385e0565fa75af9cec06f8ba6b7e60c`；仅此服务升级，lab／workbench／motion 的 Deployment 不变。相关三套回归共覆盖 115 项，新失败仅从与请求、镜像、宿主和运行库绑定的受限收据精确提取固定诊断码；未知内容返回通用原因，不透出原日志。

真实 HTTP 只读核对原 Anime 失败委托 `6b719f65-6aca-4433-95ae-a350f84aa4d3`，现返回非空 `animation_method_render_container_failed`。原记录没有保存具体 seek 码，因此不回填或声称恢复它。升级及 GET 前后十个 SQLite 账本的原始表结构／内容指纹一致，全部原挂载与凭据元数据保持；没有新增模型、TTS 或渲染。发布证据为 `method-diagnostics-rollout-r1/completed.json`，状态 `deployed_verified`。

## 原声复用与跨镜连续性

原项目 `6f2cbbdc-754a-4069-8c0f-5447cea35973` 的新对照 run 为 `06163975-8b2a-4122-b8ff-ed584c137e9f`，复用原 run `663b838d-091d-4378-8813-586d301f72c2` 的两段 WAV。新增恰好两个模块任务、两个模块版本和一个整片渲染；没有新增方案、TTS、录音版本或独立镜头 MP4 拼接，原项目和旧记录逐项保持。

新 MP4 SHA256 为 `0303bc49c6ebdce34d695316f763358c3b4e3870559d05cab76a0f38eb36de43`，单个 HyperFrames 工程输出 1280×720、24fps、16 秒、384 帧。独立完整视频／音频解码通过，原录音字节与 snapshot 一致；最终 AAC 的两段双声道相关度分别为 0.9999868、0.9999858，RMS 比为 0.99910、0.99916，共 768000 个样点。

第二个 Agent 的输入包含第一个 Agent 原样的完整模块文件、文件摘要及退出状态，最终 snapshot 也逐字保留两份原创源码。独立核验者与主 Agent 实际检查切镜前后第 168–215 帧的连续窗口，以及第 191、192、193 帧原尺寸图。12 个圆片的位置、大小、顺序及点／环／加号／斜线纹样连续，细描边、下方柔影、米白背景、通道底色及边线未跳变；随后开口逐渐扩大，圆片逐步松散，没有换物或黑帧。这支持本轮 5.3 的连续编排与真实边界评价完成，不能推广成任意内容都不会出现断裂。

技术证据为 `appearance-continuity-audio-reuse-business-r1/collection.json` 与独立 `validation.json`、`visual-sequence-review.json`，分别保留原记录和实际观察范围。本样片只有简单 2D 表面，未验收 PBR 材质、任意转场、完整声音理解或真人效果认可。整片独立视听审阅／一次修订及用户追加额度闭环仍未完成。

## 正式制作序列登记上线

工作台从完整 Git `0364cdfb9fdbfae616b4796acde22cb771f25670` 普通构建并部署为 `video-workbench:latest@sha256:6932277da28d033a586eec694d0f05a2ba29e2e1ee7cda46ad05800353eee727`。本轮只以一次 Recreate Deployment CAS 升级 workbench；motion、creation、lab 的镜像及配置保持。

正式制作完成后，以实际成片字节 SHA、不可变 render id、已核验 snapshot、模块与运行身份登记稳定序列；显式复用原声的多级派生继承同一身份。登记意图先写业务库再写独立序列账本，回执丢失按精确 run/version/source 恢复。普通新制作即使媒体摘要相同仍保留独立身份。缺失账本或构造漏接依赖会阻止新付费子任务；已经完成的成片保持 completed，登记问题另记 pending／blocked。GET/list 不登记历史，回收站恢复不重建身份。

本地 68 项整片制作／原声复用／HTTP 集成回归通过；55 项序列账本测试通过。新增 25 个业务用例分为 22 项基础测试、2 项构造护栏和最后一次 4 项窗口验证；后者含三项重复回归，不汇总成一次完整 25 项运行。覆盖 create/bind 提交后回执丢失、CAS 重新冻结、并发、多级继承、篡改拒绝、已用额度保留、缺库与取消／删除。测试的合成输出只证明持久身份及恢复机制。

服务器发布及只读 HTTP 前后，原九个工作台数据库的结构、全部表行及 BLOB 摘要、外部媒体和十个 creation 账本保持。仅在同一 PVC 的 `/data/production-sequences.sqlite3` 建立空的八表账本，已有 Secret、runtime 和 PVC 身份不变。真实两条旧 run 仍为 completed／draft_only／not_reviewed，没有历史迁移或隐式创建序列；readiness、Pod 实际源码及固定镜像摘要校验通过。实际回执 `production-sequence-rollout-r1/completed.json` 为 deployed_verified。

本次部署为零新增模型、TTS、渲染和轮次预占。新片的真实登记尚未用另一付费制作验证，浏览器验收仍受安全检查服务不可用阻断。此切片不执行整片审阅、自动一轮修订或用户追加；OpenSpec 3.5／4.1 整体继续待办。

## 2026-11-15：统一资源层与正式消费候选

完整 Git `ed678aa` 已普通构建并推送六个 `latest` 镜像，源码、构建上下文、镜像标签与 digest 分别保留在 `shared-resource-release-r1/completed.json` 及各构建回执；没有把历史镜像换摘要当作新构建。六镜像构建完成与业务部署完成分别记录。

| 镜像 | 新 digest 前缀 |
| --- | --- |
| agent-service-control | `8ec0291ddfcb` |
| agent-service-pi | `29b64a218eff` |
| video-animation-creation | `f18f663010f5` |
| video-workbench | `5ec54ec7769b` |
| video-motion-renderer | `31a2516d113c` |
| video-animation-lab | `32ce5677ffea` |

新 renderer 的 Canvas、Three.js、Anime.js、Manim 四种既有组合已各执行一次技术再准入，共 144 帧、4 次渲染；原 Agent 源码及原 Manim/Cairo 资源字节保持，新增 Agent、Cairo、TTS 和 Skill 发布均为零。检查结果及新 engine／authoring-library／Manim-resource 准入位于 `/var/lib/video-animation-creation/shared-resource-r1/completed.json` 所列文件。这是方法运行时准入，不能替代正式整片执行或正式消费准入。

Manim 局部时钟的浏览器测试另在同一新 renderer `31a2516d…` 内实际启用，结果为 3 pass、0 fail、0 skip。默认未启用浏览器的 1 pass／2 skip 基线分别保留，不能并作实际浏览器通过。两次浏览器捕获使用同一个合成 descriptor，placement 分别为 0 和 12，各 24 帧、合计 48 帧；正向、反向、重复定位、空闲稳定、全新重载复现与窗口外 hidden 均通过。镜像内宿主、引擎、HyperFrames 0.8.149 和两份 Manim helper 的实际字节已核对，仅只读挂载测试源码。此项新增 Agent、TTS、Cairo、整片渲染和 MP4 均为零，证明定位协议而非 Agent 原创效果；完整回执为 `manim-placement-browser-r1/completed.json`，实际 TAP 为 `enabled-browser-stdout.tap`。

独立资源规划角色 `video.resource-plan.v1` 已准入并激活。三个 Agent 控制服务使用新 control 镜像，API health 为 200，新独立 caller 为 ready；旧 routes、callers、worker 配置、凭据与历史表保持。专用 Secret 为 `video-workbench/video-resource-plan-agent`。激活回执 `resource-plan-activation-r1/activation-summary.json` 是四业务部署前的独立检查点，当时工作台环境变量和挂载尚未接入、四业务 Deployment 尚未变化。角色候选使用空 `producer_catalog`，尚未验证含 Manim 的生产者选择、新联合方案 sidecar 到整体确认的完整链路或策划质量。正式 FilmModule 候选继续使用原已准入角色及 worker 路线，没有为统一版本而切换其执行组合。

正式消费候选复用项目 `6f2cbbdc…`、原连续 run `06163975…` 的第一镜原包和两段各 8 秒的完整 WAV。恰好新增一个 recipe Agent、一次 Cairo 资源执行、一个第二镜 FilmModule Agent 和一次整片渲染，TTS、动画源码改写、生成重试和 Skill 发布均为零。recipe `baeea83f…` 与 module `083a6e25…` 均取得原始产物和真实停止证明；Manim manifest 保持局部第 0 帧，48 帧资源通过独立 placement 放到全片 `[204,252)`。新第二镜 Agent 的原始源码实际以 Anime.js 暂停时间线驱动 Canvas 通道与球群运动，并逐帧等待 Manim 透明层；最终包为 `914f0dbc…`。

新 renderer 的唯一成功整片 Job 输出 1280×720、24fps、16 秒、384 帧 H.264/AAC 视频，媒体摘要 `e44d0a9596ce…`、snapshot `4db3d8fe…`。原 WAV 字节与录音时钟保持，完整音视频解码通过。实际编码第 203、204、216、228、240、251、252 帧与原 RGBA 资源及确切布局进行像素对照：窗内资源主体可见，窗前／窗后背景无残留；各检查区域平均 RGB 误差为 2.00–2.47，已加载全部 48 个原 PNG。这一有限检查证明实际资源消费与半开窗口，不能推广为整片审美或任意内容的质量认可。

新正式整片执行准入 `11137e52…` 同时绑定成功 Job、独立完整媒体核验和该镜像唯一一次运行中取消探针。取消探针原操作器在额外宿主观察中失败的记录保留；同一 Job 的实际 foreground 删除、精确 Docker 停止事件及后续 Job／Pod／进程与 cgroup 缺席补证见 `shared-resource-formal-cancel-r1/reconciled-cancellation.json`（文件摘要 `bd7e53d9…`），没有另开取消 Job 或声称原脚本全部通过。

正式 Manim 消费 v2 准入 `8ce09a00…` 已按原 producer envelope、placement、consumer package、snapshot 和实际媒体构建，并重新读盘校验。创建服务将读取的完整文件为 `/var/lib/video-animation-creation/shared-resource-r1/formal-consumer-admission.json`；对应五件原证据已保留。候选汇总、执行准入和像素检查分别见 `formal-shared-consumer-v2-r1/completed.json`、`execution-admission.json`、`encoded-placement-audit.json`。成功 Job 与 helper 已清理，PVC 输入／结果保持。

候选采用纯工程编译接口保留第一镜旧运行身份与原包，没有改写旧包来通过新运行身份检查。因此这些结果属于独立正式消费候选，不能当作正常制作 API 已通过。资源核心的 revise、placement 与历史保留测试不代表正式修订完成：`ProductionResourceStage` 尚无实际 revise／retry 入口，模块产物尚未纳入共享依赖图，跨镜前任变化后的资源／模块重做也未接正式修订协调器。本段不据候选完成自动勾选 H.8／H.9 或 U.2–U.5，也不改变完整视听审阅、一轮修订、开发者认可和人工发布的边界。

### 四业务部署与正常入口检查

四业务新配置随后已部署并验证：creation 为 `f18f6630…`，workbench／motion 为 `5ec54ec7…`，lab 为 `32ce5677…`，正式 renderer 为 `31a2516d…`；当前四个 Pod 均 1/1 Ready、0 次重启。首次 workbench 启动因沿用方法级 host 身份被正确拒绝，原失败保留；仅为正式路径派生 `runtime_identity.host_sha256`／`admission_id` 并建立新不可变 ConfigMap 后恢复。固定资产、authoring libraries、原准入和候选 snapshot 未改写。新镜像内 UID 10001 的 `FilmProductionSettings` 与消费 v2 context 校验通过，creation 实际 UID 0 读到了完整准入及五件原证据，工作台实际资产摘要一致。原账本行及摘要保持，仅新增空的 resource-plan schema；部署新增模型、TTS、渲染和 Skill 发布均为零。回执为 `formal-shared-consumer-v2-r1/deployment-plan/final-r1/completed.json`，状态 `deployed_verified_after_configuration_projection`。

正常 ProductionRun API 的唯一 POST 已完成 run `6cc51e62-25c4-40f9-a38d-784e831b209c`，render 为 `003a4231-bd55-49f2-884a-9640e1ef3081`。实际新增一个 recipe、一次 Cairo、两个 Canvas module 和一次整片渲染；TTS、新联合方案、新资源规划、自动重试及 Skill 发布均为零。它复用旧联合方案与完整原声，显式提供 `resource_plan`，`resource_plan_source=None`；因此只证明正常 run 的共享资源制作／消费，新导演 sidecar→用户整体确认及含 Manim 目录的策划质量仍须独立验收。只读采集器对 BEGIN 标记的子串计数失败保留，补充采集按完整行锚定验证原源码与完整前驱，没有改写作品或重新制作。

本次正常 MP4 为 `73613008a338a0c5382b5b65ceb0fc666fe631b31c7a9fd3582226166126f7e3`，snapshot 为 `c8646b0c…`：16 秒、24fps、384 帧，完整音视频解码通过，374 个不同画面。两原 WAV 字节完全复用，最终双声道与原音频相关度分别为 0.9999868、0.9999858。独立证据位于 `shared-resource-normal-business-r1` 及其 `independent-media-r1/validation.json`；浏览器实际观察资源 0/1→1/1、编排、渲染及草稿待审阅，原生播放器完整播放至 16 秒，1280×720、readyState 4、无媒体错误，下载链接指向该 render。播放与入口可见不代替实际点击下载、旁白时序或审美认可。

正常模块没有 import／call Anime.js，H.8 继续待办，不把候选里的 Anime 使用或统一运行库已加载移记到该 run。第二模块确实通过 Manim helper 消费局部 0 帧起的 48 帧资源，但安排为全局 `[192,240)` 的右上角 240×135 缩略图并淡入淡出，第 192 帧透明；实际查看第 200 帧确认这一点。原目标是让资源参与主通道边界展开，缩略图属于表达偏差，不能将“实际消费”写作目标已经达成。没有为弥补该偏差追加生成、打磨或改写原源码；U.2–U.5／H.9、完整视听和人工认可保持独立待验收。

正常 MP4 的追加像素审计保留为 `pixel_evidence_not_proven`：第 204／216／228／234 帧最近的原 PNG 分别匹配局部 12／24／36／42，198 帧最接近局部 5 而非预期 6，原因未定。五个不透明采样帧强 alpha 区域的平均误差为 7–8.6，支持缩略图进入实际编码；首帧透明及边缘淡入淡出导致其余采样难以分辨，不能据此宣称全窗口逐帧精确证明。原测量、阈值及未达标状态保持，见 `encoded-resource-r1/audit.json`；没有重新编码。

### 现有内容按画面创作与动作编排整理

新效果库浏览器回查曾发现旧 Manim 样片漏标，Manim 筛选显示 0/10。根因为 creation 的历史读投影错误地要求旧 descriptor 的 helper 等于当前 helper。完整 Git `6a02aa008b4a38412b1e2eec375aa745a318c28c` 仅修该读投影，并以正常构建的 `video-animation-creation:latest@sha256:ca5d0dc041bdc8a68f701e8e34fb16dde34002cf76b1cb3ffb799cb580a9b351` 单服务部署。旧样片按自身冻结 helper 库存、descriptor 摘要和真实加载观测分类；新生成仍严格使用当前 helper，producer、consumer 和执行准入未放宽。干净源码定向回归 32 项通过，另用原样片只读复现修复前无标签、修复后 Manim 0.22.1。原参数、挂载、init、凭据和 renderer 保持；Pod Ready、0 重启及真实 gateway／Manim 能力预检通过。

用户随后要求专门子 Agent 整理已有数据。已逐条核对 10 个效果、22 条可见实验与22个唯一已成片候选；4 条无可验证样片实验保留待确认。实际分类为 HTML/SVG 3、Canvas 6、Three.js 1、Manim 1，Manim 样片同时保留 HTML 承载标签；动作编排 Anime.js 1、GSAP 0，没有将 HyperFrames 内部时钟推断为作品使用 GSAP。所有分类来自原冻结样片及实际加载证据，不按标题或请求推测，也未改写原记录或重做媒体。

修复发布后，独立真实 catalog GET 与逐条盘点完全一致；浏览器也逐项选择六种筛选，确认 HTML/SVG 3/10、Canvas 6/10、Three.js 1/10、Manim 1/10、Anime.js 1/10、GSAP 0/10，Manim 卡片显示 HTML/SVG 与 Manim 0.22.1。三库20张表、1105行前后完整指纹一致；没有迁移、数据库写入、新实验或发布。回执为 `historical-manim-projection-{release,activation}-r1/completed.json`；D盘现有任务目录的 `existing-library-capability-classification-20261115.json` 与 `existing-library-live-classification-20261115.json` 保留逐条分类及真实核验。源码、数据核验与浏览器结果分别留证，不改变完整视听和人工发布边界。
