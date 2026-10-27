# 科研业务接入通用 Agent 服务

科研公开 API、用户归属、报告列表、引用追溯、版本、章节修订、比较和采用接口保持不变。
业务后端提交 `research.generate.v1` 或 `research.revise.v1`，由通用服务的已验收执行绑定
选择 Pi/Hermes。业务配置不包含模型供应商密钥、原生命令、镜像或 Agent 会话。

当前优先完成 Pi 的生成、固定证据修订和业务试接，Hermes 不作为前置条件。通用服务控制面
已部署，并有离线及 Linux/PostgreSQL 回归记录；生产执行路由仍关闭，科研现有后端尚未切换。
真实 Pi 领域执行、业务试接及其证据见 [原生引擎验证记录](../agent_service/docs/RUNTIME_VALIDATION_20261027.md)，
控制面 Ready 或回归通过不能代替这两项验收。

## 配置与凭据归属

科研 API 的公开运行配置如下；部署补丁通过容器 env 设置这些值，不将秘密写入 ConfigMap：

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

业务凭据复用 [平台 ExternalSecret 清单](../agent_service/deploy/platform/vault/agent-service-externalsecret.yaml)：
Vault `secret/data/agent-service/runtime` 的 `SCIENTIFIC_SERVICE_TOKEN` 同步为
`scientific-agent/scientific-agent-service` Secret 的 `token`，科研 API 只读挂载到上述文件。
这里不新建供应商 API Key、Vault 登录、OAuth/Casdoor 应用或 Cloudflare 入口。

## Pi 优先的单任务试接

PPT 与科研共享通用服务的一个全局执行槽，同一时刻只运行一次 Agent 尝试；并发提交进入队列。
Pod 按一次生成或修订创建，所有写进程停止且证据持久化后按清理流程回收。用户查看报告、等待确认
或决定下一次修订时不占用运行 Pod；下次修订创建新的尝试并读取已保存的基线。

1. 先为 `research.generate.v1` 和 `research.revise.v1` 分别记录真实 Pi、模型、工具及隔离验收，
   审定对应镜像 digest 和 binding revision，仅启用这两个已验收的 Pi 绑定。保持全局并发为 1；
   Hermes pool/route 继续关闭，不能用 PPT 或普通文本探针的通过代替科研合同验收。
2. 构建包含当前 `scientific_agent`、`agent_service` 客户端及科研工具校验代码的 API 镜像并推送私仓，
   使用固定 `repo@sha256:...`。等待既有 `scientific-agent-service` ExternalSecret Ready。
   暂停新科研提交并排空现有任务，保存当前 API Deployment 与网络策略用于恢复。
3. 在仓库根目录生成可审查的 API 补丁和独立出站规则。此模式只生成文件，无需旧 Worker 镜像，
   不创建新 Worker、PVC 或 Secret：

   ```bash
   python k8s/scientific-pi/render.py \
     --execution-backend agent-service \
     --api-image '<private-registry>/scientific-api@sha256:<64位摘要>' \
     --api-patch --output rendered/scientific-agent-service/api-patch.json

   python k8s/scientific-pi/render.py \
     --execution-backend agent-service \
     --api-egress-policy --output rendered/scientific-agent-service/api-egress-review.json
   ```

   默认目标命名空间为 `agent-service`，Secret 为 `scientific-agent-service/token`，两类 profile revision
   均为 `v1`。如部署目录已调整，可显式设置 `--agent-namespace`、`--agent-caller-secret`、
   `--agent-caller-secret-key`、`--agent-generation-revision`、`--agent-revision-revision`。
   Secret 名称/键及 revision 应与已同步凭据和已验收合同一致，不能用修改业务 env 绕过绑定验收。
4. 审查并将出站规则合入科研 API 的既有网络策略覆盖范围：仅允许 `app=research-api` 访问
   `agent-service` 命名空间内 `app.kubernetes.io/name=agent-execution-service, component=api`
   的 TCP 8080。确认 Agent API 的对应入站规则也已生效。独立 Egress 策略会使原先未受出站隔离的
   API 开始被隔离，因此不能不审查就直接应用；保留原有 DNS、数据库、邮件等必要通路。
5. 在排空和网络检查后，对 API 补丁做服务端 dry-run，再显式应用并等待 rollout：

   ```bash
   kubectl -n scientific-agent patch deployment/api --type strategic \
     --patch-file rendered/scientific-agent-service/api-patch.json --dry-run=server
   kubectl -n scientific-agent patch deployment/api --type strategic \
     --patch-file rendered/scientific-agent-service/api-patch.json
   kubectl -n scientific-agent rollout status deployment/api --timeout=180s
   ```

   从科研 API 的实际网络与凭据挂载环境调用带认证的 `GET /v1/capabilities`，确认只获准访问科研合同，
   且生成和修订都解析到已验收 Pi 绑定；不要打印 token、完整 Secret 或模型供应商凭据。
   然后一次只提交一个新研究任务，验证真实搜索、报告和引用，再对已发布基线执行一次固定证据修订，
   检查取消、重启恢复、产物校验及接收回执。全部业务检查通过后再恢复常规提交。

`scripts/deploy-scientific-pi.sh` 仍为原有专用 Pi Worker 部署入口，默认不会切换到通用服务。
恢复旧后端时使用保存的 Deployment/网络策略，或以默认 `--execution-backend pi` 生成并审查补丁；
先停止新提交并处理通用服务中的活动任务，不能通过回退配置丢弃不确定任务或绕过执行槽。

## 结果接收与恢复

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

本页提供业务试接步骤，不构成科研已经迁移上线或真实 Pi 合同已经验收的记录。实际执行结果以验证记录
及对应任务、产物、停止证明和业务接收证据为准。
