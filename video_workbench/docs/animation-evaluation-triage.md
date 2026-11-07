# 自动评测问题定位与开发者处理

2026-11-07，分支 `codex/video-animation-lab`。本轮把已有自动评测接到本地开发者工作台：打开实际比较的两个冻结版本，查看机器指出的问题，再决定是否值得修改。普通视频制作流程不增加步骤。

## 打开已有评测

在应用仓库根目录启动工具，显式指定已有资源、数据与评测目录：

```sh
python -m video_workbench.animation_lab \
  --data-dir video_workbench/rendered/feedback-20261106/animation-lab/data \
  --assets-dir video_workbench/rendered/feedback-20261106/animation-lab/assets \
  --evaluations-dir video_workbench/rendered/feedback-20261106/animation-lab/auto-evaluation \
  --port 8766
```

打开 `http://127.0.0.1:8766/`，从左侧“自动评测报告”选择一次运行。目录必须已有可验证的 `run.json`、技术记录、冻结审阅请求和实际采样图；只有准备输入的运行尚不能成为可分诊报告。损坏或不支持的运行不载入为可操作案例，直接请求相关接口会被拒绝。

评测案例和原有固定样例分别保存身份。即使名字相同，预览仍使用原评测中冻结的产物，不能用当前编译器重新生成一份画面来替代。GSAP 或字体摘要不匹配时预览不可用，可以查看验证通过的原证据帧。

## 核看并处理问题

“自动评测与问题处理”分别显示技术检查、视觉模型和开发者决定。模型问题卡保留原描述、类别、严重程度和引用画面；点击证据可定位版本及时间，并显示模型给出的区域。没有定位证据的技术失败会说明无法定位，不能据此伪造截图。

开发者可以补充说明，并选择：

- **确认需修改**（`needs_fix`）：进入当前待修复集合。
- **此项不成立**（`dismiss`）：保留原机器意见，同时记录开发者认为它不需要修复。
- **暂缓判断**（`defer`）：保留问题，等待更多证据或后续核看。

新决定追加到本地数据库，同一份报告和同一个问题的最后决定为当前状态。原意见与历史决定不会被覆盖；顶部“导出标注 JSONL”同时包含评测快照和完整处理历史，附 `effective`、`superseded`。源报告发生变化后，旧判断保留在历史里，不迁移到新画面。界面及接口检查报告身份，过期写入、原帧请求或任务导出返回冲突。

`tie/no_change` 和空问题列表表示该次模型审阅没有提供问题，不制造待办，也不触发新调用。未调用、失败、证据不足及技术检查未通过各自显示；这些状态不能替代人工效果判断。

## 导出局部修改任务

至少确认一项需要修改后，可以点击“导出已确认的修改任务”。JSON 只包含当前 `needs_fix` 问题；已改判为不成立或暂缓的项目不包含在当前任务中。

任务携带源运行、报告、证据、语义输入、原音频与产物摘要，包含原始计划和开发者决定。它要求修改后重新编译、保留内容和音频、执行原适用观察检查，再做同输入对比。框图的现有可自动编辑范围仍限于 `flow/focus` 布局与对应槽位；过程与跨镜问题标为 `developer_design_required`，需开发者先实现相应修改器。

导出状态是 `prepared_not_executed`。它没有自动调用 Agent、模型或渲染器，没有修改生产模板或采用状态。任务 JSON 也不是包含音频、字体和完整运行环境的离线归档；复现仍需保留原冻结运行和摘要匹配的资源。

后续显式准备评测时可沿用 `--feedback-db`，读取同一数据库中的人工标注及分诊结果。只携带产物匹配且当前有效的 `needs_fix/dismiss`，暂缓决定不作为改进指导。意见仍是有来源的参考数据，不是模型指令，也不会自动变成全局规则。

## 本地接口

| 接口 | 用途 |
| --- | --- |
| `GET /api/evaluations/{run}` | 读取身份校验后的报告、问题及当前开发者决定 |
| `GET /api/evaluations/{run}/frames/{frame}?report_identity=...` | 读取绑定当前报告的原证据图 |
| `POST /api/evaluations/{run}/decisions` | 追加 `report_identity/finding_id/decision/comment` |
| `GET /api/evaluations/{run}/revision-task?report_identity=...` | 导出已确认问题的局部修改任务 |
| `GET /api/export?format=jsonl` | 导出人工标注、冻结评测快照及完整分诊历史 |

写操作继续由同源页面携带本机令牌。数据保存在显式数据目录的 `animation-lab.db`，不写业务项目数据库。读取报告、播放冻结预览、保存决定和导出都不提交付费模型任务。

## 当前验证与边界

集成检查为 **225 项 Python、11 项 Node 通过**。覆盖冻结身份和证据校验、问题引用、原帧绑定、历史及当前有效性、陈旧报告冲突、导出范围、反馈筛选，以及前端切换和异步状态边界。

真实浏览器已打开原 `diagram-r1` 冻结比较：当前框图 compiler 6 与 `layout-focus` 两侧保持原身份，技术检查通过，实际模型意见仍为 `tie/no_change`、零问题。页面如实显示无需处理，没有为演示而插入模型问题。截图为既有证据目录中的 `auto-evaluation/triage-diagram-browser.jpg`。

过程样例 `process-retry-r1` 已完成两版各一次真实原生采集，运行身份为 `b34d06b53a675b5653bab71dc61cc58b3756709543584b69e3a0ace7d31a7f9e`。历史过程 compiler 1 与当前 compiler 2 复用同一内容和24秒静音时钟，每版51张采样图；两个隔离容器均已移除并确认不存在。实际使用只读核验的已有镜像 `latest@sha256:a437f7cb8c3f50bfd0cf08441bd341ec153568e418246c484b9dd64d22f33800`，没有拉取镜像或重新编码视频。

本次技术结果为：当前版通过，历史版出现 blocker `process_observation_sample_missing`。这是历史观察资料不满足现有采样合同的技术失败，不直接证明其画面观感差；该问题没有精确帧定位，应按未定位技术卡展示。冻结审阅请求身份为 `164dbf12ce283b12f1d05bd798c258c26a85310ac9ba5e8beadf6aa687c4efec`，包含12张有来源的图片，但技术失败后按本次有界实验约定停止，**没有提交模型请求或自动重试**。停止原因及零调用记录保存在原运行目录的 `stage-stop.json`。

真实浏览器已完成技术问题卡的处理闭环：先确认需修改，刷新后状态保持，下载得到包含1个问题的精确源修订任务；改判为不成立后导出入口隐藏，随后暂缓并刷新，当前状态保持 `defer`。导出的任务绑定上述源运行，报告身份为 `4c859d00a4f91bdb8093fa7f719545f2658aebbe4a4f5329d20727ee84374095`，状态 `prepared_not_executed`、`agent_calls=0`。完整JSONL保留三个决定，有效性为 `[false,false,true]`；最终暂缓后供下一轮使用的有效反馈为0，没有插入模拟问题或新增人工动画标注。这些决定用于技术验收，不是用户对画面效果的审美认可。

浏览器回执、当时下载的任务与截图分别为 `auto-evaluation/triage-process-browser-validation.json`、`triage-browser-revision-task.json` 和 `triage-process-browser.jpg`。完整历史导出另存为 `triage-browser-history.jsonl`，含原有标注、冻结快照和本次三条分诊事件，后续清理时应与长期数据库一起保留。带模型问题的原帧及区域定位由19项集成检查与前端边界测试的明确夹具覆盖；本轮没有取得真实模型问题的浏览器验收。真实框图零问题、真实过程技术失败及模拟模型意见分别保留来源。

OpenSpec 独立变更为 `add-video-animation-evaluation-triage`，本轮限定范围的实现、技术检查及浏览器验收已完成。旧生产效果验收、真实旁白同步与自然度、Canvas/Three.js 能力不随本轮完成。本轮未合入主线或部署服务。
