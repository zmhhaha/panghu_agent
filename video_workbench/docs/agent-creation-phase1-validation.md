# Agent 创作阶段1：观察、独立审阅与单次修订

2026-11-11更新；开发分支 `codex/video-capability-experiment-redesign`。当前：r4完整Git源码构建已完成，通用Agent Control、review v4执行环境和动画创作执行器已上线；开发者入口为 `https://video-service.panghuer.top/dev/animations/`。网页只提交一次的 `held_out` 制作 `c91c2150-1ecc-423c-8968-1948a0b7f276` 已完成初作、渲染、一次独立审阅、一次修订及技术复验。两版8秒媒体的摘要、独立完整解码和浏览器播放通过；重新打开页面后原request/run、账本事件、任务、媒体及额度保持，未新增执行。OpenSpec仅新增完成1.4“有界基础”，未执行生产服务重启故障注入。review v4候选的一次 `read_inputs()` 及24帧原始图片传输通过，但预先登记的E问题仍未识别，1.3保持未完成。尚未合主线；本次是开发者动画创作入口上线，不代表普通整片工作台已切换新导演流程或用户审美已认可。

以下首轮（p1）与时序审阅第二轮 `sequence-review-r2` 保留为历史记录。第二轮23帧实际图片传输、一次Agent修订、8秒渲染技术检查及账本重放通过；新增发现并修正了抽查时刻的C状态遮挡，但仍漏检预先登记的E靠近说明问题，修订片2.5秒的固定说明仍不可见。当时只有隔离真实HTTP准入边界补证，完整付费提交链及阶段1整体尚未完成，现有工作台流程未切换；两轮原结果及失败均保留。当前r4发布和网页制作证据见本文末尾。

## 本阶段实现

- 新增通用 Agent 的三个版本化角色：`video.animation-observe.v1`、`video.animation-review.v1` 和 `video.animation-create.v2`。旧动画 v1 合同及资源保持原含义。
- 参考观察与独立审阅接收真实媒体摘要、有序时间窗口及图片文件。模型可以提出学习片段或具体画面问题；画面问题绑定时间段、帧、源码版本和修改目标。
- 在共享模型代理记录实际发送图片的摘要、类型和长度，只接纳已完成成功上游请求的传输证据。角色结果的“供给帧列表”不能冒充实际模型输入。
- `animation_observation.py` 从本地媒体真实解码采样，明确未采样运动、首尾范围和没有音频等限制；超出预算要求选窗口，不静默只看前几帧。
- `AnimationRevisionStore` 使用独立 SQLite 保留制作序列、审阅、修订、技术复验及事件。原子预占后才能外部提交；不明提交只核对原任务，失败、取消或重启不退回已消费额度。
- `AnimationPolishCoordinator` 推进一次独立审阅、有问题时最多一次创作者修订和一次隔离技术复验。不自动发第二轮模型审阅，不写用户采用或审美认可。
- 候选目录准备保持新角色禁用；镜像增量保留现用 Pi 的旧资源和未知共享改动。候选实测与正式业务 HTTP 入口的上线验收分别记录。

## 首轮代码验证

实现提交 `9253513d7669f5e1a5f333f40a34a3b4493de6f1` 的联合检查为502项通过，覆盖新角色、共享多模态证据、注册与候选协议、观察、网关、持久账本和单轮编排。增量镜像、运行时及各模块的额外检查与联合范围有重叠，不累加为一个测试总数。

这些检查使用模拟模型／传输和测试媒体，不作为真实模型已经理解画面的证据。自由源码合同的600秒上限不代表现有实验渲染器已经准入600秒；本次实际渲染仍限定为已支持的8秒 HTML/SVG。

后续兼容及候选导入提交为 `3bb46779539b2a0ec4eb659684e8aa7c0892dc6e`。增量镜像及相邻范围231项通过，其中overlay为21项；候选导入及账本／观察／网关／编排153项通过，其中导入为40项。范围有重叠，不与首轮502项相加。

## 首轮（p1）真实候选及预先登记的验收

复用上一轮真实 Agent 的群体筛选统计首作，原媒体SHA256为 `d8e2bfdffb156495b0a61f385c7b1c6ca7fb81899b67700e3d7827e602eaa012`，原源码SHA256为 `f56af470298076e0abd646324fe3022def1b373e7be41b22c81f25499edfa273`。

预算为参考观察1次、独立审阅1次、有问题时修订最多1次；候选各最多一次连接探测。已知工程观察是E卡片在约2.2～2.8秒靠近固定说明文字；该问题和本段记录不提供给独立审阅者。实测要核对能否从画面中提出具体可操作问题，不能用任务成功或空问题列表替代能力验收。

原0秒起始、3fps网格的24帧恰好未显示最接近的中间时刻。调用模型前另存0.15～8秒、3fps的24帧证据，并保留原网格；新输入SHA256为 `1b1ca4dd34a0cdfecf86e334b6ff7cda02590a9e8bd6add714d2f416e7c78911`。这是针对已知短问题选择可观察输入的预检，未覆盖最初0.15秒，也不代表完整播放、听到旁白或完整运动理解。

统一运行材料在忽略目录 `video_workbench/rendered/feedback-20261106/animation-lab/agent-creation-phase1/`。服务器材料留在同一阶段目录；共享凭据和私有模型响应数据库不下载、不写入Git。

## 首轮（p1）运行来源与镜像

沿用用户的 `zmhhaha/pi-cage`：固定提交 `e0262e65e43c6924164e995394f715dc5d19c210`、Pi `0.99.2`。多阶段构建从pi-cage镜像取得Node、Pi包、Worker入口和身份元数据，再装配通用Agent宿主、浏览器／Python等依赖及动画角色；不是把交互式pi-cage服务容器直接当成视频服务。精确来源可由现用donor的`io.panghu.agent.pi-cage.source/revision`标签核对。

候选镜像为 `arm-cluster-master:5000/agent-service-animation-review-pi:latest@sha256:794a8ed067d307a736ea97b524fd66c5095612f10d1710298695a76272cca908`，构建源码为上述3bb4677。沿用的donor为 `agent-service-animation-pi:latest@sha256:bba22ca46b6c78eb7328b436826a7662e094cd49e1fba9af30e0c2b6976633cb`。新镜像保留其运行配置、旧资源及其他共享逻辑，九文件最小补丁均有逆向字节恢复证明；旧配额100和旧资源定位保持。角色目录准备为禁用状态，未写入现有生产路由。

## 首轮（p1）真实参考观察

首轮预算中唯一一次参考观察候选已完成，Worker／代理均退出且零重启，镜像身份、任务／尝试／输入与停止证据对应，专属临时对象已清理。16张原始JPEG全部在成功模型请求中找到匹配SHA；候选共22次模型请求，包含连接探测及任务内部的工具往返，不是22轮创作。

输出从画面正确描述五个对象、未知值、B／D／E筛选重组、人数与已知值分母的区别，以及均值和加权均值的变化，提出三个可学习表达区间。它仍是抽样帧的视觉分析：不等于完整视频播放、连续运动理解、听到旁白或用户认可。独立审阅使用另一组24帧，未提供预先登记的工程问题。

## 首轮（p1）独立审阅：实际传输通过，预定问题识别未通过

首轮预算中唯一一次独立审阅完成，24张供给帧全部在成功模型请求中找到匹配SHA。审阅提出两项绑定具体时间、画面依据与修改目标的问题：A值为2但筛选过程中仍显示“保留”，状态含义有歧义；分母及未知值说明字号小、对比度不足。

审阅没有识别预先登记的E卡片约2.2～2.8秒靠近固定说明文字的问题。即使返回了其他可操作意见，也不能据此勾选“识别已知可见问题”的验收。模型调用前选择的偏移采样网格以及原网格均保留，没有再发第二次审阅。回执为 `review-assessment.json`，其中 `predeclared_finding_identified=false`。

## 首轮（p1）唯一一次Agent修订

创作者依据实际审阅意见完成一次修订，保留数据、对象和8秒时长，补充“初始成员”语义说明、1秒后更新A的排除状态，并放大／提亮分母和未知值说明。初始1秒的“保留”标记属于冻结输入约束，修订未为迁就审阅而破坏它。修订由Agent编写源码，未由开发者手写动画补丁。

修订源码SHA256为 `a0c2d7d13958ae508cd57e29ff2e0a50f624fce11dab91ed25ed0dd5508bacb7`。参考观察、独立审阅和修订分别为一个真实任务；对应模型请求数为22、30、4，包含连接探测和任务内部工具往返，总计56个请求，不是56轮创作。三个候选的Worker及模型代理均正常退出、零重启，专属临时对象已清理。

## 首轮（p1）候选导入、单次渲染与持久恢复

停止后的真实候选通过证据校验导入独立账本，执行路径明确为 `isolated_candidate_import`；制作序列为 `a996ed02-6625-4a8d-b7f1-b9b98a8cb358`。导入核对原输入、结果、运行身份、停止与图片传输证据，没有伪造历史HTTP提交。账本接续技术复验时使用拒绝所有Agent操作的网关，未再次提交模型任务。

唯一一次隔离渲染输出H.264、1280×720、24fps、8.000秒静音MP4。服务器技术检查覆盖192帧和3130项独立数值核对，问题列表为空；当前范围内的数值、身份、SVG几何及反向归零检查通过。本地 `ffprobe -count_frames` 实读192帧，完整解码通过。渲染使用既有实验器的Chromium定位截帧与FFmpeg编码，不是HyperFrames导出；本次适配仅验证8秒HTML/SVG，现有实验器上限10秒，不能扩写为600秒自由源码已准入。

MP4 SHA256为 `48eb3df1edf89927b069ac6dff424e68af8d5ac0ca5fb6b53bea671fec3db556`，可查看[8秒修订样片](../rendered/feedback-20261106/animation-lab/agent-creation-phase1/verification-run/work/output.mp4)。同一账本重开后重复推进返回原完成结果，未新增Agent提交或渲染；这不是完整故障注入恢复验收。渲染容器已移除并确认不存在。

主要回执为上述统一目录中的 `import-receipt.json`、`render-adapter.json`、`verification-step.json` 和 `verification-run/render-outcome.json`。候选导入与恢复通过不代表正式HTTP提交前的真实额度控制通过，回执保持 `live_http_verified=false`。源码版本、媒体摘要和技术检查通过也不代表漏检问题已修好、画面审美已认可或用户已采用；本轮无音频，不评价语音自然度及音画同步。

## 首轮（p1）已保留的准备失败

首次构建在归档清单检查失败：脚本导入产生三个Python缓存文件，原源码字节未缺失。已保留原目录并改用新干净目录及禁用缓存写入，严格校验不放宽。第二次构建因现用Pi的共享源码上下文与仓库基线不同而停止；按实际donor做受控AST拼接和其余精确补丁后重建通过，未整文件覆盖共享逻辑。

一次独立离线图片探针因原始证据文件权限拒绝而停止，未处理图片或调用模型；未重试、未据此判断候选有问题。后续参考观察走真正的候选输入通路，实际16帧图片传输已通过。目录准备还暴露纯科研合同导入缺项；从同一Git提交的独立支持归档补齐三个纯合同文件后通过，没有修改科研服务。

## 第二轮（r2）：时序审阅与提交边界，已收尾

本轮实现提交为 `acb203bf8cd863278a7e793a8631025fe358aefb`，新增显式选择的 `video.animation-review.v2` 与独立资源包 `animation-review-v2`；资源清单SHA256为 `ce0ea7908798bcca99c7effc5cf6cbc3f3c9166e12cfadf2bae50a5d862fe77c`。旧v1提示词、资源及默认三角色目录保持，新v2角色须明确选择，候选准备时仍禁用，不扩展旧调用者权限或预留UID。v2关注按时间发生的中间状态、运动对象与固定内容的关系、短时拥挤／遮挡、身份和可读性；靠近本身不自动判为缺陷，也允许问题列表为空。提示词没有写入本案例的对象名和预定问题时间。

分层采样在概览之外为指定窗口提高时间密度，以真实解码测得的归一化PTS绑定帧；同一PTS的重复请求合并，不能因为两张图片字节相同就当成同一时刻。旧网格采样路径保持。审阅角色与版本同时冻结到新操作及后续阶段账本；旧未标角色的操作仅能按v1／revision1恢复，不改旧文档字节，不能以相同输入摘要跨v1／v2切换。

提交前准备将输入和ready核对放在持久提交claim之前，实际POST必须在唯一claim之后；未知提交仅查询原幂等键。隔离的真实loopback HTTP探针已通过：实际创建1个排队任务，在服务真实返回202后由客户端丢弃响应，重开业务账本和客户端后仅GET恢复同一任务；取消仍消费额度，终态重放无HTTP请求、账本不变。探针使用合成ready门禁，未启动dispatcher／Worker；Worker尝试、模型请求和渲染均为0，control API未重启。它证明该隔离准入边界，不能代表实际Worker就绪、模型／修订／渲染全链或完整工作台验收；生产配置、路由和acceptance未改。回执为 `sequence-review-r2/http-admission-receipt.json`，`full_paid_chain_verified=false`、`full_workbench_verified=false`。

### 本轮输入、预算和执行身份

统一材料目录为 `video_workbench/rendered/feedback-20261106/animation-lab/agent-creation-phase1/sequence-review-r2/`。输入沿用首轮原始媒体与源码，目标原文复用；没有复制旧候选或旧账本。概览声明窗口0～8秒、1.5fps，局部窗口1～4秒、4fps；准备结果为23张JPEG、987048图片字节、0个别名，实际归一化PTS从0至7.333333秒，完整时刻列表见 `preparation-summary.json`／`review-original-temporal/sampling-plan.json`。声明0～8秒窗口不等于覆盖末帧或完整播放。输入SHA256为 `d1896c0f5ebbbfb7ae12580258c58b6e7f1136cad73a11b93866a22bbf680bd0`，采样计划SHA256为 `e87823a2f1882b081a0b964920991a49dc5d65038354e1937b50a2550fb83c10`；之后已核对23帧全部进入成功模型请求，不仅是准备文件存在。

模型提交前已另行登记预算：参考观察0次、独立审阅最多1次；仅当这一次审阅提出具体、证据绑定且适用于冻结目标／约束的问题时，创作者修订最多1次，随后最多渲染／技术复验1次。不发第二次审阅，不自动重试，不重新打开首轮预算。预定验收仍为E卡片约2.2～2.8秒靠近固定说明的问题，仅保存在operator材料，不进入模型输入；这是已知案例回归，不能宣称盲测泛化、完整运动或音频理解。

Worker复用 `arm-cluster-master:5000/agent-service-animation-review-pi:latest@sha256:38731ff5003aa80d4a98acd9cc77caa1dbe004cac18185509b3d09d51aeabb40`，其构建源码为 `acb203bf8cd863278a7e793a8631025fe358aefb`，仍沿用上述pi-cage来源。后续业务导入器显式候选目录支持已提交为 `c1568e99106ab5be4b5936fde2c11e5d73065c05`，定向检查72项通过、1项Windows符号链接检查跳过；已在Linux服务器实际补验该符号链接拒绝路径。服务器业务源码归档独立保存，176个 `agent_service` 文件与镜像构建归档逐字节摘要相同，因此没有重建Worker；不能把业务归档提交号写成镜像构建提交号。

本轮最初 `review-p1` 在候选名称碰撞检查处停止，发生于Secret创建之前，未实际调用候选模型，不计为失败模型审阅或第二次审阅。之后使用新的 `review-p2`／`create-p2` 运行身份和UID范围，未移动旧目录、删除历史预占或给旧结果重标身份。首轮镜像、漏检和失败记录均保留原身份。

### 真实审阅、单次修订与尚存问题

唯一一次v2审阅完成，23张实供图片全部通过成功模型传输校验。返回两项具体意见：D在2秒时遮住C右侧的“未知”状态；A在筛选过程中仍标为“保留”，与阈值和所在区域冲突。独立查看原始1.75／2.0／2.25秒供给帧确认C的状态先可见、后被D覆盖、再恢复，因此此次确实发现一个短时遮挡；不能据此推断精确遮挡时长或全部运动。

审阅仍未提出预先登记的E卡片靠近固定说明问题。原2.5秒供给帧确有该现象，不能用新增C意见替代既定验收。回执 `review-assessment.json` 继续记录 `predeclared_finding_identified=false`，1.3未完成。本轮为已知案例回归，不宣称盲测泛化，也未再调用第二次审阅。

唯一一次创作者修订由Agent生成源码：B／D／E先横向离开原列，再纵向归位；A在筛选开始时更新为排除，保留原数据、8秒时长、原布局及统计更新约束。源码SHA256为 `b14142f466e92e282d828ef0ed4a0733b859c3c97dc637985170df9e38c00000`。审阅／创作分别为27／4次模型请求，包含各一次连接探测和任务内部往返，合计31个请求，不是31轮制作；真实任务各一次，observe为0。

### 渲染、重放及技术边界

停止后的两个候选校验并导入独立账本，lineage为 `a880fe31-c947-4f6a-b16b-657932ae30bc`。唯一一次隔离渲染输出8秒H.264、1280×720、24fps静音MP4，192帧和3130项独立数值检查通过，输出SHA256为 `7ab6cad1b9f6318f773dc6e220fac3e2f5076c4ca367900c4db2f74b9effdbdf`。本地实读192帧并完整解码通过；仍使用Chromium定位截帧与FFmpeg，不是HyperFrames导出，不代表600秒或新引擎准入。

从已编码MP4实际解码的1.75／2.0／2.25／2.5秒四帧中，C状态均可读，A均显示排除；这只确认这些时刻的变化。2.5秒时，E占据固定说明先前所在区域，说明不可见，作为尚存画面问题保留；没有把候选记成用户认可或采用，也不声称全时段无遮挡。实际抽查回执为 `local-media-check.json`，这一步仅解码已有MP4，未再执行场景或渲染。

重开账本重复推进返回原完成结果，三个operation及事件保持不变，没有新增Agent提交或渲染。两个候选精确资源、相关Secret和渲染容器均确认不存在；当前共享目录与 `public-before.json` 完全相同。回执为 `closeout-receipt.json`。执行路径仍为 `isolated_candidate_import`、`live_http_verified=false`；HTTP入队探针与本次真实候选链是分开的证据，不能拼成完整正式付费提交链。

本轮代码首轮联合332项通过；身份冻结修复后的相关120项、旧三stage逐字节恢复1项及HTTP探针3项另按各自范围通过，与332项重叠，不累加。目录映射72项及Linux补验如前。候选准备名称冲突、Windows长路径下载失败和kubectl空输出解析失败均保留；后二者修正后只重读既有证据／已完成账本，没有追加模型或渲染。本轮预算已用完并收尾。

## 尚不能据此宣称完成

两轮实际图片传输、真实修订与隔离技术复验已验证，第二轮新增C短时遮挡发现及抽查改善，但预登记E问题识别仍未通过，修订片还有固定说明不可见的问题。隔离HTTP额度预占／幂等边界已补证；实际Worker／模型／修订／渲染正式提交全链及业务入口仍待验收，不能把候选导入或合成ready探针记成生产链路通过。下一步应改进通用的运动对象与固定说明关系审阅，并补正式提交闭环；本轮不再新增制作任务。

完整效果库、参考与异结构改编的两次用户认可、源码检索复用、新整片导演流程、用户两轮追加和Canvas／Three.js自由运行准入，均属于后续阶段。声音自然度、实际字词对齐、整片美学及用户采用不因技术通过而自动完成。

## OpenSpec首轮收尾快照

项目 `ec663f5a-53d3-4a0e-9037-933eb788d183` 的 `evolve-video-agent-creation-and-effect-library` 已同步至revision `cb56cf29fc9d7507e118a644755af5673b3aa99a`，精确回读一致，校验 `valid=true`、issues为空。本次仅新增完成1.2，累计5／29完成、24项待办；1.3和1.4按上述原因保持未完成。实际回执为统一阶段目录中的 `spec-closeout.json`。旧音频变更及其他服务任务状态未改。

此为首轮同步快照，第二轮实际进展另按下节同步，不改写首轮回执。

## OpenSpec第二轮收尾

同一项目和变更已同步本轮真实输入、审阅、修订、渲染、HTTP探针及尚存问题；最终revision为 `ba4b236c1244e65d5016b592b75e17e9cbb7beb2`。设计页与本地方案精确回读一致，任务正文保留旧历史；全部勾选逐项不变，仍为5／29完成、24项待办。校验 `valid=true`、issues为空。1.3的预定问题识别未通过，1.4缺完整正式提交链，均未勾选。

同步回执为 `sequence-review-r2/spec-closeout-r2.json` 和最终措辞同步的 `spec-closeout-r2-final.json`。本轮不改旧音频变更或其他服务状态，不合主线、不切换工作台或生产路由。

## 2026-11-11：完整源码r4、批量输入与开发者入口上线

本轮在Git提交 `7cf5f2a3865f5ef1a0068832f833ee811c5eb9f1` 上构建，不再将旧容器文件叠加为本轮发布源码。服务器同步完整源码后按522项文件清单核对，清单SHA256为 `e1fedf479da40b914726c5add557a410d7c85e95fdd6753b245d9326d58ff055`。三镜像均使用 `latest`，发布身份另以源码、构建回执与digest固定：

| 构建产物 | 镜像与digest |
| --- | --- |
| Agent Control | `arm-cluster-master:5000/agent-service-control:latest@sha256:b8e5e536c8ddf1baffe8979d5d78f37245193fad3a27be2d6725e9a62b77b99a` |
| Pi Worker | `arm-cluster-master:5000/agent-service-pi:latest@sha256:2fef7a2c84432860547a72c588e1b72734873eb29ca7bd40c992d6ed2eb76bfd` |
| 动画创作执行器 | `arm-cluster-master:5000/video-animation-creation:latest@sha256:8f7730bb5d4bd710ee05599e2d6d0d0a3a916ab96443432f93e531d4370a5640` |

构建回执 `creation-product-r1/full-source-release-r4/build-completed.json` 记录三产物成功，构建阶段模型请求、部署均为0。构建成功与随后准入、上线及真实制作分别取证，不以镜像存在替代服务运行。

### 固定批量输入工具及离线SDK核对

新增无参数的 `read_inputs()`：一次读取固定只读 `inputs` 中的全部JSON、PNG或JPEG，输出文件名、文本及原始图片内容；不接受路径、URL或文件选择。限制为64文件、每文件2MiB、合计32MiB，拒绝链接、未知格式、非法图片头与超限输入，并保留取消路径。review v4仅开放 `read_inputs/write`，不开放通用文件读取；扩展文件按SHA256 `5f2bde15e1f477b2f6c01bca39785aec0d6ebaa2910627ccfc0a4d44e25dd601` 固定，环境revision同时绑定完整扩展摘要及环境规范数据摘要，Worker加载时重新核对文件摘要。

真实Pi/core `0.99.2` 的断网SDK探针通过：一次工具调用、两次stub模型回合，下一回合接收24张逐字节一致的图片；真实模型请求为0。它验证SDK工具返回与多模态消息装配，不证明真实模型理解画面。该工具相关定向检查为75项通过；回执为服务器统一目录 `creation-product-r1/read-inputs-offline-r1/result.stdout`，不与历史重叠测试累加为总数。

### review v4真实候选：批量门槛通过，E问题仍漏检

独立候选 `candidate-anim-review-v4-r2` 使用上述新Pi Worker和 `video.animation-review.v4`，实际完成一次 `read_inputs()`，没有逐帧工具调用、意外原生工具或非法工具参数。24张供给帧包含21种不同图片字节，全部24个图片位置在成功模型请求中逐字节匹配；首次图片传输发生在一个请求中，不能将21种字节误写成只传了21帧。实际付费模型请求共4次，其中一次连接探测、三次任务内部往返，批量行为及预登记请求数门槛通过；这是一次审阅任务，不是四轮审阅。

候选提出D在1.75～2.25秒转移时遮住C状态，以及A的“保留”标签与筛选结果冲突，均带有效时间和帧引用。它仍未提出预先登记的E卡片约2.2～2.8秒靠近固定说明问题；批量输入成功与已有其他意见不能替代该项识别验收，1.3保持未完成。这是复用已知案例的回归，不代表盲测泛化、完整运动理解或完整视频观看；没有音频，不能评价配音及音画同步。独立自动评估保留 `hidden_finding=operator_confirmation_required`，未自动批准隐藏问题。

Worker与代理均正常退出、零重启，候选专属临时资源已清理。准确回执为 `creation-product-r1/read-inputs-assessment-r2/batch-behavior.json`、`independent-assessment.json` 和 `candidate-anim-review-v4-r2/candidate-output/operator-summary.json`、`cleanup-summary.json`。此前review v3逐帧读取未通过批量门槛的失败及其实际图片传输证据均保留，未重标成功。

### Control与执行器的实际发布

技术准入先固定review v4执行组合摘要 `6cd2a6f158c0b6e0c000172f5f338f95642cc0456d01afe6eb661d499832e7e2` 和绑定摘要 `97a6691e457fb2d87036d97173d3f8cad1b563ea802a8e370a5928adfe8c8e1a`；该准入步骤自身不部署、不分配UID、不发模型请求，不冒称完整生产验收。

随后Agent API、dispatcher与model proxy三个现有Control Deployment已更新到上述 `b8e5e536…` digest，review v4已配置并ready。发布前后历史数据库逐行摘要保持，原服务重新开放；保留旧Host与其他角色、原密钥与策略，不初始化或清空数据库。旧review v2停用，新review分配独立永久UID范围。创作者 `video.animation-create.v2` 的既有准入组合保持，仍使用其已固定的 `sha256:38731ff5003aa80d4a98acd9cc77caa1dbe004cac18185509b3d09d51aeabb40` Worker；不能把新review Worker构建误记为所有角色已经换镜像。Control回执为 `creation-product-r1/read-inputs-plan-r2/rollout-complete.json`，当时仅做零模型调用的能力探测，尚未把后来的网页制作归入该回执。

动画创作执行器r4的init与main实际镜像ID均为上述 `8f7730bb…` digest，Pod为 `video-animation-creation-84995bdb8d-fjgz8`，部署generation为2，review配置为v4；Ready、零重启，健康接口HTTP 200。执行器发布保留原两次失败制作和修订账本，历史行摘要核对通过，没有为了取得成功而清库。准确回执为 `creation-product-r1/executor-rollout-r4/verified.json`；部署本身不证明后续制作、审阅或用户认可。

服务器统一证据根目录为 `/root/video-process-20261105/feedback-20261106/animation-capability-redesign/agent-creation-phase1/`，上述 `creation-product-r1/` 均相对此目录；本地临时证据继续留在忽略目录，不将密钥或模型响应数据库提交Git。

### 网页单次真实制作：完整有界链、媒体及页面恢复通过

浏览器在开发者入口以 `held_out` 输入只提交一次，request为 `c85fc3db-74f8-444a-bd68-1f127daea814`，run为 `c91c2150-1ecc-423c-8968-1948a0b7f276`，lineage为 `4c18cb33-c555-4fba-8388-065157b186d3`。该run已返回 `completed`，初作与修订稿均实际渲染且技术检查通过，包含一次独立审阅及一次修订；审阅提出约1.333秒的标题叠字。初作媒体SHA256为 `14dfe785782de7f9b038257a3f94cbd4ab9158c727b37fb57042d47f300b8aec`，修订媒体SHA256为 `08159933791900e019279718273b0771fb068581eac0980dba6ec916ef35fa04`，原稿与修订稿分别保留。

重新打开开发者页面后恢复同一个request/run，原稿与修订稿媒体摘要保持。两版HTMLVideo均为 `readyState=4`、`duration=8`、`error=null`；修订稿从约0.244秒播放到8秒并返回 `ended=true`，初稿也正常播放到8秒，修订源码下载链接已点击。这验证页面重新读取和媒体播放，不能写成Control API重启或故障注入恢复，也不代表实际下载文件已经独立核对。

三个真实Agent任务分别为初作 `47ffee13-85d4-4f02-bdfa-b14b24f0be53`、审阅 `87af4ff9-4053-4a91-a3b6-07ffee165fc3`、修订 `617f2f41-0018-49d7-afd1-9b9a4c86e2ef`，均为 `completed`、错误为空、各唯一attempt，且 `stop_proven=true`。公开usage为空，不能把三个任务写成三个付费模型请求。持久账本记录初作1、独立审阅1、创作者修订1、渲染2，均等于冻结上限；审阅、修订及技术复验分别保留预占和唯一提交claim。刷新前后严格核对request/run、三个job、lineage、15条事件、两媒体路径与摘要、版本及updated_at完全一致，没有增加执行或消耗额度。

只读独立媒体核对确认原稿105957字节、修订稿104538字节，实际SHA均与产品记录一致；两版均为8.000秒、H264、1280×720、24fps，ffprobe实读192帧，全部192帧完整解码exit 0且stderr为空。核验通过执行器已有工具读取既有文件，没有新增Agent、模型或场景渲染。回执 `creation-product-r1/browser-validation-r4/media-check.json` 的SHA256为 `cdefa359f9ae74a4ee9774451ba66fa9038ccb75a18ce7d591ae3b2dd9438e97`。

本次真实网页全链、持久账本／幂等预占、页面只读重放及已有重开恢复测试共同支持1.4的“有界基础”完成，不以旧候选导入或合成ready的HTTP探针拼接替代。没有执行Control API真实重启或故障注入，不宣称完整生产故障恢复已验收。本次没有重开旧失败任务或发第二次独立审阅。服务端回执为 `creation-product-r1/browser-validation-r4/run-after-refresh-summary.json`、`refresh-readback-comparison.json`、`agent-terminal-jobs.json`；本地浏览器记录为 `creation-product-r1/browser-validation-r4-observations.json`。

完整效果库、参考与异结构改编的用户认可、新整片统筹流程、用户额外修订轮次及Canvas／Three.js自由运行准入仍属后续阶段；此次开发者入口发布不自动完成这些任务，也不代表画面审美、旁白自然度或用户采用已获认可。本轮不合主线。

### OpenSpec r4收尾

项目 `ec663f5a-53d3-4a0e-9037-933eb788d183` 的同一变更已通过 `expectedRevision` 更新至 `903c55814b1281bfa78a93419afb3ce8fbeee967`。仅更新 `tasks.md` 当前事实并新增完成1.4，其他artifact逐字未变；1.3和所有后续任务保持原勾选。累计6／29完成、23项待办，精确回读一致，`validate_change valid=true`、issues为空；没有归档整个变更，也没有修改其他服务。唯一同步回执为本地忽略目录 `creation-product-r1/spec-r4-closeout-1794363097332553100.json`。
