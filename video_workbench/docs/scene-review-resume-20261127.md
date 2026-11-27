# 场景审图协议与历史记录交接（2026-11-27，质量失败、历史已结案）


本轮框架发布及正式审图传输已经验证；实际审图未达到质量门槛，successor 以 `scene_space_preview_quality_not_met` 失败并安全停止。没有导出影片，用户艺术验收未发生，6.1 仍未完成。

## 协议适用范围

| 数据/调用 | 本次行为 |
| --- | --- |
| 原图片/动画 v1 观察、审图 | 保留原合同和原来的帧文件合计 3 MiB 限制；不解释为 v2 分类审图结果。 |
| 精确 `video.picture-learning-review.v2`、revision `1` | 按既有已准入合同核对原始文件：最多 9 个文件（含任务 JSON），图像每份不超过 2 MiB、合计不超过 18 MiB。同时增加独立的 Worker 原生事件 4 MiB 预检；必须同时满足。 |
| 未准入的未来 profile/revision | 不自动继承扩大后的文件预算。 |
| 新冻结的场景制作配置 | 显式记录 `review_canonicalization_revision = same-clock-original-png-v1`，首次冻结审图输入前执行同钟观察选择。 |
| 没有该版本字段的旧冻结配置 | 继续按旧输入构建规则读取；不修改已冻结审图文档、原帧或结果。 |
| 本次 ce9 失败任务的显式 successor | 保留原四份捕获，使用新恢复证书选择实际三份观察；原失败任务保持失败身份。 |

文件预算与原生传输预算是不同限制。原生预检计入图像 base64 的精确长度、任务文本的 JSON 转义长度及 64 KiB 封装预留，在任何输入上传或付费任务创建前拒绝超限。它不调整 Worker、provider 或已准入 profile 的限制，也不证明模型已经看到了图像。

## 同钟观察选择

只将同一个绝对帧的 clip 捕获关联到对应的静态捕获，且必须同时证明：同一源代码、完整冻结方案、world、camera、viewport、runtime，以及原 PNG 字节完全相同。渲染编号和 clip 相对帧可以不同，绝对时钟必须一致。

本次实际供应的帧 ID 为 `preview-0`、`preview-71`、`clip-0-47`。`clip-0-0` 显式关联到 `preview-0`，不被描述为另一份独立看过的观察。47、71 帧即使 PNG 摘要相同，也必须保留为各自的模型图像部分。原四份捕获及 provenance 在 canonicalization 证据中保留；不重编码、不换 JPEG、不修改原字节。

审图结果只允许引用实际供应的三份帧 ID。完成回执须在同一已完成模型请求中证明三份独立图像部分，不能仅用 PNG 摘要集合代替三份观察的计数。

## 恢复 API 与授权

两个恢复 API 均提供只读 GET 预检和经原权限、CSRF、幂等验证的 POST：

- `/api/production-runs/{run_id}/scene-source-import-resume`：继承原授权和原绝对截止时间，过期后拒绝。
- `/api/production-runs/{run_id}/scene-source-import-resume-window`：仅在显式的新窗口授权下使用。请求保留原 `authorization_sha256` 和 `execution_deadline_at`，另外提供 `renewal_authorization_sha256`、不超过一小时的 `phase_execution_deadline_at`、严格为真的 `ack_unused_allocation_only`。

两条路径共享同一个按 owner、parent 和原 grant 推导的 successor 幂等键，并共享“最多一个 successor”的限制。不同模式不能各建一次任务。旧接口拒绝新窗口额外字段；不存在过期后自动改用新窗口的逻辑。

恢复资格仅覆盖：原始输入上传阶段失败、审图尚未 attempted、没有审图 job、三个渲染均已完成并证明物理停止、原 source/plan/configuration/artifacts/grant 与 CAS 摘要完全匹配。恢复只能消费尚未使用的第一次审图与一次导出。新 author、preview、repair、revision 均不允许。

原 600 秒渲染分配继承已知的 `49.178791753482074` 秒支出，余量为 `550.8212082465179` 秒。原事件及结算 reservation 逐项携带，不能重置为 600 秒。祖先的历史累计费用未知保持未知；新任务必须单独测量并满足其现有请求次数和单次输出上限。

## 历史不自动复评

本次没有遍历、改写或重新提交历史失败记录。旧 v1 结果不会被追认为 v2 合格，旧艺术失败不会被部署成功覆盖。ce9 的失败文档、原四帧、原 grant、原授权和原截止时间保持原样；恢复的审图和媒体写入独立 successor。

本次新规则属于框架的文件完整性、时钟、输入选择、预算和生命周期约束。没有发布 WikiSkill、采用效果库候选或恢复五分钟水墨影片，也没有把传输测试、独立技术审图或三秒影片视为用户的艺术认可。

## 本次独立窗口的真实失败终态

本次新窗口经实际只读 GET 预检并唯一一次 POST 提交，已承接到 successor `95502699-ee68-4a3b-8f3e-5b5caa76a19f`。共享幂等键为 `scene-source-import-resume-340c8d9148a0c64d30fb571433ff0474cb12e6cc318f6c23`；提交回执明确 `post_replayed = false`。原 ce9 未被改成重试状态。

| 证据 | 摘要/身份 |
| --- | --- |
| 独立新窗口授权 | `fd0d9d4e0197f204695a0a059961e260cfac095ffb10aa7cbfbaf7c1ddce1ea1` |
| 实际候选 Pod/source binding | `b96d9927fc9df1ffd3c6001133eed35781cc492069b6cff3447e800e830b3e97` |
| GET 派生的准确 POST 请求 | `90f907f8fe722f81391dae86295a117b1424d3f48c793ff185126953ac239587` |
| 本次业务执行前全量历史 | `ceb519cf4c9a47f01d3798957002e4a95c4978df6fe448f53843a95ac5c872f5` |
| 实际审图 job | `51cfa0f2-d30d-41cc-afa6-c33bb91ae149` |

新窗口绝对截止时间为 `1795790065.1829803`（北京时间 2026-11-27 22:34:25）；原 `1795785338.0791557` 截止时间及原授权仍在原 grant 和恢复证书中保存，没有刷新或覆盖。

独立 monitor 固定观察这个 successor 和原键，已以 return code `0` 完成终态与物理停止核验。run 为 `failed`，错误 `scene_space_preview_quality_not_met`；scene 为 `cancelled` 且 `stop_proven = true`。这是质量门槛拒绝，不是把模型成功返回误报为影片成功。

审图 job `51cfa0f2-d30d-41cc-afa6-c33bb91ae149` 已 `completed` 且证明停止；3 个 provider 请求均完整返回 HTTP 200。最终 authoritative usage 为 complete：input `64663`、output `1295`、total `65958` tokens，单次执行 attempt 为 1。原三次已完成渲染也逐一证明停止。

本轮新增 author、preview、repair、revision、export 均为零。渲染累计支出保持原 `49.178791753482074` 秒；没有生成 MP4，没有运行未通过审图后的自动修画或换内容验证。原有失败结果与用户艺术验收状态保持原意。

停止回执见 `formal-resume-window-status-r4.json`（SHA `c6f458dae4bfe4bc471c2eeee8f9c1c679ed0655f9e2ea35fbf0c86a91c6c5c7`），最终 job/usage 只读回执见 `formal-resume-window-job-observation-r3.json`（SHA `3f8825f54d274c56dbc0d55208cae3d79958121649570c2773cf2053f6c33be3`）。

历史 authority 已确定仅本次 4 条 Workbench 新增记录，逐项保护 19 条祖先记录，不授予任何旧记录更新/删除权限；authority 摘要为 `e4f1340fc7150171ea385f57b8a628029701ae85d827a32eb12c1891d9575c55`。全量业务历史严格比较与私有目录归档已完成，见后文的最终证据。质量未通过、用户艺术验收未发生，6.1 仍未完成。

## 最终历史与证据保存

业务执行前快照 SHA `ceb519cf4c9a47f01d3798957002e4a95c4978df6fe448f53843a95ac5c872f5`，终态后快照 SHA `a3c37fa0808398ae25e27c85eba6ae3cdfd55049c9e14ef273ac3b8c22063564`。

原始 strict 与 sequence guard 实际均通过；58 个数据库、9724 条既有行保持，只增加 successor 的 4 条工作台记录，没有新 executor 或 sequence 行。19 条祖先记录（12 个业务文档、原冻结计划、3 次既有渲染的 queue/native 记录）单独守护，不作为新增例外。关账文件 `formal-resume-window-history-closure-r1/closure.json` SHA `e6728063c2ecc8e631630d77de90087c94f2c520506a1471e51a8f0dbdf0efe1`；六份原始输入/比较证据与 closure 已逐 SHA 保存到服务端本次新窗口的 `history-closure-r1/`。

只读保存当前已停止私有执行目录的 30 份原件，本地和服务端 TAR 均为 2856960 字节，SHA `6a6ba5f2412f493ba266995ed0aa09e7e8d4959890a9886b0f321f036f4e60e5`。旧 355 文件档案与 ce9 失败档案保持原样；不在代码仓提交认证原件或私有 TAR。独立审查记录 `formal-resume-window-terminal-independent-review-r1.json` SHA `3222c029c444eab8ae403962d0bad983bfb4d3a6bbccbff680fcc0568adebd7a`。

质量拒绝后未调用下载/正式导出接口，没有 MP4，也不存在完整正式影片解码成功的结论。6.1 保持未完成。本次唯一新评审任务已经消费，3/8 请求没有用完不代表可以另建评审；后续不自动重试、修画、换内容、发布 WikiSkill 或合入 main。用户艺术和运动认可仍未取得，五分钟影片保持暂停。

已按用户此前的协调授权通知学习会话恢复提交，本次没有代其重试旧失败任务。历史学习结果继续保留原协议解释；本次没有启动历史视频批量复评。

## 画面质量评审

原评审得分为 72，`stop_recommended=true`，只有一个 blocking finding：`ink-wash-style-mismatch`，advisory 为空。原文指出主山与左下岩石主要由硬边灰色切面、重复浅色锯齿带和条纹构成；纸纹、松针与软雾存在，但山体仍偏切面渲染，缺少有变化的积墨和干湿毛笔边缘。

`visible-spatial-structure=satisfied`：四个不等高峰相连，后山被主山遮挡，云/前景与两棵松树的空间支持稳定。`declared-light-interpretation=satisfied`：本内容没有声明光向，光照是 optional，现有明暗提供一致的写意体积。以上是该版本、所供应样帧上的模型意见，不外推为所有空间/光照能力已获视觉验收。

因此没有为了使短片导出而降低门槛或修画，也不把技术传输成功等同于艺术质量通过。实际供模帧是 0、47、71 三个时刻；重复上下文内累计的 6 个图像记录不代表 6 个独立时刻。没有连续播放或音频评审结论。
