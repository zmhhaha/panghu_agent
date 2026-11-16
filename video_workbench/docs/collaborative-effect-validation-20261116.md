# 职责协作创作：发布与验证记录

2026-11-16，开发分支 `codex/video-capability-experiment-redesign`。协作创作工程、干净源码构建、技术准入和目录上线已经完成；本轮同题 Canvas 业务重试完成并产生新样片，但水墨画质仍未达到目标。技术候选、业务生成、连续运动观察、开发者认可与 Skill 发布分别记录，不互相代替。本记录不代表已经合入主线。

## 运行架构与版本

复杂效果由通用 Agent / Pi 在同一委托及预算内依次执行 `plan → component → integrate`，组件按依赖关系有限并行，整合结果交给原 HyperFrames 渲染链路。宿主负责协议、身份、预算、恢复和验收边界，不手写动画替代 Agent 创作，也不把协作作为未准入时的静默回退。

首轮应用源码 `ec152213cc7b0d5b25bb6aa0704a8242f2936767` 经服务器干净 Git checkout，使用普通 Dockerfile 构建并推送 worker、control、executor、lab 四个 `latest@digest` 镜像；五个目标 Deployment 完成确切 spec、实际镜像摘要及源码核对。原 86 个 host、旧 binding / worker 镜像、旧 caller 权限及 private 结构保留；新增独立协作 caller，17 个 SQLite 在线备份通过 `quick_check`。

随后只更新 Worker 的 HTTP 等待设置，修复提交为 `0ad1e3e558f916ba65c8b139ec618750b43f26a8`。这次仍为普通 clean Git 完整源码构建，没有 application overlay。

| 角色 | 此次 transport 更新 |
| --- | --- |
| Pi Worker | 新镜像 `arm-cluster-master:5000/agent-service-pi:latest@sha256:9c61ed62cc36dcbd616300fd05cd62cd1502b85fc46ab946def70607412cd061`，来源为修复提交 |
| control | 复用首轮应用镜像及其原源码身份；dispatcher、model-proxy、API 仅切换公共目录 ConfigMap 指针及目录注释 |
| 创作 executor / 动画实验室 | 复用首轮镜像；此次未重建、未改 Deployment spec |
| HyperFrames renderer | 沿用原镜像；此次未重建或部署 |

三个控制 Pod 的应用源码身份仍是首轮提交，不能把新 Worker 的 revision 写成它们的新应用版本。镜像统一使用 `latest`，确切发布身份保存在 labels、annotations、digest 和原始收据中。

## HTTP 等待修复与真实技术验证

已确认旧 Pi 0.99.2 的 `httpIdleTimeoutMs` 缺省为 300000 毫秒。它同时影响 HTTP dispatcher 与显式 provider SDK 超时，因而能够覆盖 OpenAI SDK 的 600 秒默认；原 adapter 生成的独立任务设置漏写了该值。该机制与首轮约 300 秒失败一致，但尚未单独证明历史 unknown 请求只有这一个原因。

新 adapter 将该设置写为本次既有绝对截止时间的正整数剩余毫秒；设置阶段耗尽预算则在启动 Pi 前以 `timed_out` 拒绝。会话和 provider 重试保持关闭，独立 absolute deadline、取消与进程树停止约束保留。没有修改 llm-service、固定 Pi 入口或默认模型，也没有通过无限等待或自动重试绕过任务预算。

实际新容器内运行 adapter 和无网络合成子进程，600 秒、60 秒、600 秒任务受 20 秒截止时间裁剪三种场景分别产生约 599998、59999、19999 毫秒设置；运行中截止和取消均能及时停止，合计 5 个离线场景通过。对应镜像源码字节与干净源码清单一致。这些检查不调用真实 provider。

新的 `candidate-collab-transport-r1` 真实运行 plan，使用 4 次模型请求；worker / proxy 均正常退出、重启为 0，过期租约返回 401。独立准入为 disabled、仅技术组合准入，绑定新 Worker 摘要。它没有执行 component / integrate、渲染、配音、完整视觉观察或 Skill 发布，不能作为同题业务效果改善的证据。

root 随后完成 `activation-transport-r1`：仅三个控制 Deployment 切换公共目录，实际 spec 与运行镜像摘要匹配；原 caller 和 Secret 引用、旧 host / binding / pool / UID 保留，新 UID 永久追加预留，17 库再次备份通过。此操作没有读取或写入 Kubernetes Secret 对象；只读数据库核查在 Pod 内使用既有数据库认证，未导出凭据。

现用挂载凭据的只读 probe 完成 8 次 GET，新协作 caller 的单 profile scope、独立 token 关系及新 binding 与准入一致；原 methods / creation 作用域保持。probe 没有提交任务、上传输入、请求模型、渲染或修改路由。

## 同题业务验证：新样片完成，水墨质量仍有差距

首轮委托 `e548a419-59ef-458d-9c16-12a8c718183a` 的 plan 和三个 component 成功，其中两个组件确有并行重叠；integrate 在 313.516 秒以 `model_error` 失败，没有整合产物或本轮新 candidate，render / review 均为 0。原成功部件与失败身份保留，没有改写成成功。14 次模型 admission 中 13 次 complete、1 次 unknown；provider 上报 usage 不等于可信计费，金额未知。远端停止与 worker allocation 回收已有只读证据。

新 Worker 上线后，root 通过正常结构化 API 提交唯一同题 Canvas 重试：

- request：`cad14119-2cca-49a8-a0d5-b7a8dbea1e67`。
- commission：`46458bff-5ed0-4bd0-b5a0-2b04f8fa76cd`，最终状态为 `completed`，停止原因 `budget_exhausted`。
- 预算：最多 3 个组件、2 个并行，creator 5 / render 1 / review 1，1800 秒，至多一条最终样片；这是业务操作额度，不能当作 provider 模型请求数。

plan、三个 component、integrate、一次 render 和一次 review 全部完成；总墙钟约 1083.832 秒，两个组件实际并行重叠约 138.430 秒。新 candidate 为 `0c930ade-4fff-41d0-a6ea-23e7521d9951`，作者为本次 integrate 操作 `2e9d72ee-d006-400f-9c89-5c05045bdd76`，不是页面继承的旧 `best_candidate`。只读原记录核对了作者、输入及 candidate / integrate 源码身份一致；整合源码包含 `scene.html` 与 `scene.js`，实际源码文本合计 23726 个 UTF-8 字节。

本地原始源码按 canonical JSON 复算，与 integrate 和 candidate 的原摘要一致；逐文件字节数和摘要也匹配。新 MP4 原字节 SHA 为 `9b616266068b2013f50e20975910fc68fc56d7084eee12cfdbd428e25ce134b0`，区别于旧样片；实际为 24 秒、1280×720、24 fps、576 帧 H.264，完整解码通过。完整解码证明媒体有效，不代表逐帧审美观察。

实际模型请求为 18 次，全部 `complete`、unknown 为 0；provider 上报 input 377877 / output 35848 / total 413725 tokens，属于未核实 usage。可信计费用量、价格和金额仍未知，不能把 token 数换算成已确认费用。所有 Agent 任务单 attempt、已停止且 allocation retired；原渲染容器的绑定与清理收据证明容器已移除。观察没有另发任务、取消或清理操作。

root 在浏览器从 0 播放至末帧，记录 0.230、19.8319、23.958333 秒处的状态，并保存新旧对照截图及 4 / 12 / 22 秒抽帧。新构图和山体体块已区别于旧版的线束观感，但目前仍有明显多边形块面、折线裂缝与笔墨细节不足，不能宣称达到水墨要求、通过开发者审美验收，或因审阅任务完成就认定画质合格。稀疏时序观察不足以证明全部笔墨生长、薄雾或鸟群运动和每帧稳定性；也没有听取音频。

本次原始媒体、源码、成本局限、实际对照和停止证据已归档。P.5可据此更新，表示有限同题实验及评估记录完成；它不表示水墨质量达标、开发者认可或 Skill 已发布。没有进行同模型同预算的对照消融，不能单独把美术差异归因于职责协作，也不能由此宣称成本效率提升。预算已按原委托用尽，不自动继续下一轮。

## OpenSpec 与证据入口

映射项目为 `ec663f5a-53d3-4a0e-9037-933eb788d183`，change 为 `evolve-video-agent-creation-and-effect-library`。已按原 revision 完成 CAS 同步并通过服务端验证，新 revision 为 `22ac0f8d3014fdd99dbfb8ed48f7bf97ee722074`。本次仅补勾 P.4、P.5，76 项中 49 完成、27 待办；P.6继续未完成，水墨质量未达标的结论随记录保留。

P.6 的先行关键画面渲染、检查与阻断门控仍未完成，`keyframe_times` 或最终成片抽帧不能代替。此前整库 WikiSkill 业务编译 **503 项输入、900 秒超时、`release_id=null`** 的独立未完成结论保持；不得因本次修复、准入或水墨样片代勾。

服务器收据位于 `/root/video-releases/collaborative-effect-20261116/`：首轮 `build-r1` / `candidate-collab-r1` / `admission-r1` / `activation-r1`，新组合对应 `build-transport-r1` / `candidate-collab-transport-r1` / `admission-transport-r1` / `activation-transport-r1`。原字节捕获、SHA 校验、caller probe、业务提交和只读观察位于 D 盘同名协作工作目录；本轮 `ink-collaboration-transport-r1-` 前缀的 `source.json`、`media-verification.json`、`comparison.json`、`cost-and-stop.json` 与 `observation-final.json` 提供原始证据，汇总为 `business-checkpoint-transport-complete-r1.json`。OpenSpec 同步回执为该目录的 `openspec-transport-final-review-r1/sync-result.json`。
