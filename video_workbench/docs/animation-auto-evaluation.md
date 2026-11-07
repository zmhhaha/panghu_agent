# 动画模块自动评测与局部改进

本轮在开发者工具中建立“固定小样 → 自动检查 → 视觉审阅 → 局部候选 → 同输入复测”的入口。普通视频工作台继续保持原制作流程，生产采用、共享 Agent 路由和正式交付门禁保持各自含义。

首批使用已有五组无旁白技术样例：框图连线退出、长标签可读性、失败重试、跨镜 retain 和 copy；随后新增真实 Canvas 队列与 Three.js 分层案例，当前共七组。它们的静音输入只提供固定时钟，不证明配音自然度或真实字词同步。新引擎使用独立的像素/场景检查，详见 [Canvas 2D 与 Three.js 开发者小样](animation-canvas-three-lab.md)。

## 自动检查和视觉意见

自动检查依据实际截图、DOM/场景状态和声明的检查范围，关注溢出、遮挡、端点关系、显隐、对象位置、跨镜状态及反向定位。单个模块只检查适用项目；缺失观察或范围不足应明确报告，不能由“未发现”推成“全部合格”。现有生产检查若被复用，保持其版本和原门禁。

视觉模型查看绑定具体时间和摘要的帧，以 A/B 盲标识比较，给出有画面引用的意见和受限改进建议。模型可以无法判断；静态帧不证明动作流畅，连续帧也只覆盖已提供的时间窗。客观检查、模型意见、开发者偏好和用户认可分别记录。

## 模型和 Agent 接口核对

| 能力 | 已从源码确认 | 仍需真实验证 |
| --- | --- | --- |
| llm-service 图片输入 | `/v1/responses` 接受 user content 中的 `input_image` 与 `input_text`；Chat 出站适配保留图片内容；本轮真实图片审阅已完成 | 其他路由、模型或题材需各自验证 |
| 模型路由身份 | 响应提供 `X-LLM-Route-Revision`；本轮 `llm-trusted` 实际为 `gpt-6.1-sol`，路由 `f93cc97a5fb1bebf8b24905b` | 路由变更后不能沿用旧结果证明新组合 |
| Agent 传输 | `AgentClient` 已有 capabilities、上传、提交、幂等查询及产物下载 | 具体任务 profile 是否准入、ready、支持所需输入与工具 |
| 既有视频计划 | 生产 capabilities 的 storyboard/scene/explanation/motion/process revision 1 均报告 configured/ready；readiness 为池准入 | 没有本轮通用 Agent 执行；它们没有自动成为图片审阅 profile |

调用方继续采用显式 `LLM_BASE_URL`、`LLM_SERVICE_TOKEN` 和 `LLM_MODEL`；服务端凭据不进入页面、产物或日志。稳定别名的配置示例与 `/v1/models` 不是视觉能力探测结果。`trusted` 只说明允许的字段，不保证供应商支持每种参数组合。审阅输入中的标注、画面文字和模型历史结果作为参考数据处理。

新闭环不得重跑此前已停止的 `motion-r2` 候选，也不借用 PPT 任务绕过新视觉评审合同。真实任务和模拟传输分别记录；一次 HTTP 200、请求 JSON 构造成功或 profile 名称存在，都不代表视觉审阅已经可用。

11月7日在现有模型代理内只读查询 `/v1/models` 返回 200，仅提供 `id/object/owned_by`，没有 `advertiseVision` 或视觉能力字段。它列出 custom、deepseek、openai 的固定别名及 `llm-guarded/llm-trusted`，其中两个稳定别名的 `owned_by` 是 custom。这个查询没有模型推理费用，也没有修改路由。

真实图片调用只在现有受信模型代理中启动一次独立 HTTP caller 进程；它在 Pod 内读取现有配置及所引用的 caller token，没有把凭据复制到开发者电脑、页面、产物、日志或采集容器。常规 CLI 可通过 `LLM_SERVICE_TOKEN_FILE` 读取单独投射的只读凭据文件，且必须与 `LLM_SERVICE_TOKEN` 二选一。采集器使用独立的网络隔离容器，不挂凭据。该接线是直接调用 llm-service，不是新增通用 Agent profile 或已验证 Agent 自主改版。

## 局部改进

每轮保留相同内容和时钟，限制候选数量以及可编辑的声明式计划、布局或样式字段。拒绝可执行代码、未知字段、越界参数和无关内容变化；候选重新编译，得到独立身份，再运行相同检查。旧输入、原基线、失败候选和选择理由继续保留，不自动改写生产模板或采用状态。

有效标注按冻结身份筛选。已撤回或被替代的意见保留历史，不作为当前偏好；单条意见也不会自动升级成全局规则。这里的后续利用是读取并使用明确的工程评测资料，本轮不训练模型。

## 当前状态

OpenSpec 初始变更为 `add-video-animation-auto-evaluation`。已实现确定性证据合同、独立 Linux/Docker 采集器、范围明确的客观检查、盲 A/B 审阅、严格输出校验、局部布局候选、一次调用预留及只读报告。准备入口支持七组案例：框图两例比较当前版本与一个有限布局候选；过程和跨镜案例比较其明确的历史基线和当前版本，尚不支持局部布局修改。后两类没有完成本轮真实自动视觉闭环。新增 Canvas/Three.js 四个版本已完成真实技术采样，尚未调用视觉模型，不代表已经实现对应的自动改版器或生产准入。

真实运行 `diagram-r1` 比较当前编译器与一个 `focus` 布局候选。双方内容、语义和固定静音时钟一致，每版实际采集 30 帧，覆盖声明的动作窗、首末帧与反向定位，严格技术检查均通过。两个采集容器均确认清理。运行身份为 `e753bfc9627589f43e78ed4629c58a1426b42bedd96e1cb4b6d6fc30265e1a75`。

随后仅调用一次实际视觉模型，输入 12 张真实绑定时间与摘要的图片，含两组各三帧的有序动作窗。返回通过严格审阅合同，意见为 `tie`、无问题、`no_change`，即在所提供证据中没有发现明确优劣。工具保留两个实际测量版本和复测结果，不为追求改动而再生成候选。没有触发第二次调用、重试、生产采用或通用 Agent 任务。供应商返回 input/output/total tokens 为 19661/560/20221；这是 provider reported 用量，不是可信计费凭证。

这次结果验证的是开发评测闭环能正常保留“无需修改”的结论，不代表布局候选质量已经优于现有版本，也不证明整段动画流畅、旁白自然度或真实字词同步。静音采集产物是浏览器采样画面与状态，并非新 MP4。

既有 `add-video-animation-developer-lab` 的五案例完整浏览器功能验收已另行完成，最终提交和交付仍按其独立状态更新，不能因为本次自动评测通过就代为完成。本轮相关 Python 检查合计 177 项通过，前端模型边界 4 项通过，覆盖身份、伪造帧、输出合同、候选越界、采集隔离、失败清理、五案例准备以及未配置模型时的零调用恢复。独立 HTTP transport 的 22 项包含在 Python 总数内，覆盖图片载荷、令牌文件、无重试/重定向、未知超时结果、大小限制及响应白名单。

## 开发者运行入口

在应用仓库根目录运行。先准备冻结的实际 runtime JSON；新运行目录必须尚不存在。以下路径只是示例，`prepare` 不调用模型或渲染器：

```sh
python -m video_workbench.animation_lab_evaluate prepare \
  --runtime /trusted/runtime.json \
  --output /evaluations/new-diagram-run \
  --case diagram-connections --template focus
```

它写入 `run.json`、运行资源身份、两版独立采集输入与受限适配器。`--feedback-db` 可选读取实验标注库，只选择身份或产物匹配且当前有效的反馈。`--case` 支持目录中的七组固定案例；`--template` 仅用于框图，候选布局只允许 `flow/focus`。其他案例省略该参数，比较固定版本，不声称已经实现对应局部修改器。`three-layers` 另需 `--engine-assets-dir` 指向摘要匹配的本地 Three.js 资源。内容、语义或音频时钟变化会被拒绝，不能输入脚本或任意样式代码。跨镜候选可以包含多个采集目录，必须完整执行。

在具备本地已验证渲染镜像的 Linux/Docker 执行环境，对 `run.json` 中每个采集目录使用独立入口：

```sh
python -m video_workbench.animation_lab_capture \
  --directory /evaluations/new-diagram-run/inputs/candidate/scene-1 \
  --runtime /evaluations/new-diagram-run/runtime.json \
  --image 'registry/video-motion-renderer:latest@sha256:<verified-digest>' \
  --overlay-dir /evaluations/new-diagram-run/overlay --execute
```

省略 `--execute` 只检查并输出计划。必须对另一版目录同样采集后，再在原运行目录收集技术结果；不自动拉镜像、不自动重试。实际路径以冻结运行文件为准：

```sh
python -m video_workbench.animation_lab_evaluate collect --run /evaluations/new-diagram-run
```

配置显式 `LLM_BASE_URL`、`LLM_MODEL` 与一个凭据来源后，才执行视觉阶段。每运行在 HTTP POST 前写入一次调用预留；已有预留会拒绝再次执行，包括未知超时结果：

```sh
python -m video_workbench.animation_lab_evaluate model --run /evaluations/new-diagram-run
```

未配置模型时可以完成技术阶段并查看报告；视觉阶段记录 unavailable，不发 POST，不消耗调用预留，可在之后提供有效配置时继续。已经真正开始的调用则受一次预留限制。不能把报告中的“尚未调用”当成模型验收。失败保留冻结请求、已经取得的回执和停止原因。报告 `report.html` 可经开发者实验服务的 `--evaluations-dir` 只读入口查看；该页面没有模型调用或采用按钮，不在普通视频工作台增加步骤。

原始输入、帧、执行日志、模型请求/响应的脱敏记录和非敏感回执统一进入既有忽略目录 `video_workbench/rendered/feedback-20261106/animation-lab/auto-evaluation/`。长期标注库及冻结资料应与可重建的临时文件分别保留。

已保存评测的交互核看、问题分诊和精确源任务导出，见 [自动评测问题定位与开发者处理](animation-evaluation-triage.md)。后续 `prepare --feedback-db` 可读取当前有效且产物匹配的开发者 `needs_fix/dismiss` 决定作为参考数据；暂缓决定和历史被替代决定不作为当前指导。
