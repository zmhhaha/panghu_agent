# 过程动画跨镜衔接

本轮对应 OpenSpec `improve-video-audio-clock-and-expression` 的 3.2b。继续完善视频业务现有的 HTML/SVG、GSAP 和 HyperFrames 链路；Canvas／Three.js 公共库、独立公共渲染服务和 panghu-tool 迁移暂缓。可信用量／费用和生产存储回收仍由框架统一解决。

## 行为与边界

全部镜头已采用 process2 方案后，可在「跨镜连续图解」规划候选。逐边界审阅对象的延续、复制或不关联，再明确采用；也可先用候选和已采用录音制作草稿。候选生成与修改不调用 Agent／TTS，不自动修改画面、录音采用指针或人工时序状态。

- 延续同一节点时，继承其实际处理状态；延续 token 时，继承其身份与停靠位置。重复的显示／状态动作可保持原状态，冲突明确拒绝。
- 复制只支持 token。副本有独立身份，原件保留在安全留存位置，后续镜头也继续保留；最多三处留存位置，容量不足时拒绝，不丢弃原件。
- 卡片复制、混合普通图解与过程演示、不支持的源编译器，以及不安全的状态／路径组合明确拒绝。普通整片草稿仍可独立使用。
- 人工旁白时序核对与正式交付门禁保持；技术观察通过不等于旁白同步、声音自然度或理解效果已获人工认可。

## 实现

新增 `motion-process-continuity-compiler-1`／`continuous-process-1`，独立于旧普通跨镜编译器。`video.process-continuity-scene.v1` 包装源过程方案和边界段，记录对象身份、复制来源、状态／停靠位置、路径和首尾 checkpoint。

v3 快照保留可重建的 v2 源、完整原录音、源方案和时序。候选明确冻结编译器，缺省的历史候选保持原普通编译器含义。生产提交前重建并逐字节比对文件；执行器只接受登记过的编译器与模板组合。新代码进入镜像的精确源码清单，不添加运行依赖。

运动使用相对于初始位置的 transform；保留严格渲染检查。观察器为新编译器增加跨镜边界、复制来源以及非单调／反向跳转截图。独立复核器校验实际 DOM 几何、状态、端口、路径、文字避让和原件保留；截图字节、回执、运行指纹和媒体摘要在生产入口再次绑定。

## 验证状态

应用实现提交：`4cebd3abe8ea78a6c603ce328b65888087ca1671`；严格渲染位移修复：`e07dfbc24afc43af6fa49295889b83e946628b0c`；转场终点判定修复：`eab5a7e7d4d43bb66a866785038b0e55f756e5d1`；重复帧校验补丁：`139295401b24dd1440c6df227d79776eeaa7036b`。独立分支 `codex/video-audio-clock`，本轮未合主线。最新生产发布和真实浏览器技术闭环已完成，OpenSpec 3.2b已同步并回读验证通过，详见下文最终闭环与收尾回执；原浏览器失败完整保留在历史小节。准确人工时序、声音自然度及整体验收未自动核准。以下按阶段保留原结果。

- 完整 Python 回归：1903 passed、5 skipped。Windows 深路径导致的首次失败保留，改用短临时路径后通过；该完整集合运行于位移与终点判定修复前。位移修复后 78 项定向回归通过，最新终点判定修复后 166 项定向回归通过；这些集合有重叠，不累计，也不宣称最新提交重新运行了完整集合。
- 全部前端／渲染 JS 回归：153 passed；旧编译器和冻结快照兼容集合另有 125 passed，集合存在重叠，不累计。
- 早期发布操作器工具集合：62 passed，回执为 `test-video-release-operators-r3-real-helpers.xml`；这只说明该版工具的测试范围，不代表本轮最终发布操作器完整回归。专用浏览器验收夹具的事务／回滚演练另有 6 passed。合成证据不替代实际发布或浏览器验收。
- 预检工具补充检查：26 passed，包含实际 FFmpeg 组合与主 PCM 取证。预览／编码帧对照工具 r2 的 12 项通过，补充 token 标签紧边界及字形区域检查；后续工具 r4 集合 35 项通过，另有三个实际失败目标重算通过。这些集合有重叠，工具测试与定点重算不代替实际完整帧对照结果。

首次隔离渲染因 GSAP `left/top` 位移被 HyperFrames 严格 lint 拒绝，未修改线上；已改为 transform，并保留失败目录。后续 r2 使用完整的原 12 秒＋7 秒录音，retain／copy 两种路径各两段，共四段实际编码与 DOM 观察均成功；但当时的独立审查误报转场终点状态，整轮没有因此写为通过。

误报来自恰好 0.6 秒的浮点边界：`(.6 - .15) / .45` 略小于 1，实际终点 `.15 + .45` 却已经等于 `.6`。最新修复在核心状态求值器和独立复核器中，以 `time >= start + duration` 判定离散停靠状态是否完成，插值仍使用原比例。没有放宽几何或截图校验，也没有修改渲染动画。基于 r2 原包装方案重建，retain-0／retain-1／copy-0／copy-1 的全部 24 个编译文件在修复前后字节完全相同，并逐项匹配原 manifest 摘要；本地对原四份观察重新诊断均通过。证据为 `continuity-media/preflight-r2/compile-identity-report.json` 和 `local-diagnostics.json`。原审查失败回执保持，不把本地诊断写成新的服务器预检成功。

## 预检与发布进度

最新源码对应的工作台 `build-r5` 已构建并发布，镜像标签均使用 `latest`；本轮预检与发布绑定以下摘要：

- 工作台：`sha256:167060a2c4e923c16605b865cf47c88a98d3c5d9aef0db977cfb31a1b2df8d60`。
- 复用的渲染器：`sha256:aaa8f1c05e141ac83c182df1a66826f9931d2d0a38278aaecf68d9f12b0ba486`。终点判定修复不需要重建该渲染器。

服务器 r3 的编译、独立复核与组合已经成功，但媒体检查工具随后尝试读取已被正常清理的 `assets/master.wav`，报 `FileNotFound`。该轮保留失败，不能仅凭编码成功视为整轮通过。r4 在组合阶段保存实际已关闭的主 WAV 及其取证回执，随后照常清理临时文件；没有关闭生产清理或用重新拼接的音频替代真实主音频。

后续 r4 已实际完整通过，状态为 `exact_process_continuity_preflight_passed`。四段共 28 个渲染输入与原输入精确相同；八个旧渲染器阶段（四段编码及四段观察）原样复用，保留原执行身份；编译、独立复核、整片组合和媒体检查四个工作台阶段重新执行。业务写入为 0，不能将复用的渲染器阶段记为新执行。

retain 与 copy 两条成片均为 19.625 秒、1280×720、24 fps、实读 471 帧，完整解码通过。组合时实际捕获的主 PCM 与预期逐样本一致；MP4 解码音频各检查十个原声相关窗口，两片的最小相关系数均为 `0.9999441150885439`。主 PCM 的精确一致与有损 AAC 解码的相关性分别记录，不将后者写成逐样本一致。

| 路径 | MP4 字节数 | MP4 SHA256 |
| --- | --- | --- |
| retain | 338,881 | `a93a2385322fd2c1a1df26c57ab53b2d2699dcf95c6c2d8abb715290d74aaa07` |
| copy | 356,768 | `25bb9e56b9bb187042004a2b1143f404a9a8452ffbe1aa77c21bf23c4fbd2848` |

r4 完整回执为服务器 `preflight-r4/summary.json`；本地非敏感副本为 `continuity-media/preflight-r4/summary.json`，SHA256 为 `776199841cd05c39775a3bba36e39a1db8abe22350636f9ef4e6a53414e48e0e`。该结果证明固定输入的技术预检，不自动证明画面审美、准确旁白时序或声音自然度。

### 预览与编码帧对照的失败与边界

原 parity-r1 的两段最终质量编码均已完成，分析工具随后因运行身份无法读取 root／0600 的摘要而停止。后续复制可供该身份读取的非敏感摘要，并复用这两段原最终编码，不重新编码。此前完整分析的 80 条对照中 74 条通过、6 条失败，原报告保留，没有把整轮写为通过。

六条失败中，四条是 copy 在 0.375 秒同一问题的重复记录：将几乎为零的位移与裁剪坐标一起取整，导致 ROI 量化后偏移一个像素。工具 r4 改为独立量化位移，避免微小数值误差改变原裁剪位置。另两条重复记录来自初始帧仅有标题和说明时，全前景平均误差指标不适用。r4 仅在 `initial` 且无可见图解对象时，强制使用实际 DOM 量测的标题／说明字形区域及其外背景检查；全局指标、全部动态 ROI、token 标签及字形阈值保持，其他帧仍保留原前景限制，没有抬高 16 的阈值。

初始文字在实际预览与编码帧间存在栅格边缘差异，不能表述为逐像素完全相同。局部结构和字形区域检查用于核验相应内容；已有普通 codec 基线不能解释这项差异，来源尚未查明，不将其归因为已确认的普通编码误差。工具 r4 的 35 项测试及三个实际目标重算已通过，仍不代替完整分析。

后续服务器 parity-r3 已使用修复工具完成新一轮完整分析，结果为 `passed`：六个片段（四段 draft、两段 final）的 80／80 条对照全部通过。使用目标时刻相邻的 floor／ceil 编码帧，对照时差不超过 `1/24` 秒。继续复用 parity-r1 原两次成功的最终质量编码，本次重新分析的额外编码为 0；不将复用编码记为新执行。原权限停止、74／80 报告、诊断与修复记录均保留。

本地完整结果为 `continuity-media/parity-r3/summary.json`，SHA256 `80bbdfa5c332b28f97bda3805608de634f204636a7ea19e3b953ce5ada515f49`；详细对照 `results/parity.json` 的 SHA256 为 `845217781fccfcafd9ba10f9d5dc20b57aa6a8489df20dc97eac2abd0af05ff8`。两份本地回执的摘要与上述身份匹配。

助手已目视检查 initial、retain 转场末端，以及 copy 转场中段／末端／最终态的 preview、decoded 和 diff 对照；在这些所查画面中，文字、停靠位置与原件保留未见异常。该检查只覆盖列出的样本，不是用户审美、准确旁白时序或整体听感核准；上述初始文字栅格差异和非逐像素一致边界仍保留。

### 发布进度

`release-r1` 的 prepare、apply 与独立只读 postcheck 均已成功，发布编号为 `video-process-continuity-20261106`。工作台和执行器 Ready、实际 imageID 与固定 digest 匹配、异常重启均为 0；旧 Pod 已观察停止，原不可变运行配置保留。health／ready 为 200、匿名会话为 401，执行器鉴权及 runtime／capabilities 匹配；八音色和 13 个项目的统一音色投影保持。

发布后核验比较工作台 17 表和执行器 2 表：旧 schema、原行值、已有连续候选及采用记录保持，额外行 0、队列 active 0、无活跃渲染 Job。此发布阶段 Agent／TTS／渲染提交均为 0，共享 Agent 和数据库 schema 未改；两份新鲜锁定备份仅留服务器。发布汇总 `continuity-media/release-r1/summary.json` 的 SHA256 为 `8d3d481f587285e3c6bee2dcea906ff10e1767842d9f55c1d726435e01a25dce`；独立 `postcheck.json` 的 SHA256 为 `1f4dd9dc1f2e95fd1038421471fc2fe7bbaffb12c94684d3600b2c79150a3415`。汇总中的 `postcheck_pending` 是 apply 刚结束时的历史状态，以后续独立通过回执为准。

发布后另外准备专用浏览器夹具项目 `7c3104da-cf2a-44cc-b035-764cc2c43380`。上述“额外行 0”只针对发布后核验当时，不包括随后新增的夹具项目或后续浏览器制作记录。夹具存在不等于真实浏览器闭环已完成，候选审阅／采用、草稿、刷新和下载仍在验收中。

### 真实浏览器阶段：连续草稿失败，闭环未完成

真实页面已完成两镜图解与原录音的明确采用。自动规划的候选 1 为 retain；将 token 改为不关联时，未保存修改保护生效，并可另存候选 2、保留原候选。返回候选 1 将 token 改为 copy，另存候选 3 后明确采用。正式导出按 pending 时序门禁拒绝，界面提示“动态时序仍待人工试听核对，完成后才能正式导出。”；没有自动核准人工时序。

随后唯一一次真实生产连续 draft 使用候选 3 的 token copy 与默认 `end_hold=4` 提交，在第二镜 `validating_scene` 产物校验阶段失败，界面问题编号为 `MR-8dbdfdececeee794`。这次失败保留，未将此前固定输入的预检、帧对照或已通过的候选交互当作生产草稿闭环通过。失败原因尚未确认，当前不作推断。

本轮完整浏览器验收仍为 failed／未完成，相关成片播放和下载闭环尚未通过，3.2b 保持未勾选。3.3、声音自然度、准确旁白时序与讲解效果继续待办或待人工确认，不因固定输入预检、帧对照或发布通过而自动完成。后续诊断或修复结果另行追加，保留本次真实失败记录。

所有本轮操作器和本地证据均放入原有 ignored `video_workbench/rendered/audio-clock/process/`；服务器证据放在原根 `/root/video-process-20261105/continuity/` 下。数据库备份仅留服务器。首次层数保护停止的构建、首次编码失败和后续修复结果各自保留，不覆盖历史证据。

### 重复跳转截图诊断与有界校验补丁

2026-11-06追加：原 `MR-8dbdfdececeee794` 对应 `process_continuity_seek_sequence_mismatch`。第二镜时长11.625秒，前置转场0.6秒、片尾停留4秒。在不同跳转历史后请求同一 `.375` 秒，诊断样本13与15的实际DOM、状态、几何、computed transform、GSAP缓存值及文字完全相同，JPEG SHA却不同：

- 样本13：`0ced8ba07dc78fef7a2967fa777d22bc3781bd3e2beb308c878dcdadbaa92439`。
- 样本15：`9f347ad5e1e895e03bc2ef358577716ebe3c0eb2e624b1b5d8a268f1dfd7a90c`。

真实1280×720 RGB解码比较为1600个差异像素，全图平均通道差 `0.0052126736111111115`、最大通道差44；差异包围区域为 `(568,464)-(721,529)`，集中于token圆角附近。文字区域及其向外2px完全相同，没有测得文字位移或残影。这里只确认观察结果，尚未证明底层成因，不能称为已确定的普通编码噪声或特定Chromium机制。

实际诊断还检验了提前注入已核验runtime bootstrap与 `--disable-partial-raster`，两者均未改变差异；每种跳转历史后连续三次截图各自保持稳定，历史间差异仍存在。现有证据不支持对应的初始化、partial raster或等待截图稳定猜测。原失败、各次诊断和截图均保留，不以补丁覆盖既有记录。

源码 `139295401b24dd1440c6df227d79776eeaa7036b` 不改编译器或渲染器产物，只调整独立复核中的重复帧判定，保留原SHA作为证据身份：

1. 测量的元素、关系、布局、连续状态及请求／实际时间必须精确一致；同SHA沿用既有精确比较路径。
2. SHA不同时必须取得两份真实字节、逐份匹配SHA并完整解码为1280×720 RGB JPEG；缺字节、坏SHA、异常格式／尺寸、损坏或过大证据均拒绝。
3. 允许区域只来自编译器固定尺寸可见token的圆角几何及其JPEG块邻域，不根据实际差异扩张。全部测量文字区域向外2px保护，保护区及允许mask之外必须逐像素精确一致。
4. 允许区域内仍须同时满足全图平均通道差≤0.01、最大通道差≤48、差异像素≤2048。任何一项超限拒绝；这不是通用感知相似度放宽。

新增 `test_process_continuity_repeat_frames.py` 的32项通过，回执为 `repeat-frame-tests-r2.xml`。真实原JPEG和对应测量保存在 `tests/video_workbench/fixtures/process-repeat-frame/`，包含双向真实对照，以及文字擦除／换字／单笔画、额外细线、位移、缺对象、背景、2px文字保护边缘、证据异常、非法metadata和三项预算独立超限负例。微笔画等负例即使低于三项全局预算仍被保护区规则拒绝。既有定向回归另444项通过，回执为 `patch-review-regression-r1.xml`；不累计历轮集合，也不声明最新源码已重跑全部Python测试。

`build-r6` 已构建新工作台 `latest@sha256:ba9a9ba2b67fa79fca7715de80f9fb3e90afc9d9465fc59f902445004b8ea5a6`，复用渲染器 `latest@sha256:aaa8f1c05e141ac83c182df1a66826f9931d2d0a38278aaecf68d9f12b0ba486`，runtime ConfigMap保持；此阶段尚未部署。`continuity-media/build-r6/images.json` SHA256为 `57b420b4a169d9354c7a511cbdf96204e177ae45793f49784acd23e7e3aff0ec`。

以该新工作台镜像在隔离环境重用原 `diagnostic-r2` 输入、观察和真实JPEG，实际生产复核入口已返回 `exact_failed_scene_review_passed`。原观察前后SHA保持，原样本13／15仅经上述有界规则通过，两份原SHA、差异指标和保护区精确结果均保留在复核输出。没有重新观察、Agent或TTS调用，也不是一次新的生产视频制作。`continuity-media/patch-preflight-r1/summary.json` SHA256为 `44ff1cb21dc4f43a4bcc769ed72faa8aafac4e9c58df3d85d6d63f164683ce87`。

当前四段输入的 `preflight-r5` 正在执行，尚无通过结论；补丁未部署，浏览器尚未重做。原真实浏览器失败继续保留，草稿／播放／刷新／下载闭环和3.2b仍未完成；3.3、准确人工旁白时序、声音自然度及整体讲解效果保持待办，不因本次隔离复核通过自动核准。

### 后续预检、发布与新草稿结果

2026-11-06后续：同一源码 `139295401b24dd1440c6df227d79776eeaa7036b` 的 `preflight-r5` 已实际返回 `exact_process_continuity_preflight_passed`。编译、独立复核、整片组合及媒体检查重新执行，原四段编码和四段观察继续按原执行身份复用；这些复用阶段不算新渲染。retain与copy仍各为19.625秒、1280×720／24fps、471实读帧，MP4 SHA分别为 `a93a2385322fd2c1a1df26c57ab53b2d2699dcf95c6c2d8abb715290d74aaa07`、`25bb9e56b9bb187042004a2b1143f404a9a8452ffbe1aa77c21bf23c4fbd2848`，与r4两片完全相同。预检主PCM及原声检查仍按该固定输入预检范围通过。`continuity-media/preflight-r5/summary.json` SHA256为 `e2efcf43f6cdc0a08904711df374e87376fbfbca0e4d3b43af2366400a37588e`。

补丁发布wrapper首试在导入摘要检查时停止，尚未造成线上变化。经检查，服务器现存较早base与本地增强版不同，分别保存为 `release_continuity_patch_base_r1.py` 与 `release_continuity_patch_remote_r2.py`，服务器原文件保持；现存base的身份不被据此反推为release-r1当时执行的身份。新版发布工具101项通过，后续只读audit工具另35项通过，均仅说明各自工具范围。

随后 `release-r2` 的prepare、apply与独立postcheck已实际通过。工作台及执行器使用新 `latest@sha256:ba9a9ba2b67fa79fca7715de80f9fb3e90afc9d9465fc59f902445004b8ea5a6`，Ready、实际imageID匹配、0重启；渲染器 `aaa8f1c05e141ac83c182df1a66826f9931d2d0a38278aaecf68d9f12b0ba486` 及runtime ConfigMap `video-motion-runtime-6e2ad4a143eff46537e2` 保持。发布核验比较14个项目、工作台17表与执行器2表的旧行及媒体，额外行0、active0；这里的零新增是新浏览器草稿之前的时点结果。

`continuity-media/release-r2/summary.json` SHA256为 `443fae0a8b0bbe2a186aa806064b202b3e28976e786b6bd619b1d1fdf2f21032`；独立 `postcheck.json` 为 `0bf6cf5598de1eb85f8bfd1b142025f31759db391f787f250922b278b1879ac8`。summary中的 `postcheck_pending` 保留apply刚结束时的状态，后续独立postcheck通过回执为准，未改写该历史字段。

真实浏览器随后创建并完成新草稿 `20951f68-5849-4610-b823-64f4ab4fbe5c`：项目仍为 `7c3104da-cf2a-44cc-b035-764cc2c43380`，沿用明确采用的候选3 token copy、原录音和默认4秒片尾停留。新片为23.625秒、1280×720／24fps、567实读帧、375766字节，MP4 SHA256 `7c364330b99f3d80e0124f0fae4778574beb88e2a38a16a4ab8f48b5c7e298bc`。完整解码及十个AAC解码原声窗口核对通过，最低相关系数 `0.9999441150885439`；此次真实草稿未测组合主PCM逐样本一致，不能用固定输入预检的主PCM证据替代。未核准人工时序或声音自然度。

草稿后只读audit返回 `process_continuity_browser_read_only_audit_passed`，逐项核对全部原typed行及媒体BLOB保持，仅新增本次工作台 `video_renders` 1条、执行器operation1条与scene2条；原失败 `67e6faca-1ca3-4313-bd9d-a0720495d934` 和候选采用保持。audit回执为 `continuity-media/browser-acceptance/final-audit-20261106T092601Z-160afb38a26b/summary.json`，SHA256 `1d41e59b3ba0f0ee8e42b039c4cc4d8f46bc5bbd9ca2a354ae15cc2ecd2d5c0c`。audit不写业务数据，亦不自行证明浏览器播放／下载通过。

浏览器已实际打开新草稿播放器，并在 `12.617066` 秒与 `11.922213` 秒完成正反跳转。完整播放、下载与最终刷新仍在验收，当前不写成浏览器闭环通过，3.2b保持未完成，等待这些实际步骤结束后再追加结果。原浏览器失败及各诊断阶段保留；3.3、准确人工旁白时序、声音自然度和整片讲解效果继续待办或待人工认可。

### 最终浏览器技术闭环

2026-11-06后续实际验收已通过：使用新草稿 `20951f68-5849-4610-b823-64f4ab4fbe5c` 的原生播放器控件，回到0并完整播放至23.625秒自然结束，`ended=true`、`muted=false`、`error=null`。向12.617066秒及返回11.922213秒跨镜跳转成功，可见复制后副本与原件以及返回边界前的画面。

实际点击下载按钮后，工作台访问日志确认同一成片媒体GET200，播放器媒体请求为206。服务端导出的同一375766字节MP4已另行取回，SHA256为 `7c364330b99f3d80e0124f0fae4778574beb88e2a38a16a4ab8f48b5c7e298bc` 并完整解码通过；浏览器操作系统最终文件落盘未观察，验收范围是下载入口和响应，不声称浏览器文件已保存成功。可查看[23.625秒连续草稿本地媒体](../rendered/audio-clock/process/continuity-media/browser-acceptance/final-audit-20261106T092601Z-160afb38a26b/draft.mp4)。

刷新后完成草稿历史恢复，候选3仍已采用，原失败记录保持；重新打开同一render ID，仍恢复23.625秒元数据且无媒体错误。再次尝试正式导出仍被pending人工时序门禁拒绝，未勾选人工核对。3.2b技术实现、发布与浏览器闭环因此完成，OpenSpec任务已实际同步并回读验证通过；3.3、准确人工旁白时序、声音自然度与整体讲解效果继续待办或待人工确认。

最终浏览器[实际回执](../rendered/audio-clock/process/pc-browser-passed-r2.json)状态为passed，SHA256 `76d7a12e115efec8e8bc5d52a702d88798041b0b088a80dc3a4d0a931361a153`。该回执明确保留原失败，浏览器制作总计两次、补丁后一次，Agent／TTS为0；不以技术验收替代真人听审和画面效果认可。上文各“待验／未发布／失败”段落继续作为当时的真实阶段记录保留。

### 收尾回执与OpenSpec回读

本轮[技术收尾总回执](../rendered/audio-clock/process/process-continuity-closeout.json)状态passed，SHA256为 `a8c6bc4ecb36c0aeed7c56438f469aef2d7be09e0fa33f9c50c3882db18076ce`，关联真实预检、局部帧对照、原失败证据复核、发布／postcheck、浏览器和只读audit身份；不扩大各证据原有范围。独立分支 `codex/video-audio-clock` 未合主线。

OpenSpec项目 `ec663f5a-53d3-4a0e-9037-933eb788d183` 的变更 `improve-video-audio-clock-and-expression` 已实际更新至 revision `f67f5a1019a1eda4e1d8777bfeade3e7f0cfdb0a`，随后回读匹配、验证 `valid=true` 且issues为空。仅将3.2b从unchecked改为checked，其余checkbox保持；33项任务由完成9项变10项，剩余23项。3.3及人工自然度／准确时序／整体效果核准保持未完成，不因本轮技术闭环而代勾。

本地[同步摘要](../rendered/audio-clock/process/process-continuity-closeout-openspec/summary.json)、[实际回读](../rendered/audio-clock/process/process-continuity-closeout-openspec/readback.json)与[validate结果](../rendered/audio-clock/process/process-continuity-closeout-openspec/validate.json)保留；同步摘要绑定上述技术收尾总回执SHA。
