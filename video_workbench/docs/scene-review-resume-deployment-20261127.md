# 场景审图恢复发布记录（2026-11-27，质量失败、历史已结案）


框架部署、同内容原始 PNG 传输和正式审图执行已验证；实际画面未达到审图质量门槛，run 安全失败且没有导出影片。6.1 的正式影片验收仍未完成，用户艺术认可也未发生。

## 问题与改动

原三秒场景任务在任何模型调用前因四份原始 PNG 超过旧共享网关 3 MiB 限制而失败。四份各为 838118 字节；同时，保留的 Worker 原生 JSONL 事件存在独立 4 MiB 限制。仅扩大 raw 文件预算不能解决后者。

这次改为按精确 v2/revision 合同校验原文件，再预检实际原生封装。新冻结的场景配置可将同钟同内容、原字节相同的 clip0 捕获关联到静态0帧；不同绝对帧保持独立。原失败任务可以通过受约束、最多一次的 successor 消费剩余第一次审图和一次导出，不重画、不重置预算。原窗口已过期时必须使用另一条显式新窗口授权接口。

## 14 个源码文件

| 文件 | 作用 |
| --- | --- |
| `video_workbench/animation_review_gateway.py` | 对显式合同引入文件预算钩子，保持 legacy 默认行为。 |
| `video_workbench/picture_learning_gateway.py` | 仅精确 v2/revision 选择现有预算，并在上传前执行原生事件预检。 |
| `video_workbench/picture_review_transport_budget.py` | 独立、纯计算的 4 MiB 原生事件保守上界。 |
| `video_workbench/scene_review_canonicalization.py` | 按完整冻结逻辑帧和原字节选择供应观察，保留原捕获关联。 |
| `video_workbench/scene_source_import_resume.py` | 原窗口 successor、CAS、余量携带及三份独立图像回执验证。 |
| `video_workbench/scene_source_import_resume_window.py` | 独立的新窗口请求/证书，原 grant 和原授权不变。 |
| `video_workbench/scene_space_store.py` | 在新 task 中继承原事件、reservation、完整冻结方案和原未知祖先账本。 |
| `video_workbench/production_run_store.py` | 只读预检、跨模式唯一 successor 和严格幂等/CAS。 |
| `video_workbench/scene_space_production.py` | 配置显式版本、已完成渲染复用、输入冻结、模型及导出约束。 |
| `video_workbench/app.py` | 两套 GET/POST API 的认证、写权限、严格请求模型和幂等入口。 |
| `tests/video_workbench/test_classified_review_input_budget.py` | 文件边界、原字节上传、legacy 保留、超原生封装的上传前拒绝。 |
| `tests/video_workbench/test_scene_review_canonicalization.py` | 22 个同钟选择与不可合并/身份变化检查。 |
| `tests/video_workbench/test_scene_source_import_resume.py` | 恢复资格、CAS/唯一性、窗口权限、预算/原记录保留和未使用分配。 |
| `tests/video_workbench/test_scene_space_production.py` | 测试 renderer 回执补充实际请求、source、plan 身份字段。 |

## 验证边界

原生 PNG/网关关联验证 135 项通过；纯同钟选择验证 22 项通过。这些批次与其他集成验证存在覆盖交集，不累加为独立用例总数。独立 reviewer 另以 ce9 原四份 PNG 和冻结计划离线验证实际三份观察及保守封装上界；零模型的真实保留 Worker 工具/事件链验证独立留存。实际业务审图与完整导出应在后文单独记录。

发布采用普通 Dockerfile 和完整、干净、已发布 Git revision，使用 `latest` tag 加 digest 固定镜像身份，不创建额外版本 tag。完整源码清单含 1212 个测量条目；实际镜像核对 Workbench 25 个、executor 11 个运行源码指纹。没有基于运行镜像覆盖应用源码。

## 当前核验的镜像与 Pod

| 角色 | 源码与镜像摘要 |
| --- | --- |
| Workbench | `9497b879a7cc199814ddf9c838a15e95c9246429`；`arm-cluster-master:5000/video-workbench:latest@sha256:fe9783d97ccf54dd211ec3bef9f47bc5015fc8d93a5afb804af47c2853f080d6` |
| Executor | 同一源码；`arm-cluster-master:5000/video-animation-creation:latest@sha256:02f42ddb13dae6111bd2e67b5757e93c14c1e1bd33239dc4880cb60e5915bc70` |
| API / dispatcher / model-proxy | 保持 `5aeaa5cecdc19672d7d0a240c04728ce7865678c`；control digest `31e2c1436815e6b3b76b6ed739d5c1e1d8cf644a21ad5cd6898df79d93449fbf` |
| Worker | 保持原准入镜像 digest `6db203923a15eb7e68f1585fdcdaec04481a4d90fbc07bb9e94c35f985631412`，没有重建或新冷准入。 |

Workbench Pod UID 为 `f02e4baa-1adb-4a81-b685-b5520a105424`。五个实际角色的容器 digest、运行源码指纹、Pod wiring、Ready 和旧 ReplicaSet 归零已核验；新 scoped caller 和 CPU Canvas PNG 能力的只读 GET 通过。

原 execution binding `aee83e77ad034330709b09e450cc5bf27d775216983a16aeb3c573e42962f02f`、原 public catalog、UID 池、现有 token 和控制服务没有改变。Workbench/Executor 只通过 UID、完整原 spec、resourceVersion 的 CAS 更新容器镜像及三个来源注解。

## 旧整片功能保留

实际新 Workbench 镜像已在 `network=none` 下读取原 immutable film/source 配置和原已准入镜像的九个 public assets，完整执行 `FilmProductionSettings.from_env` 与 Manim 配置 smoke。原 legacy v3 host 摘要 `0b58f10bac9b21301e788422e19f445e547cef5e6fe1d8de024838b84f1bb3ba` 保留，当前 v4 host 也保持。没有关闭整片功能或替换旧执行准入来规避启动错误。

## 证据索引与业务结果

部署前使用 ce9 结案后重新采集的全新 quiet/history，before 摘要为 `0da3e481c9825cc1abda9f5afa98edeaa589b779256b155ffd14e9ef6ef8ef81`，不复用旧发布闭包。实际部署 live 回执摘要为 `ad14b3d8e6104c3f754c6c616ec958691a97599aea3df5c223dd008617561a16`。

部署后的 guarded 与 raw-strict 比较均通过，新增、更新、删除 allowlist 均为零：after 摘要为 `38782e284efe0a248c9c959e5b00303f8ce9156d805fe667193832735d0f7f4a`，guarded 比较回执摘要为 `bf88704b939ec1b627d642a2ac111c2f886933e476a026569eab289e80b8997b`，raw-strict 摘要为 `7f36099b23c65122228c24b056e0bf88fac578bf942cb7561badba6fe7ccbbfe`。

## 本次独立窗口的实际提交、失败与停止

部署后先另行采集业务执行前全量历史，摘要为 `ceb519cf4c9a47f01d3798957002e4a95c4978df6fe448f53843a95ac5c872f5`。该业务 before 与前文零变动的部署 before/after 分别保留。

实际候选 binding 为 `b96d9927fc9df1ffd3c6001133eed35781cc492069b6cff3447e800e830b3e97`；独立新窗口授权为 `fd0d9d4e0197f204695a0a059961e260cfac095ffb10aa7cbfbaf7c1ddce1ea1`。GET 派生准确请求摘要 `90f907f8fe722f81391dae86295a117b1424d3f48c793ff185126953ac239587`，保留原授权和原截止时间，并单独记录新 phase 截止时间 `1795790065.1829803`（北京时间 2026-11-27 22:34:25）。

唯一一次 POST 已承接到 `95502699-ee68-4a3b-8f3e-5b5caa76a19f`，使用 GET 推导的唯一共享键 `scene-source-import-resume-340c8d9148a0c64d30fb571433ff0474cb12e6cc318f6c23`，回执 `post_replayed = false`。监视进程已启动，只观察/按授权停止这个新 successor；不能重发 POST、更换键或停止旧父任务。

实际审图 job `51cfa0f2-d30d-41cc-afa6-c33bb91ae149` 已完整 `completed` 并证明物理停止，3 个 provider 请求均完整返回 HTTP 200。最终 authoritative usage 为 complete：input `64663`、output `1295`、total `65958` tokens，1 个 attempt。该模型任务的执行成功没有被解释为艺术质量合格。

successor `95502699-ee68-4a3b-8f3e-5b5caa76a19f` 的真实终态是 `failed`，错误 `scene_space_preview_quality_not_met`；scene 为 `cancelled`、`stop_proven = true`。独立 monitor return code 为 `0`，证明原三次已完成渲染和新审图 job 均已停止，原 12 条受保护业务记录及 7 份原始 auth 文件保持不变。

本轮没有新 author、preview、repair、revision 或 export；累计渲染支出保持原 `49.178791753482074` 秒。没有 MP4 导出，因而不存在本次影片完整解码/媒体交付通过的声明，也没有自动换内容或生成修画候选。

终态与停止回执摘要为 `c6f458dae4bfe4bc471c2eeee8f9c1c679ed0655f9e2ea35fbf0c86a91c6c5c7`（`formal-resume-window-status-r4.json`），最终 job 与用量只读回执摘要为 `3f8825f54d274c56dbc0d55208cae3d79958121649570c2773cf2053f6c33be3`（`formal-resume-window-job-observation-r3.json`）。

本次历史 authority 仅允许 4 条 Workbench 新增，逐项保护 19 条祖先记录，禁止旧行更新和删除；摘要为 `e4f1340fc7150171ea385f57b8a628029701ae85d827a32eb12c1891d9575c55`。随后已实际完成 guarded/raw-strict 关账与私有目录归档，见后文；authority 与比较结果分别留存。

原失败 ce9、原授权/截止时间和所有旧结果不被覆盖；历史记录不自动复评。文档不包含凭据、数据库或私有原始模型输出。框架部署成功、质量失败和用户艺术验收是分别保留的状态，6.1 仍未完成。

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
