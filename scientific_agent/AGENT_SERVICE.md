# 科研业务接入通用 Agent 服务

本页记录旧一轮式 `research.generate.v1` / `research.revise.v1` 接口的接入与维护路径。
项目式工作台的六阶段流程、双确认门和 API/UI 构建部署请使用
[WORKBENCH.md](WORKBENCH.md)。新流程使用 plan/retrieve/outline/draft 四个独立合同，
再复用固定证据 revise；下面“两条路由/两项权限”的说明仅描述旧接口，不能照抄来覆盖
工作台已审定的五项权限或绑定。新组合的候选、正式 HTTP 与浏览器验收分别记录在
[工作流验证记录](../agent_service/docs/SCIENTIFIC_WORKFLOW_CANDIDATES_20261029.md)，
候选通过不等于业务发布和用户流程全部通过。

科研公开 API、用户归属、报告列表、引用追溯、版本、章节修订、比较和采用接口保持不变。
业务后端提交 `research.generate.v1` 或 `research.revise.v1`，由通用服务的已验收执行绑定
选择 Pi/Hermes。业务配置不包含模型供应商密钥、原生命令、镜像或 Agent 会话。

2026-10-29 最初接入时，科研生产 API 的一轮生成和固定证据修订均使用用户的 pi-cage。
该次接入只开放两个科研 Pi 路由，全局执行并发为 1；PPT 与 Hermes 未包含在该次切换中。精确 Worker 镜像、
正式 Jobs HTTP 与生产科研 HTTP 的生成、修订、下载、发布和接收回执均已验收。
实际镜像、任务与证据见 [科研生产接入记录](../agent_service/docs/SCIENTIFIC_CUTOVER_20261029.md)。

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
代码缺省仍为 `pi`，本次生产部署通过审定补丁显式选择 `agent-service`。后续更新沿用部署目录
中的实际配置；不得用缺省值覆盖已部署后端，也不得在完成环境验收之前启用新的执行组合。

业务凭据复用 [平台 ExternalSecret 清单](../agent_service/deploy/platform/vault/agent-service-externalsecret.yaml)：
Vault `secret/data/agent-service/runtime` 的 `SCIENTIFIC_SERVICE_TOKEN` 同步为
`scientific-agent/scientific-agent-service` Secret 的 `token`，科研 API 只读挂载到上述文件。
这里不新建供应商 API Key、Vault 登录、OAuth/Casdoor 应用或 Cloudflare 入口。

## 服务器常规构建与更新

科研 API 继续使用已有 [Dockerfile.scientific-pi-api](../Dockerfile.scientific-pi-api)。
文件名沿用早期 Pi 接入命名，当前镜像也包含通用 Agent 连接器；它不运行 Pi/Hermes Worker。
在服务器拉取已提交源码后，从仓库根目录构建并推送到现有独立科研 API 仓库。
先用 `git status --short` 确认没有未记录的源码修改，再为本次提交使用固定 tag：

```bash
SCIENTIFIC_API_REVISION="$(git rev-parse --short=12 HEAD)"
SCIENTIFIC_API_TAG="arm-cluster-master:5000/scientific-api:agent-service-${SCIENTIFIC_API_REVISION}"

docker build -f Dockerfile.scientific-pi-api -t "$SCIENTIFIC_API_TAG" .
docker push "$SCIENTIFIC_API_TAG"
docker image inspect "$SCIENTIFIC_API_TAG" \
  --format '{{range .RepoDigests}}{{println .}}{{end}}'
```

推送成功后，取最后一条命令列出的 `arm-cluster-master:5000/scientific-api@sha256:...`
作为部署镜像。这里使用 Registry manifest digest，不使用本地 image ID、tag 或 `latest`。
把下面的摘要替换为本次实际值，并为每次部署选用新的记录目录：

```bash
SCIENTIFIC_API_IMAGE='arm-cluster-master:5000/scientific-api@sha256:替换为本次实际64位摘要'
SCIENTIFIC_DEPLOY_DIR="$PWD/rendered/scientific-agent-service/${SCIENTIFIC_API_REVISION}"
mkdir -p "$SCIENTIFIC_DEPLOY_DIR"
printf '%s\n' "$SCIENTIFIC_API_IMAGE" > "$SCIENTIFIC_DEPLOY_DIR/api-image.txt"
git rev-parse HEAD > "$SCIENTIFIC_DEPLOY_DIR/source-revision.txt"
```

同一提交再次部署时使用另一个新目录，保留原记录。接着按下节使用现有渲染器生成
`--execution-backend agent-service` 补丁，审查网络规则并应用；这些步骤也用于接入后的
常规 API 更新。无需新建部署脚本、重建旧专用 Worker 或修改其 PVC。

部署目录保存实际镜像摘要、源码提交、渲染补丁、审定网络规则及回退快照。
它们是可复查的部署输入，不把终端里临时设置的 `kubectl set env` 当作唯一配置来源。
通用服务仍通过自己的 [build.sh/deploy.sh](../agent_service/deploy/README.md) 更新，使用
已审定的公开配置与 Worker host 目录；不要用重新生成的全关闭初始目录覆盖已审定生产配置。

以下旧入口不用于通用 Agent 后端的常规科研更新：

| 旧入口 | 行为与适用范围 |
| --- | --- |
| `bash scripts/deploy-api.sh scientific_agent` | 构建并应用共享 `agent-api:latest`、旧 `api-agent` 源码挂载及 LLM/RAG 配置，会覆盖独立科研 API 的部署设置。 |
| `bash scripts/deploy-scientific-pi.sh --apply-api ...` 或 `--apply` 配合 `SCIENTIFIC_PI_APPLY_API=1` | 固定渲染专用 `pi` 后端，并删除通用 Agent token 挂载与配置；仅用于旧专用 Worker 路径或明确回退。 |

已有旧 Worker、PVC 和历史连接凭据继续用于历史任务恢复及回退，不随本次 API 更新删除。
保留历史资源不表示新任务仍提交给旧 Worker；新任务后端由本次 API 补丁明确指定。

## 旧一轮接口的 Pi 单任务试接

已接入通用服务的业务共享一个全局执行槽，同一时刻只运行一次 Agent 尝试；并发提交进入队列。
PPT 未切换到该通用服务时，其原有独立 Worker 不计入这里的执行槽。以下步骤针对旧一轮接口。
Pod 按一次生成或修订创建，所有写进程停止且证据持久化后按清理流程回收。用户查看报告、等待确认
或决定下一次修订时不占用运行 Pod；下次修订创建新的尝试并读取已保存的基线。

1. 先为 `research.generate.v1` 和 `research.revise.v1` 分别记录真实 Pi、模型、工具及隔离验收，
   审定对应镜像 digest 和 binding revision，仅启用这两个已验收的 Pi 绑定。保持全局并发为 1；
   Hermes pool/route 继续关闭，不能用 PPT 或普通文本探针的通过代替科研合同验收。
2. 按上节构建包含当前业务连接器及科研校验代码的独立 API 镜像并推送私仓，
   将实际 `repo@sha256:...` 赋给 `SCIENTIFIC_API_IMAGE`。等待既有 `scientific-agent-service` ExternalSecret Ready。
   暂停新科研提交并排空现有任务，保存当前 API Deployment 与网络策略用于恢复。
3. 在仓库根目录生成可审查的 API 补丁和独立出站规则。此模式只生成文件，无需旧 Worker 镜像，
   不创建新 Worker、PVC 或 Secret：

   ```bash
   python k8s/scientific-pi/render.py \
     --execution-backend agent-service \
     --api-image "$SCIENTIFIC_API_IMAGE" \
     --api-patch --output "$SCIENTIFIC_DEPLOY_DIR/api-patch.json"

   python k8s/scientific-pi/render.py \
     --execution-backend agent-service \
     --api-egress-policy --output "$SCIENTIFIC_DEPLOY_DIR/api-egress-review.json"
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
     --patch-file "$SCIENTIFIC_DEPLOY_DIR/api-patch.json" --dry-run=server
   kubectl -n scientific-agent patch deployment/api --type strategic \
     --patch-file "$SCIENTIFIC_DEPLOY_DIR/api-patch.json"
   kubectl -n scientific-agent rollout status deployment/api --timeout=180s
   ```

   从科研 API 的实际网络与凭据挂载环境调用带认证的 `GET /v1/capabilities`，确认只获准访问科研合同，
   且生成和修订都解析到已验收 Pi 绑定；不要打印 token、完整 Secret 或模型供应商凭据。
   然后一次只提交一个新研究任务，验证真实搜索、报告和引用，再对已发布基线执行一次固定证据修订，
   检查取消、重启恢复、产物校验及接收回执。全部业务检查通过后再恢复常规提交。

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

本页提供接入和常规更新步骤。实际部署与验收范围以验证记录及对应任务、产物、停止证明和业务
接收证据为准；科研已通过的组合不会自动使 PPT、Hermes 或其他技能组合获得验收。
