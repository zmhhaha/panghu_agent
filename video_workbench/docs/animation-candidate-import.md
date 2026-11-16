# 历史隔离候选的单轮台账导入

`video_workbench.animation_candidate_import` 是开发者操作工具，用于把已经停止的
`candidate-live.py` 一次性候选执行结果接入持久化单轮台账。它只读取历史导出，
不提交 Agent、不调用模型、不启动渲染、不启用生产路由，也不更改原始源码或媒体。

这条路径明确标记为 `isolated_candidate_import`。它不能作为生产 HTTP 提交、
正式网关端到端接通、生产准入或人工效果通过的证据。正式网关有独立的验证路径。

## 输入与可信边界

从可信的操作员工作目录导入，保留以下结构：

```text
ROOT/
  base-source.json
  creation-context.json
  review-selection.json                 # 可选，选择已冻结的评审输入目录
  review-original/inputs/...            # 默认评审输入
  review-original/evidence.json
  review-original-offset/inputs/...     # 选择其他采样窗口时使用
  review-original-offset/evidence.json
  observation-reference/inputs/...
  observation-reference/evidence.json
  candidate-animation-observe-p1/candidate-output/...
  candidate-animation-review-p1/candidate-output/...
  candidate-animation-create-p1/candidate-output/...
```

每个候选的 `candidate-output` 必须保留请求、结果、控制器证据、实际停止后的
Worker/Proxy Pod JSON、操作员摘要、禁用状态的 Host 配置、场景及执行清单；
完成的候选还需保留原始结果文件。缺失、不一致或仍运行的证据会被拒绝，
不能据此新建一个替代尝试。

导入器校验原始输入字节及摘要、Profile/执行绑定/镜像摘要、真实任务与尝试身份、
控制器结果摘要、停止回执字段、Pod 镜像和停止状态、权限、执行命令及挂载范围，
以及结果与业务输入的绑定。视觉角色还必须有模型代理记录的实际图片传输证据，
并绑定同一任务、尝试和请求。提供了帧文件或模型自行声称“已查看”均不够。

候选控制器在接受并导出结果前已使用其私有密钥校验停止回执签名。
导入器没有该密钥，**不会声称本地重新验签**。它检查导出之间的一致性，
不能把任意不可信 JSON 升级为真实运行证明；操作员应保留原始导出及来源。
`stop_signature_verified_locally` 始终为 `false`。

`completed_upstream_transport` 只证明图片随成功的上游请求实际传输，
不证明模型正确理解了画面、看过整条视频或听过旁白。导入保留这些限制；
抽帧窗口之外的内容不能被当作已观察。

## 分阶段导入

在 `panghu_agent` 目录运行。`--expected-image-digest` 必须由操作员从本次已审阅的
镜像构建记录取得，格式为 `sha256:` 加 64 位十六进制摘要，不能填 `latest`。

先等待独立评审候选停止并完整导出，再导入评审：

```sh
python -m video_workbench.animation_candidate_import \
  --root /path/to/agent-creation-phase1 \
  --owner phase1-operator \
  --project-id cohort-phase1 \
  --expected-image-digest 'sha256:<本次构建的64位摘要>' \
  --stage review
```

占位摘要必须替换后才可执行。检查输出中的 `review_status` 与真实评审意见；
只有评审完成且存在具体问题时，才由独立的一次性候选流程准备并执行一次修改。
导入工具本身不授权、准备或执行该模型任务。停止并导出修改候选后运行：

```sh
python -m video_workbench.animation_candidate_import \
  --root /path/to/agent-creation-phase1 \
  --owner phase1-operator \
  --project-id cohort-phase1 \
  --expected-image-digest 'sha256:<同一次构建的64位摘要>' \
  --stage all
```

`--stage all` 先校验参考观察候选的完整导出，再校验评审与修改，全部合格后才分配台账名额。
参考观察仅进入审计，不代表用户验收，不会伪装成独立评审，也不会消费另一个评审名额。没有具体问题时不导入修改，
后续技术核验使用原始源码。`--stage revision` 可在只需补入修改时使用；
评审仍会被重新校验并要求与已冻结记录完全相同。

默认从 `ROOT/review-selection.json` 的 `directory` 读取评审输入目录，例如：

```json
{"directory":"review-original-offset"}
```

文件不存在时使用 `review-original`。也可显式传入
`--review-directory review-original-offset`；相对路径相对于 `ROOT`，
绝对路径可指向另一份已冻结的输入。选择的是原始输入，不会重新采样或替换其摘要。
选择优先顺序为：显式 `--review-directory`、`review-selection.json`、默认 `review-original`。
已经导入的评审不允许通过换采样窗口或改源文件覆盖。

## 输出、重复执行与失败

导入器只写入 `ROOT` 下三个位置：

- `revision-ledger.sqlite3`：每个血缘最多一次评审、一次修改和一次独立技术核验。
- `candidate-import-audit/`：按内容摘要命名的不可变来源与导入回执。
- `import-receipt.json`：本次导入的摘要，包含 `lineage_id`、所选评审目录与输入摘要、阶段状态。

台账保存候选原有的任务 UUID，记录 `isolated_candidate_import` 审计事件；
不补造 `submission_claimed`、`submitted` 或其他历史 HTTP 提交事件。
同一来源、输入和结果再次导入是幂等操作。已导入来源发生改变时返回冲突，
不会替换旧结果或重置额度。

控制器已记录且停止证据完整的失败候选，可作为失败导入并消耗对应名额。
CLI 返回非零退出码。证据不全、任务身份不确定和结果不明均不能被解释为
“未执行”，不能自动重试模型。若导入尚未写完审计文件便中断，修复文件系统原因后，
可使用完全相同的导出和参数重跑导入；这不会产生新的模型任务。

导入回执始终明确：`new_model_submissions=0`、`live_http_verified=false`、
`verification_started_by_importer=false`、`aesthetic_status=unreviewed`。

## 接续一次技术核验

由单独的操作员执行器读取 `import-receipt.json` 的 `lineage_id`，打开同一个
`revision-ledger.sqlite3`，把 `ImportedCandidateOnlyGateway()` 同时注入
`AnimationPolishCoordinator` 的评审与创作网关。该防护对象拒绝所有上传、提交、
轮询和收集操作，防止遗漏阶段意外发起模型请求。

调用 `step()` 时继续传入原始 `creation-context.json`、`base-source.json` 和
同一评审输入目录。独立渲染适配器实现 `render(operation_id, source, creation_input)`
及持久化的 `lookup(operation_id)`，保存与源码摘要绑定的渲染和技术检查回执。
台账只允许一个核验操作；发生提交结果不明时先查询原操作，不能创建替代渲染。
技术核验不会触发第二次 Agent 评审，技术通过也不会自动标记美学效果通过。

专用导入测试使用合成的停止导出，不会运行 Pod、模型或渲染器：

```sh
python -m pytest -q -p no:cacheprovider \
  tests/agent_service/test_animation_candidate_import.py
```
