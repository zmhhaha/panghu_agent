# 文件、检查点和业务恢复接口

本轮数据面代码已补齐并通过本地回归，结果见 [本地验证记录](LOCAL_VALIDATION_20261026.md)。以下为实现的接口合同；真实 Linux 引擎、模型和 Kubernetes 集成结果应以服务器独立验收记录为准。

所有业务接口继续使用调用方专用 Bearer token。tenant/caller 从服务端凭据映射产生，调用方不能通过 JSON 更改所有者；输入、Job、成果、检查点、恢复记录均按此范围隔离。业务选择版本化任务合同，不在请求中选择 Worker、供应商密钥、镜像、宿主路径或可执行模块。

## 文件和额度

`POST /v1/inputs?name=inputs/source.md` 上传普通二进制体并返回 `input_id`、`sha256`、`size`。零字节文件合法。Job 的 `inputs` 引用包含 `input_id`、精确 sha256，可选 `path` 和 `role`。提交时再次验证归属、hash、保留状态以及合同的单文件/总量/数量额度。

Worker 的执行上下文包含受保护 `inputs/` 与 `base/` 子树。输入若已经以 `inputs/` 开头只保留一层；检查点原始文件名始终保留在 `base/<原artifact path>`。规范化后出现同名、大小写别名或文件/目录碰撞会拒绝，不覆盖文件。

全局硬上限为单文件 64 MiB、总成果 256 MiB、文件数 2048；具体合同可以进一步收紧 `max_input_bytes`、`max_input_total_bytes`、`max_input_files`、`max_artifact_bytes`、`max_artifact_file_bytes`、`max_artifact_files`。输入和检查点共享输入总量/数量额度，检查点单文件按成果文件额度重放。额度、task/output schema 与完整 binding 一起钉入 Job，不随路由切换改变。

文件只能是安全相对路径下的普通文件。禁止链接、硬链接、设备、FIFO、路径穿越、Windows 特殊名、大小写别名以及文件/目录冲突。真实只读 bind 挂载由可信宿主验证，chmod 不代替隔离。

## 停止与冻结成果

业务先读取 `GET /v1/jobs/{job_id}`。可发布候选要求业务核对终态、`stop_proven`、精确 attempt/fence/request/result digest 和冻结 manifest。已入队但未启动的取消/超时用 `not_started: true` 明确区分，不捏造停止回执。quarantine 和不确定停止不导出完成候选。

宿主观察 Agent 写入进程停止后收集文件，再完成只读源卸载，最后冻结并签署结果；卸载失败保持 quarantine，不提前清理或发布。API 不暴露可供业务签署 Worker 停止凭据的密钥。

`GET /v1/jobs/{job_id}/artifact-manifest` 返回：

```json
{
  "schema_version": "agent-artifacts.v1",
  "job_id": "服务生成的UUID",
  "attempt_id": "服务生成的UUID",
  "files": [
    {"artifact_id": "服务生成的UUID", "name": "project/slides/01.svg", "sha256": "64位摘要", "size": 123}
  ],
  "total_bytes": 123,
  "publication_authorized": false,
  "sha256": "manifest正文的规范JSON摘要"
}
```

`files` 按名称排序，`sha256` 对除自身字段外的紧凑、键排序、UTF-8 JSON 计算。`GET /v1/artifacts/{artifact_id}/content` 下载原始字节，业务按 manifest 逐文件核对 hash、大小与期望集合；业务独立的 PPT/科研领域校验依然必须执行。

## Portable checkpoint

`POST /v1/jobs/{job_id}/checkpoints` 使用 `Idempotency-Key`，正文为 `{"artifact_ids": null, "format": "workspace-tree.v1"}`；可显式选择源 Job 的已冻结成果集合。源 Job 必须已停止并处于 completed/needs_input，不能导入业务伪造的 Worker manifest。

返回 `checkpoint_id`、`sha256`、`format`、源 Job 和服务 manifest。manifest 包含源 profile/revision、完整源 `execution_binding`、contract digest、文件引用/hash/size、父检查点。新 Job 用 `checkpoint_ref: {checkpoint_id, sha256, format}` 引用。检查点保留文件及父链引用，底层数据仍归原调用方所有。

兼容策略只允许：

1. 相同 profile/revision 和 contract hash，并且能力包的 name/revision/hash 完全一致；这样可在重新验收后切换引擎、工具环境或模型执行同一合同。
2. `v1` PPT 明确的相邻阶段：`plan_initial → plan_options → prepare → generate → revise`，以及 `revise → revise`；能力包的三项身份同样必须一致。PPT 的执行前 hook 还需核对领域 checkpoint 格式、phase、确认内容和全部文件 hash。

其他跨领域、跨合同、跳阶段或包身份变化均拒绝。检查点只重放可移植业务成果，不包含 Pi/Hermes 原生 session、个人 HOME、认证信息或业务发布权。

## 幂等恢复与保留

丢失提交响应时，`GET /v1/jobs/by-idempotency/{key}` 查询原 Job；客户端不会自动重发可能已执行的提交。`GET/PUT /v1/jobs/{job_id}/recovery` 保存业务恢复文档，PUT 携带 `expected_revision` 做 CAS。恢复文档可引用同调用方的 checkpoint，引用也会被保留。

`POST /v1/jobs/{job_id}/retention/release` 可显式释放输入和/或成果的 Job 引用，限已确认终止的普通终态，quarantine 不可释放。随后 `DELETE /v1/inputs/{id}`、`DELETE /v1/artifacts/{id}`、`DELETE /v1/checkpoints/{id}` 仅在不存在其他引用时删除；删除会保留墓碑、hash 和原始元数据，清空 blob 字节。检查点仍引用成果、恢复文档仍引用检查点时，底层资源不能被提前删除。

`POST /v1/jobs/{job_id}/publication-ack` 携带幂等键、`manifest_sha256`、`delivery_ref` 和普通业务 decision，记录调用方对候选的接收/决定。它始终返回 `publication_authorized: false`，执行服务不会代替业务写发布表或调用发布接口。读取记录使用同路径 GET。

低层 `AgentClient` 封装了上述接口。业务连接器仍需把 Jobs/事件/停止身份/文件 manifest 映射到业务 outbox、确认、审核、版本和发布事务，不能只看 Worker 的 completed 标志。
