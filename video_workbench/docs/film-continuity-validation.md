# 整片上下文与相邻候选交接

本轮推进 OpenSpec `evolve-video-agent-creation-and-effect-library` 的 3.4。
代码完成、真实生成、连续画面观察和用户认可分别记录；本文件不把协议声明当作画面证明。

## 新制作行为

所有旁白 WAV 完成并核验后，整片制作冻结一次全片上下文：总稿、理解目标、风格、段落顺序、准确录音版本和 SHA、实测时长及逐帧排程。尾帧补齐计入保持时间，不拉伸旁白。

逐段创作使用独立 `video.animation-production.v2` 角色、input/result v3 和 creative-scene v4。后段只接收上一确切候选的源码摘要、方法与对象／外观／相机／起止状态交接；不递归嵌套此前完整任务。对象声明须能在实际返回源码中定位，但源码锚点与作者声明仍不等于视觉验收。

这些信息帮助通用 Agent 自主延续表达，不规定布局、动画动作或转场模板。编排、候选准入、快照和隔离渲染核对全片身份、顺序、真实时钟和确切前候选。总稿、风格、顺序或其他段落变化会使旧链失效，历史仍保留。刷新和重启继续同一冻结上下文与子任务，不重新发起付费生成。

旧制作记录继续原有输入、资源包、提示词摘要和路由身份；新协议使用新资源包和独立技术准入。整片候选仍为草稿，不自动采用录音或画面，不写人工时序核准。

## 工作台状态

当前有效整片制作的确切候选可以显示“草稿画面／配音就绪”，并分别显示人工采用状态。历史下拉不会改变当前分镜的制作状态；失败、过期、未确认版本对不投影成当前成品。整片视频实测时长来自渲染结果，录音加留白的排程单独说明，完成状态去重。

## 离线验证

- 前端状态、整页启动和 v4 入口检查 109 项通过。
- 新旧 Python／Node 渲染边界、音频时钟和冻结 Job 检查 77 项通过；拒绝跨版本编译器、错前候选、错误段落和同总时长下的留白挪位。
- 编排恢复、确切候选链及改稿失效专项 24 项通过；与其他组合检查存在覆盖重叠，不累加为唯一总数。
- 新旧创作合同、独立角色、构建资源、候选与 Host 专项由对应回执记录；这些离线测试不创建真实准入。
- 编排恢复、精确候选链、改稿失效及最终快照须与下述真实运行证据分别核对，不能由合同检查代替。

## 真实候选与独立准入

`candidate-film-r1` 已通过通用 Agent／Pi 的独立 `video.animation-production.v2` 生成真实候选。原始任务为 `16021586-f19e-4840-9fb3-337c4d796bcc`，attempt 为 `19ff8585-c46c-4cdb-8937-4929eb3d39f3`，实际调用模型 4 次；原始输出、完整输入、整片上下文和工作镜像身份均绑定到回执。worker 与 proxy 均正常退出、零重启。此阶段没有修改生产路由，没有自动采用、人工认可或方法发布。

`film-render-r1` 使用该原始 v4 输入完成隔离渲染及独立观察，得到 13 秒、24 fps、312 视频帧的单段 MP4。媒体 SHA 为 `c230d7decb45c5b58f524bd47ce0579bfa14e09c888d44f6ae0414d889540a16`。输入为真实 24 kHz WAV，其 312000 个 PCM 采样不等于视频帧数。HTML／SVG 原生上下文、反向定位和静止时钟检查通过，prepare／renderer 均正常结束、零重启。

上述证据只证明带整片上下文的一个真实片段能够创作、渲染和技术检查。回执明确记录 `audio_observed=false`、`whole_video_watched=false`，画面及连续性质量均为 `not_evaluated`；音频对齐仍为 estimated、人工时序核准待办。不能据此声称已观察跨段连续性或已获得审美／音质认可。

`film-admission-r1` 以独立 `video.animation-production-contract.v2` 套件完成新角色的技术 Host 准入，绑定 SHA 为 `cd0fe8e0df1b485af56bc60d86f1ac9ca14e2db277a1863b029f68e4e7e92304`。该准入阶段 `accepted_host=true`、`enabled=false`，未改原始证据，未创建部署、模型请求或 UID 分配；它不继承旧角色的准入或认可，后续启用另见发布记录。

## 完整源码发布与运行时核验

开发分支版本 `df8dc6026b8151d2e28a833edc2547c40a3c8391` 已推送，并由服务器完整 Git 源码及普通 Dockerfile 构建四个镜像，没有增量源码覆盖。源码文件清单 SHA 为 `98814b254ebf437f3c22cd062254bdbb3280f103ccf47d9dab84b72363947fce`。全部使用 `latest` 加固定 digest：

| 镜像 | digest |
| --- | --- |
| control | `sha256:cdc44ab7a9cdb19dfa9d0f91aa49c6c9de280b8b10cc725d9e8ea8c32e1f8ff9` |
| worker | `sha256:668c37917c667899a9ae438fd9c8b38966175e5cfd58468941d82d777006b561` |
| workbench | `sha256:1df9c7a4816482b5e213de84447a8a938602c4ae399fdb8e92d2e933bb23266c` |
| renderer | `sha256:9de41e53f826238afb57482c2e9f26142d6e245604e462b91296ec71f89a9c5c` |

motion executor 复用 workbench 镜像。实际更新为三项 control 部署以及 workbench、motion executor；worker 与 renderer 用于后续隔离任务。本轮没有部署实验室、创作服务或 speech-service。

`film-native-renderer-r1` 在这一确切 renderer 中重新核验 HTML／SVG、Canvas 和 Three.js：每种引擎实际捕获 24 帧、20 次探测、19 个不同探测帧，独立捕获确定性比较、原生上下文、反向定位和静止时钟均通过。能力文件原始 SHA 为 `4b0f4a432dedc8cea1df3ee8b01d2a106ba68eec32fbc395e340852276518de3`，准入 ID 为 `film-native-renderer-r1-570473e46dc6835f`。这些是手写的原生协议夹具，不能作为 Agent 作品或审美样例；没有复制旧能力身份来代替新镜像核验。

`film-activation-r1` 已完成实际启用和重开入口：复用现有 `video-animation-production` caller 与凭据，加入 v2 scope，并绑定新角色的确切已准入 Host／worker。永久保留的 UID 范围起点为 `303400000`、数量为 65536；没有新建凭据、修改 Vault 或执行 `init_db`。

入口关闭期间的 `before-reopen-verification.json` 已确认完整数据库校验、历史 catalog／Host 及无关部署保留，`data_preserved_before_reopen=true`。随后 `apply-complete.json` 记录入口已恢复；其中同名字段为 false 表示此回执不再属于关闭入口前的检查阶段，不表示数据丢失。API、dispatcher、model-proxy 的实际 imageID 与上述 control digest 一致，workbench／motion executor 就绪。启用过程本身没有创建模型请求、制作任务或人工认可。

## 发布检查中的问题与修正

完整 Agent 数据库只读校验的原始 helper 在 model-proxy 的 1 GiB cgroup 内于 71.555 秒后退出 137，无 Python traceback，`oom_kill` 从 1 增至 2；主服务未重启，数据库未写入。诊断记录为 `film-database-diagnostic-r1.json`。

专用 v2 operator 仅将 SQLAlchemy 流式查询设置补为 `stream_results=True,max_row_buffer=1,yield_per=1`，限制驱动预取。SQL 排序、逐行遍历、UTF-8 长度分隔、SHA 计算及完整性检查均保留；没有抽样、删数据、提高部署内存限额或覆盖共享 helper。完整数据库校验随后通过，发布继续完成。

另一个阻塞来自停止状态检查只识别 `document` 列，而 motion executor 的实际 `motion_operations`／`motion_scenes` 使用 `state/result/error`。空检查列表触发断言，不能据此判定存在运行任务。专用 v3 operator 对这两张无 document 表按真实 schema 检查：只接受 completed／failed／cancelled／timed_out，拒绝活动、未知、cleanup_pending 或未确认清理状态；completed 必须有字典 result。终态历史中的 `motion_submission_unconfirmed` 文本须结合已完成的清理 fence 判断，不能仅凭文本误报。原 workbench document 检查保持不变。该分支 18 项独立 SQLite 检查通过，基础启用检查 30 项通过；二者不计作业务连续性验证。

## 浏览器检查与真实三段制作

发布后已在浏览器核对原有 29 秒制作记录：各段“草稿画面／配音就绪”与人工“未采用”分开显示，完成状态不再重复，视频实测时长正确显示为 29 秒。这验证已发布界面的状态语义；原记录属于此前 3.3，不能证明新跨段交接效果。

在项目“联合策划验收｜从总稿到表达段落｜2026-11-12”（`ccd99f57-234a-4825-bfe5-62af37918e3d`）从工作台只提交一次新制作 `212bc132-042a-4bb9-9cb1-fffdbb64caab`，现已完成。三段统一云健，实测 WAV 分别为 13、10、6 秒；所有录音完成后顺序创作三段 HTML／SVG，再合成 29 秒草稿，没有自动重试或新增另一制作。

新整片渲染为 `b78f7e7f-5570-4019-b09a-4e9641fe6c12`，原片 SHA 为 `61ef162a83729d6499675fd6153bbd781577b12219d2cf7bf3a67a60a656a8ea`，冻结 film SHA 为 `d1918c819a1ed165bd3b2d201829452c207bc607c7fddae0a48e1c3f5a111df2`，最终快照 SHA 为 `e871adb76f1e2fdd325d6536e8b3d52b66deef3e6ffd5d2cc5c56f1cf65b2ec6`。三段实际候选依次为 `3be66ac8-5d39-4861-a150-e280c7cf367c`、`c18dc9e6-513f-4f00-bf22-df64d8e77e6e`、`3e29e40c-2ed4-4a2c-9365-ae99d7eac888`。

只读验收核对了同一冻结 film、每段确切前候选及其输入／结果／源码／handoff 身份、WAV 身份和时长、最终快照及实际 executor／renderer 回执。采集使用 SQLite `ro`，没有写业务记录或启动生成。采集回执的零模型请求仅描述验收操作，不表示原始三段制作没有调用模型。

网页原生播放器已播放到 29 秒结束，`readyState=4`，没有媒体错误。切换历史制作前后，当前分镜状态文本保持一致；恢复新制作后草稿就绪与人工未采用仍分开显示。播放器走到结尾只证明播放链路，不充当实际完整试听、审美认可或时序核准。新候选仍为 `draft_only`／`not_reviewed`，未点击人工采用、核准或正式导出。

## 两处边界的实际画面观察

从上述确切原片提取两处边界前后一秒的全部 24 fps 连续帧，分别为帧 288–335（12–13.958 秒）和 528–575（22–23.958 秒），每处 48 帧；另保留 7 张较大关键帧和两秒片段。主 Agent 已观察两份完整联系图及关键帧图，以下是逐帧画面观察，未声称听过音轨或已获得用户认可。

| 边界 | 实际观察 | 限制与待改进 |
| --- | --- | --- |
| 13 秒 | 轨道、执行器轮盘、青色缺口圆片与橙色圆环的身份、位置参照和配色延续；“假设”出现后，两请求沿提示轨迹逐帧回到例子起点。 | 这是已确认策划中的显式重置，不描述成同一运动无缝向前延续；轨道刻度和标签明暗仍有细部差异。 |
| 23 秒 | 橙色圆环从轨道进入执行器后，下一段保留正在处理的状态；右侧已处理的青色请求及轮盘位置保持。 | 轨道刻度、边线与标签明暗在边界有轻微跳变，不能宣称画面完全无缝。 |

本次策划明确采用固定侧视图。此前本文件把“视角变化”列成 3.4 必需验收条件不准确，已按原始任务与已确认方案纠正。本次没有验证换视角、跨引擎衔接、长片规模或完整声音效果。

据已发布代码、真实三段精确交接及上述实际边界观察，**3.4 核心上下文与状态交接完成**。5.3 继续待办，保留边界细部稳定性和更广范围的真实连续性评价；3.1、3.5、3.6 及声音／用户验收等任务不随之完成。原始只读技术回执保留 `visual_continuity=not_evaluated`；后续观察独立写入 `boundary-visual-observation.json`，不覆盖原始回执，不把技术观察伪装成用户认可。

OpenSpec 已按上述证据同步，仅新增完成 3.4，其他任务与规格正文保持原样；revision 为 `5802167b8667efdcafb363187f7ae89e1c31556a`，精确回读一致、`validate_change` 通过。

## 证据位置

运维回执以服务器既有 `creation-product-r1` 工作目录下的 `candidate-film-r1`、`film-render-r1`、`film-admission-r1`、`film-context-release-r1`、`film-native-renderer-r1`、`film-activation-r1` 为准；本地汇总为 `film-release-receipts.json`。新三段完整证据在同一服务器目录下 `film-run-acceptance-r1`，取回的原片、联系图、只读回执及独立观察集中在 D 盘既有证据目录的 `film-live-run-evidence-r1`。OOM 及 journal 修正的独立 helper／检查记录与汇总放在同一临时证据目录，不纳入应用构建输入。

测试和运维临时证据集中在既有 D 盘 `video-agent-development-20261112`，不向 C 盘下载新媒体。本轮保持开发分支，不合主线。
