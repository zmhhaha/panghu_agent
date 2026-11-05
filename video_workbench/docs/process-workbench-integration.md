# 过程演示接入工作台

2026-11-05，分支 `codex/video-audio-clock`。用户确认 compiler2 的框图位置“可以了，继续”，本轮继续将已验证的动作能力接入制作链路。该反馈仅确认本次画面方向，不授予旁白时序或声音自然度的人工核准。

## 制作方式

动态图解新增明确的「过程演示」选项，适合三个环节之间同一个对象的流转、失败返回和处理状态变化。「通用图解」继续用于一般流程、对比和聚焦。选择方式只影响新的候选；修改沿用所选候选的方式，不能把旧图解静默转换成另一种合同。

生成只新增候选。用户可以查看节点、关系、传递动作与状态，明确采用后再使用原有录音时序校正、单镜草稿及普通整片制作。估算时序仍为 estimated／pending，正式导出继续要求人工核对。音色仍按项目统一，字幕默认关闭。

## 技术边界

- 独立 Agent profile `video.process-plan.v1`，输入 `process-input.json`、结果 `process-result.json`，使用本仓库原始受控流程资源。模型输出声明式过程数据；坐标、SVG、动画代码和路径仍由编译器提供。
- 独立可选开关 `VIDEO_PROCESS_ENABLED=1`，受总动态图解开关约束，profile revision 默认 `1`。未启用时保留历史、已提交任务的协调与取消，不增加新生成。
- 过程候选固定记录 `motion-process-compiler-2`。恢复任务根据已保存的输入 schema 选择原 Agent profile；渲染快照固定编译器、录音身份和文件摘要。旧请求的默认 diagram 字段不进入既有请求摘要，历史幂等身份保持。
- 生产只接受 `motion-process-compiler-2`＋`controlled-process-2` 的精确组合；compiler1 保留为本地历史重建。实际观察由过程专用复核检查对象路径、停靠位置、状态和归零，不能用一般图解的检查代替。
- 普通整片可以包含过程镜头。现有跨镜连续编译器不能保留这套路径与状态，相关入口明确拒绝 `process_continuity_not_supported`，不生成不完整的连续方案。新的过程跨镜 retain／copy、原声连续验收仍单列，不能将 OpenSpec 3.2b 标为完成。

## 验证记录

接口测试使用真实 SQLite、确定性 PCM 夹具和模拟 Agent／编码器，验证独立准入、候选与采用分离、录音绑定、冻结快照、所有权和交付时序门禁。UI 测试验证方式选择、切换场景、修改方式固定、关闭能力及惰性审阅；镜像测试验证允许清单内独立导入和数据库启动。这些离线测试不证明真实模型生成、真实录音同步或线上媒体验收。

临时包、测试回执与本轮后续证据统一保存在既有忽略目录 `video_workbench/rendered/audio-clock/process/`。下面分别记录离线验证、实际发布和真实业务验收。

本地分组验证：Agent／catalog／wire／恢复兼容综合418通过，2项Windows符号链接权限测试跳过；工作台接口32、部署配置94、允许清单打包30通过；新过程生产路径22通过，相关既有渲染／快照／连续API回归共171通过、1跳过；前端和JavaScript合同80通过。这些分组有重叠，不相加作为去重总数。新路径另以本机真实FFmpeg合成色块夹具，确认最终master PCM逐字节保留两个源录音；该测试不等于真实HyperFrames动画与真实人声通过。

第一次完整生产回归发现生产Job的第二层模板白名单遗漏process2，以及最小拼接测试误删必需动作，均已修正。Windows长路径与SQLite URL限制分别用项目内短路径或extended-path处理，没有放宽业务校验或修改生产路径。失败回执保留，修复后22项定向复测通过。

## 实际发布

应用源码 `b9a002214b72710ed28a6b162f978af6725fa6b4` 已发布到工作台和执行器。镜像使用 `latest` 标签，Deployment 固定 digest；本轮源码仍在功能分支，未合主线。

| 组件 | 镜像 digest |
| --- | --- |
| video-workbench／executor | `sha256:d01c05f0e5edb833fe9a014d60a882546c1f30e2eba39d0c022a5d0796759dbc` |
| video-motion-renderer | `sha256:1b5f8a2373cb00dd7291d725c1b7fab805b0f71231de949f9307644869a65574` |
| Agent control | `sha256:09914a4b7267c7f30253cf02b9bf4d37f1e381680739ddaa4a8781e3c1ffd218` |
| 独立 process Pi worker | `sha256:8d3d50e3a3be98f69b597d5163da4a8ef7cf689c5c1d3d56c039b57a11ed2df1` |

共享 Agent 先从线上精确源码导出，再追加本轮受控 profile，未用开发分支整包替换科研／PPT 服务代码。独立管理员候选验收完成，记录4次模型请求（含工具循环），worker／proxy均成功退出，过期租约被拒绝，验收资源已清理。之后两阶段发布 control 代码与追加式配置；旧15条路由、60个池及既有验收保持，新增独立 process 池。永久 UID 预留不回收、不复用。

视频发布前用精确新镜像及已有18秒会话日志录音完成隔离 `compile → encode → observe → review → media`，严格 HyperFrames、432帧及原声核对通过。没有关闭磁盘检查；早期 Python 标准库文件名冲突与512MiB临时盘不足均有保留的失败现场。

工作台和执行器发布后1/1 Ready、0重启，实际 imageID、runtime 与能力声明匹配。独立 postcheck 在开始业务验收之前逐项核对工作台17表、执行器2表的原 schema、typed 值和媒体 BLOB，原记录全部保持、额外行0、active0；八音色、统一项目音色、health／ready及六项依赖也通过。这里的“额外行0”仅对应发布 postcheck 时点，后续专用项目会增加制作记录。

发布操作器早期一次 guard 释放超时已精确恢复原 dispatcher，再修复换行与flush交接；另一次视频 helper 缺少 `hashlib` 在暂停服务前终止，只创建的两个未引用 runtime ConfigMap 已按 UID／resourceVersion 前置条件清理。原失败现场、once 标记及备份均保留。当前 r3 的真实 apply 与独立 postcheck 成功，不自动回滚或重试模型。

## 工作台真实验收

当前账号可见专用项目「过程演示真实验收：18秒会话日志」，项目 `ca604138-b269-438f-9adf-c1ebd0377a0c`，镜头 `5bc389a6-9823-4e38-87d5-092026cb2a73`。导入已存在的18秒录音，不调用TTS；旁白、实际音频和旧两镜项目保持。

浏览器明确选择「过程演示」，提交一次业务候选，审阅后勾选采用；录音也通过独立采用操作选中。图解候选 `0992498f-1f52-4b4d-801c-ac9455bea187` 为 process v1／compiler2，原录音版本 `2c1dfdb2-e6fa-4ba2-870e-cbb616c68287` 的 SHA256 为 `3fa64b4a6800984b4b87719adca38a2eee533d19a5616d7a79305c08b566bce8`。候选说明和关系只将完整回答经整理环节送入模型历史，失败、重试、取消记录以日志卡片说明保留。

一次单镜草稿 `8441ae83-1467-4383-bcbf-1f46a3400c61` 成功完成，绑定上述确切版本；1280×720、24fps、18秒、432实读帧，H264／AAC，无字幕。浏览器播放由0至18秒后 `ended=true`，未静音、无媒体错误；WAV／MP4下载入口已点击，独立GET完整下载、摘要和解码核验通过。刷新后项目revision7、两个采用指针和同一条制作历史恢复，人工核准复选框保持未选。

MP4 SHA256 为 `7763045194b6d99f923bbcf02bb49d3d714b198b050ccc1a5ab4082b5b9780c1`，277,272字节。整段、首尾2秒及四个beat的零lag原声相关性均≥0.99994，覆盖源432,000样本的完整时长；解码末尾多128个AAC填充样本。原WAV逐字节一致，成片AAC为有损编码，以时长和相关性核对旁白完整，不能称解码PCM逐字节相同。浏览器点击／播放证据与服务器媒体核验分别记录，后者不冒充真人听感评价。

录音时序仍为 `estimated／pending`，没有保存人工时间或确认核准。新过程的跨镜入口显示“不支持跨镜连续衔接，可制作普通整片草稿”，相关按钮禁用；不能将本轮单镜结果作为过程 retain／copy、准确字词对齐或预览／导出一帧一致的证据。

技术链路已跑通，但自动画面策划仍有局限：首段先出现完整回答 token，之后才出现日志外框；尝试记录主要靠卡片说明表达，日志及历史终态仍显示默认“待命”。这些内容节奏与语义状态问题应进入后续导演计划和关键画面审阅，不能因编码与几何通过宣称任意文稿表现力已满足。

服务器原始证据统一位于 `/root/video-process-20261105/`：Agent发布 `production-process/`、视频发布 `video-release-r2/`、原声预检 `history-process-preflight-r3/`、浏览器技术核验 `browser-acceptance/verify-20261105T134159Z-22f38ff91d2f/`。本地非敏感副本与六张MP4抽帧集中在既有 `rendered/audio-clock/process/server-closeout/`；数据库备份只留服务器。

最终只读审计 `browser-acceptance/audit-20261105T135641Z-6a98864aa317/` 再次核对17＋2表旧 schema、每个实际主键的 typed 值和BLOB保持；工作台新增7行（项目、上传操作、音频版本、过程操作、图解版本、采用头、视频记录各1），执行器新增operation／scene各1。专用项目完整操作账本确认 process1／upload1／render1、speech0；两服务0重启、六依赖UID／spec保持，队列active0，renderer Job／Pod均0。一次上游业务Agent操作不等于一次模型HTTP请求，本轮未统计其工具循环请求数。

首次收尾审计因WAL序列化副本不能直接反序列化到SQLite内存库而失败；r2仅将临时内存副本的journal header规范化，保留原始序列化摘要，实际旧行／BLOB与完整审计通过。原数据库未写入，未保存新的DB文件，r1失败证据保留。本轮只完成OpenSpec 3.1b，3.2b、2.2b、声音自然度和整体验收保持待办。精简可追溯回执见 [本轮接入回执](../samples/process-workbench-integration-20261105.json)。

OpenSpec 已同步并读回一致，revision `12a5fe9041960d2c484fe3614f0699851e9265e5`，结构校验通过、issues为空。本变更33项中9项完成、24项待办，仅本轮3.1b新增完成；没有归档或合主线。
