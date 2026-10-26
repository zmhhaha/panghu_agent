# 科研业务接入通用 Agent 服务

科研公开 API、用户归属、报告列表、引用追溯、版本、章节修订、比较和采用接口保持不变。
业务后端提交 `research.generate.v1` 或 `research.revise.v1`，由通用服务的已验收执行绑定
选择 Pi/Hermes。业务配置不包含模型供应商密钥、原生命令、镜像或 Agent 会话。

运营配置放在科研 API 的 ConfigMap，服务调用凭据从科研自己的 Vault/Secret 投射文件提供：

```ini
SCIENTIFIC_EXECUTION_BACKEND=agent-service
SCIENTIFIC_AGENT_SERVICE_URL=http://agent-service.agent-service.svc.cluster.local:8080
SCIENTIFIC_AGENT_SERVICE_TOKEN_FILE=/run/secrets/scientific-agent-service/token
SCIENTIFIC_AGENT_GENERATION_REVISION=v1
SCIENTIFIC_AGENT_REVISION_REVISION=v1
```

该 token 是科研业务调用 Agent 服务的身份凭据，授予上述两个 profile 的权限；它不是模型
API Key。模型凭据、Responses 协议、工具环境、能力包和引擎路由统一由通用服务管理。
默认仍为现有 `pi` 后端；代码变更不会自动切换已部署服务。不得在完成环境验收之前启用路由。

`scientific_agent_dispatch` 持久保存业务 task ID、原始提交、输入引用、幂等键、Agent Job ID、
结果摘要和发布回执。持久 admission claim 只允许首次拥有者发送 POST；网络响应丢失后
使用只读幂等键查询恢复原 Job，未找到时保留 admission_uncertain，不自动重 POST。
原始绝对截止时间随 Job 固定，延迟投递不会延长任务；始终未查到的提交在截止后超时。

Agent 的 `completed` 先映射为科研的 `publishing`。业务核对服务产物清单、每个文件的大小
和 SHA256，再独立校验报告、证据、claims 和 references。生成任务必须实际执行至少一次
受限学术搜索；真实返回零条结果允许生成明确披露证据不足的报告。模型写入的搜索账本和
论文元数据必须逐项匹配受保护的工具观测，标识符核验状态也只能来自对应观测。宿主在
停止所有 Agent 写进程后从服务端取回观测，以 root 所有的 0400 文件进入成果；业务端
再次校验该成果、停止证明及其 attempt/request 绑定。未完成的 admitted 工具调用不能
发布。观测只能证明受限桥接实际返回的内容，不证明文献或结论科学真实性。章节修订还重做固定证据、基线、
lineage 和选中范围外字节不变校验。报告与版本提交并读回后，才记录本地发布 ack。
随后向通用服务发送 `candidate_received` 回执；其失败会单独持久重放，不重跑模型。

当前科研生成使用本地 `local-procedures-v2`，支持受限学术元数据及摘要检索。K-Dense 五项
候选已审阅但未选用、未安装。固定证据修订只开放 read/write，不能新搜索、核验标识符、
获取全文或增补参考文献。结构和引用一致性校验不会证明科学结论正确。

数据库保留原 `scientific_pi_index` 和既有版本记录的历史格式；连接器将其转换为中立任务，
不会把其中的历史 engine/runtime 字段交给通用服务选路。若需要排空已有 Pi 任务，可保留
原 `SCIENTIFIC_PI_WORKER_URL/TOKEN`，仅供历史记录恢复；新任务使用独立幂等命名空间。

本轮按用户要求只完成代码与静态审查，尚未运行测试、模型验收或部署。
