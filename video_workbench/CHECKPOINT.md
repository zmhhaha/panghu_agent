# 视频工作台交接记录

更新：2026-10-31（Asia/Shanghai）。八镜头真实配音、206 秒 MP4 独立技术检查及工作台 rollout restart 后持久化均已通过；最终证据已下载校验，OpenSpec 已同步并验证通过。下方 2026-10-30 内容保留为历史，禁止按旧脚本重复生成任务。完整稿听感尚未获用户认可。

## 音频阶段工作位置和部署

- 应用工作树：`D:\github\armbianbegin\.video-audio-worktree`；分支 `codex/video-audio-production`；部署源码 `e5b375b02e8b639b0b4a0eecaa17c67145c005bc`，尚未合主线。
- 语音独立工作树：`D:\github\armbianbegin\.speech-service-worktree`；分支 `codex/speech-service-rk3588`；不要把语音服务源码塞进视频应用目录。
- 工作台镜像：`arm-cluster-master:5000/video-workbench:latest@sha256:ccd2509379ad55029760cf5229d09dd33b0577213172a20e791d9818ad06c6fe`；主动重建后最终 Pod `video-workbench-6454f4845f-bj52n` Ready、实际 imageID 不变、异常重启 0，替换旧 `video-workbench-7889b8d5b5-ml22n`。
- 线上语音：`http://speech-service.speech-service.svc.cluster.local/v1`，模型 `kokoro-rk3588`，男声 `zm_yunjian`；修复源码 `c506bb3c105c81200bc3b86577b65fb96f3361e8`，latest digest `sha256:adb9d87b15b461db56a2f4aca5fd6999221fe4c715ee3751250b3a4b21423634`。Pod `speech-service-758cbd54f9-bfczb` Ready，PID1 capabilities 五组全 0、NNP=1。只记录 Secret 名称／挂载位置，不输出 token；不静默 CPU generator 回退。
- 本地运行脚本：`D:\github\armbianbegin\.runtime\video-audio-production`；master 发布／证据目录：`/root/video-audio-release-20261031`。后台执行 kubectl 必须显式带 `KUBECONFIG=/etc/kubernetes/admin.conf`。
- 用户已授权自主完成修复、部署和验收；不改 DNS／Casdoor，不新增付费视觉调用，音频分支不合主线。

## 音频真实验收：继续已有副本

| 对象 | 当前固定值 |
| --- | --- |
| 原 DSH 项目 | `cdad4be8-3e51-44ff-98df-3d7500d147d3`，revision 27，8 张 SVG 已采用，原项目未改 |
| 音频验收副本 | `1e2a822d-c4c1-45de-a8b9-b72a82f714bc`，名称 `[配音验收] DSH 架构解析（复用已有画面）`，属于原用户 |
| 八镜配音 | 8 镜均已真实合成、明确采用；复用已有画面，新增视觉 Agent 调用 0 |
| 已完成 MP4 | render `5641dc62-01d7-4540-8e79-501ec801cced`，206 秒、H.264／AAC、1280×720@24fps、4944 帧 |
| MP4 SHA-256 | `75ab83eba053d20b0d8beee8687709ee5aadd10eabef8549dfc4b55122358c31` |
| 冻结与项目 revision | render 冻结 revision 67；后续编辑恢复 revision 69，局部失效验证通过 |
| 第三镜原失败 | `0f99c230-d5b4-414c-8c4e-8175e464ea67`，`--patch` 的 U+002D 不在 Kokoro 词表，旧 failed 保留 |
| 第六镜原失败 | `03d700c9-e71d-4dd7-bbff-fa608acc82c1`，D=181→200 的 snap 约 10.497% 超 10%，旧 failed 保留 |
| 重启后持久化 | 8 条采用音频逐一 SHA 匹配，上传 A2 历史保留，MP4 SHA 匹配，SQLite integrity ok，原 DSH revision 27 不变 |
| 尚未完成 | 整稿自然度／术语人工试听、字幕人工校正、BGM／混音与容量管理后续 |

第三镜通过工作台显式读音修正处理第 3／5／6 镜 CLI、路径与文件名，原旁白不改。第六镜通过完整无损 NPU 分段规划修复，保留原语速、文稿及 10% 限制，无 CPU generator 回退；新 intent 已完成剩余真实配音，两次旧失败不反写。**不要原样重跑 `workbench_audio_live_acceptance.py` 或续跑脚本：八镜与成片均已完成，无需重复创建项目或合成。**

工作台重启、8 配音／MP4 hash、SQLite integrity、原项目 revision 27 与进程权限检查均通过；PID1 五组 capabilities 全 0、NNP=1，PVC 当时可用 988,618,752B。speech Pod `speech-service-758cbd54f9-bfczb` 仍 Running、异常重启 0。回执位于 master `/root/video-audio-release-20261031/postflight.json`。`fetch_workbench_audio_evidence.py` 只取媒体和报告，不下载数据库／Secret。VPS 视频上传上限已改为 34m；浏览器已完成第二镜 1,248,044B／26 秒 WAV 上传 A2、采用、恢复 A1，A2 历史保留；真实 audio clock 第 1→2 镜播放已通过。

## 音频阶段已确认的证据和边界

Python **471 passed、1 skipped**（Windows Secret 符号链接权限）、Node **25 passed**。最终镜像 ARM 组件验收通过 10 张 1280×720 PNG 和 7 秒 H.264／AAC MP4，归档 SHA-256 `2816556a9a469ca438b61afd82389bb137990c95589c3034f3be609ebd7e42fe`。后续完整 206 秒 MP4 也已独立检查：完整解码、8 镜音频首尾匹配（全片相关系数 0.9999445、NRMSE 0.010601）、8 张参考 PNG 与顺序，以及 44 条 estimated 字幕时间／文本与 timeline 一致均通过。ZIP 19 文件含 8 source、其他 owner 404、损坏 WAV 422 且项目不变均通过。

生产 SQLite 升级前备份为 `workbench-before-audio.db`，258048 字节，integrity ok，SHA-256 `16d90ce340321dec66193729d046176305a2c4609f3f884c7677561c225805b1`；只保存在 master，不输出 owner／凭据。构建下载超时和首轮 ARM host 脚本 `Path.chown` 失败记录保留。

最终证据归档 19,122,821B，SHA-256 `64cbe74652f62513504df98d4fa2ea483d5400fdfecd69fe02d2409b7269bec0`，已下载校验并安全解包至 `D:\github\armbianbegin\.runtime\video-audio-production\evidence-final\`；成片为 `live-audio/dsh-narrated.mp4`。第 03／06 镜中心帧已目视检查，中文正常；媒体归档不代替数据库备份。

OpenSpec 变更 `add-video-workbench-audio-production` 已同步至 revision `49692535c5004b27a6fc4132900185422bb75f3d`，验证 `valid=true`、无 issues；19 项完成 14 项，剩余 1.1 整稿听感、4.2 术语人工试听与 5.1–5.3 BGM。未执行 apply／archive，独立分支未合主线。

静态 WeasyPrint 不保留 CSS 动画；字幕仅估算、需要人工检查；BGM／混音未做；完整稿声音自然度与术语发音未经用户认可。PVC 1Gi，未完成全服务音频容量管理与删除流程。

## 历史记录：2026-10-30 静态素材首版

以下是当时静态素材首版的项目、发布、验证与合并记录；不要将其中的“当前”理解为音频阶段状态。

更新：2026-10-30（Asia/Shanghai）。本文件列出继续工作必须保持的项目、版本和边界；当前结果见 [CURRENT_STATUS.md](CURRENT_STATUS.md)，执行顺序见 [NEXT_STEPS.md](NEXT_STEPS.md)。

## 工作位置与当前进度

- 工作树：`D:\github\armbianbegin\.video-workbench-worktree`。
- 开发分支：`codex/svg-video-workbench`。用户已批准，功能已快进合入 `panghu_agent` 的 `main`，保留已验收的应用代码树。
- 服务器独立目录：`/root/video-workbench-release-20261029/`，证据位于 `evidence/`。不把共享科研源码目录当作本次固定构建来源。
- 已部署工作台能真实登录；登录隔离、重启持久化、备份／离线恢复已验证。
- r7 已完成 SVG 修订与 V2 采用、独立运行中取消；原 HTML 失败记录保留。r8 新 HTML 完成并明确采用，已导出 SVG V2 与新 HTML 的正式 ZIP，源码／预览／播放器结构核对通过。
- f90 修复、新 HTML 候选、工作台 55ee、scene 人工续接发布均已完成。发布首次 release_guard 超时失败保留，不写成全自动成功。浏览器控制、最终备份、证据归档和验收环境清理已完成；固定画布裁切限制已记录，自适应仍待优化。

## 继续使用的项目与版本

| 对象 | 固定值 |
| --- | --- |
| 制作项目 | `49336596-ee04-4478-8a44-8d87aa470b28` |
| 当前 r8 项目修订号 | `15`；历史 r7 快照为 `12` |
| SVG 镜头 | `3b1e9c34-be8e-4aab-8192-155f858091a2` |
| SVG V1 | `678e5d30-92f2-4bbf-bd67-d098b4f6b9b3` |
| 已采用 SVG V2 | `5632a726-b19f-4c7c-949b-2a8f076372a8` |
| HTML 镜头 | `04c12ebc-2f6d-4d83-bd5f-e44dd07929ec` |
| 已采用新 HTML 版本 | `fdf22ab7-5254-40f1-b4d1-0e333bc0d885` |
| 新 HTML operation | `8255189a-9dec-4ec4-9b33-a6589bdfe120` |
| 已完成新 HTML Job | `26df9a50-0f75-4711-8f3e-6b7652762f0f` |
| 已完成分镜 Job | `2b0ee239-f867-49a9-ac79-74a711e6283f` |
| 已完成 SVG 初稿 Job | `9a264e44-e270-4c57-8a4b-79dd69f8deca` |
| 原失败 HTML Job | `c27f9205-5987-4f20-bd68-9ae77a2f163d` |
| 已完成 SVG 修订 Job | `d5c82bd4-4f2c-4813-870c-17f741d03454` |
| 已取消独立 Job | `215ea471-a9ae-4ddb-b418-5666206b29cc` |

V1 SHA-256：`fc850309ad3e4913574bad1767369377f2f738b57620676d51d8863d146247c4`；V2 SHA-256：`65e36c123d0d228816c60243e8dcf90857712f267001502d0aefdda36f23654d`。V2 的父版本必须保持 V1。新的 HTML 已完成，现有分镜、SVG、修订、HTML 与取消均无需重复生成。

## HTML 修复与最后一次制作

r6 原 HTML 输入 SHA-256 为 `9655f6fd4b450fd1527a2722669ad96a4116c34967296b3262687e08980fc28a`。旧幂等键为 `live-video-live-r1-html`。r8 已用新的幂等键完成生产 HTML，旧失败任务保持原状态。

原失败的直接原因是校验器拒绝 `<meta charset="UTF-8">`。f90 仅增加 head 中单一 charset=UTF-8 声明的支持，其余 meta／属性／位置限制不变。修复源码为 `f90ba1cf00ab90ff06051e08db4b6ef2c933a7f7`；profile 82 通过、工作台与 gateway 112 通过，各自统计。

新工作台 image digest 为 `sha256:55ee0651e38565b68869b557c2ae9dbd76456ce25134402d4cad5401308a575a`；新 Pi 为 `sha256:5b865fbea90fa97f59ab4b62395775526d4e5df59e5ad71185dd562744f70e75`，runtime 为 `video-pi-f90ba1c-html-r1`。工作台 55ee 已 Ready，Pod 为 `video-workbench-6d6499f7d4-ddddr`，UID 为 `482faee0-608b-4784-85aa-968834c8f051`，真实用户刷新后项目保留。新 HTML 候选 `candidate-video-html-r1` 已完成，4 次模型调用含探针；UID 44000006–44000008，Worker／代理均实际终止、退出码 0、重启 0，独立审查材料已准备。scene 已经人工续接发布，状态为 `scene_binding_upgrade_ready`；当前 control digest 为 `sha256:ec86cd6602df9e3783b2d0f0c768a6f01fee129385d8987f9c73da1ea2b9c7aa`，源码 f90。新池 `video-scene-pi-72fc4ecbab3cf7c8edaa` 使用 UID 46200000–46265535。18 条路由、58 个池，原 57 池和其他 17 条路由完整保留；科研 6／PPT 5／视频 2 项能力实际查询全部 ready。

r8 本轮提交 1 次、完成 1 次，状态为 `html_tail_verified_adopted_exported`。累计生产制作提交 5、完成 4，原失败 HTML 1；另有独立取消任务 1。额外 HTML 候选与生产各一次额度已使用，不自动重试。

本地最后尾段工具为 `.runtime/video-release/finish_html_after_charset.py`，默认只显示计划。本轮已执行完成，只允许一次 HTML generate 和一次 adopt；不应重复运行。它核对实际部署、历史证据、未采用前导出 409，以及采用后 ZIP 中的 SVG V2／新 HTML。临时 token 文件已删除；HTML 生成＋收集 92.654 秒，整个尾段 95.51 秒。HTML 源码 SHA-256 为 `1bbf454b193be3e74acd1e13583423161c62f5691bc44f71410455a390af3cdf`。真实浏览器上一／下一、重新载入后 1/2 自动至 2/2（配置 10 秒，观察 10930 毫秒）及暂停后手动切镜通过，记录于 `browser-review-final-r8.json`。完整 1280×720 HTML 可读；固定画布在 1280×656 播放器区域会裁底部 footer，自适应缩放仍待优化，不能声称全视口无裁切。

## 不能改写的历史

r4 丢弃 SVG 提交回包后，自定义 lookup 503 被旧工作台误判为失败。r5 先做受控人工修复，再在固定修复镜像窗口验证查回原 Job 与重启恢复，新增 Jobs POST 为 0。不得将此写成 r4 全程自动恢复通过。

scene 发布首次执行在 release_guard 超时停止；当时只创建配置、预留 UID 区段 46200000 并暂停 dispatcher，API／模型代理仍为 aff85。经审查的人工续接已完成并 Ready；原失败记录保留，不能声称全程自动发布通过。

r6 HTML Job 保持 failed／worker_output_invalid，无发布 manifest。原模型 write 参数中恢复的 HTML 可供离线复核，但 Worker 临时目录已清理，不声称独立读取到了原磁盘文件。r7 仍为部分尾段通过、HTML 失败、导出受阻；后续成功另作记录，不覆盖原报告。

## 证据和备份

- r7 证据：`evidence/business-partial-tail-r7/`，包含修订与真实取消停止证明。
- r8 证据：`evidence/business-html-charset-r8/`，包含新 HTML、明确采用、正式 ZIP 与结构检查；原 r6／r7 不覆盖。
- 本地归档：`.runtime/video-release/business-evidence-r7.tar.gz`，SHA-256 `ebc8cf9c3eda9421cef028091466f0d9ff10012d2651597d8486d6162eeb1b42`。
- 最终备份：`evidence/sqlite-backup-restore-ecc4ee533b964ceba7ace656020bc9c3/`，projects 3、operations 6、versions 3，包含新 HTML；备份／恢复 SHA-256 均为 `6c82e59c8b8e7eecad02cfd4d89a0c967b22c38f2d1ad85c893745244456f885`。只读快照、独立恢复、全部表行数、完整性与 hash 均通过，未回写生产 PVC，备份临时文件已清理。
- 历史备份 94d 包含 versions 2、operations 5，继续保留，不作为最终快照。
- 最终归档：`evidence/business-evidence-final-r8.tar.gz`，650306 字节，144 个源文件加内置 manifest，共 145 个成员；SHA-256 `051ee361c4cd486ebb481e756e9d7356e9a85b5cc5f8327cc751e178f72bdd7e`。服务器写后逐成员验证和本地下载整包 SHA 已通过，数据库／凭据排除；内嵌 r7 归档全部 247 个成员也已校验。
- 清理回执 `acceptance-cleanup-r8` 为 `cleaned`，精确处理 11 个本次目标，PVC 与工作台均保持不变；3 个自有端口转发停止、剩余 0。
- 公开 Jobs usage 仍为 null，费用未知；不留没有可信来源的 token 汇总待办。运行超时和精确槽位释放时间未实测。
- OpenSpec 规格／设计／任务已整合，18 项完成，验证通过且无问题；未执行 apply 或 archive。

入口为 `https://video-service.panghuer.top`，Casdoor 回调为 `https://video-service.panghuer.top/oauth2/callback`。这些配置已由用户完成，后续不重复要求配置。实际工具和测试范围见 [VALIDATION.md](VALIDATION.md)。
