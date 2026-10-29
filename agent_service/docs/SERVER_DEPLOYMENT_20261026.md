# 通用 Agent 服务部署交接（2026-10-26 至 27）

用户要求休息前收尾，已停止新增模型测试。控制面已部署；真实引擎验收尚未完成，生产路由和执行池全部关闭，PPT、科研现有服务没有切换。

用户已于 2026-10-27 要求继续工作，最新修复和验证见 [恢复验证记录](RUNTIME_VALIDATION_20261027.md)。以下保留上次收尾时的镜像、证据与状态。

## 已部署的位置

- GitHub：`zmhhaha/panghu_agent`，独立分支 `codex/agent-execution-service`。
- 本次部署代码：`4452253`（包含 `e85d91d` 的全部运行时代码）；后续收尾提交仅补文档。
- 服务器：`root@192.168.137.101`。
- 独立检出：`/root/agent-service-deploy-20261026/panghu_agent`；原服务器 `scientific` 检出没有切换。
- kubeconfig：`/etc/kubernetes/super-admin.conf`。
- 控制命名空间：`agent-service`；任务命名空间：`agent-workers`。
- 内网 API：`http://agent-service.agent-service.svc.cluster.local:8080`。
- 公开目录：`/root/agent-service-deploy-20261026/catalog-4452253/`。

收尾时 `agent-service-api`、`agent-service-dispatcher`、`agent-model-proxy` 均为 `1/1 Ready`。目录包含 PPT 五阶段、科研生成/修订共 7 个合同和 14 个 Pi/Hermes 绑定；这些组合仍未开放执行。

## 最终固定镜像

三个镜像均由服务器构建、推送并核对摘要：

```text
arm-cluster-master:5000/agent-service-control@sha256:41ed49fa5c6b79c5fa3cf01fd19219c58b2e96c94e69d681a354235243123af3
arm-cluster-master:5000/agent-service-pi@sha256:2096ff87e130775bdab66afe171e21e91d9f4b1d509d983812e845b37be79c4e
arm-cluster-master:5000/agent-service-hermes@sha256:a5b3d114647148a2cd3a1211bf5eb07468358b4303bd62046adfe4b87123a74b
```

构建始终以审定的原始 PPT/Pi donor 为来源。两个 Worker 共用先于业务源码的中立依赖阶段；后续代码变更复用缓存，避免递归以候选镜像为父镜像而触发 Docker 层数上限。

## 凭据与公共配置

`secret/agent-service/runtime` 保存专用数据库、PPT/科研、Worker 和签名身份；`secret/llm-service/callers.LLM_TOKEN_AGENT_EXECUTION` 是专用模型调用方。没有给 Worker 复制供应商 API Key，也没有新建 OAuth/Cloudflare 入口。

数据库角色/库均独立，`bootstrap --apply` 已返回 `ready=true`、`runtime_login_verified=true`；没有其他业务表、序列或 schema CREATE 权限，未修改共享 ACL/HBA。现有 186 项权限经只读聚合确认全部来自审定的 pgcrypto/pg_trgm/citext PUBLIC、非 DEFINER、无直接角色授权扩展函数，独立记录为观测值；未知扩展、自定义、DEFINER 和直接对象授权继续阻断。

Agent、PPT、科研三个 ExternalSecret 均已 Ready。llm-service 原 Secret 同步新增调用方并滚动重启完成。权威清单仍在平台 `vault/inventory/`，独立仓库 `deploy/platform/vault/` 是可部署来源副本。

## 已通过的验证

1. **Linux / 真实 PostgreSQL：924 项，零失败、错误或跳过。** 通用服务 887、科研连接器 22、PPT 连接器 15；源码 `e85d91d`，模型调用 0，临时容器/socket 清理完成。报告：`/root/agent-service-linux-evidence-20261026-r3/report.json`；run ID：`agent-pg-20261026171538-36976d539654`。Dockerfile 随后的缓存重排不改变这批 Python 实现。
2. **API 本地烟测：** healthz 200、无认证 capabilities 401、真实数据库的缺失 Job 查询 404；PPT 调用方仅见 5 个阶段，科研仅见 2 个，所有 ready=false 与关闭配置一致。这不是业务到 API 的跨 namespace 网络验收。
3. **真实 Responses：** 专用调用方访问 `llm-trusted` 返回 HTTP 200 / completed，精确回显 `CANDIDATE_MODEL_OK`。
4. **真实节点输入与 checkpoint base 隔离：** 固定 AppArmor 自收紧后，两个允许路径的 ro/nosuid/nodev/noexec、降权、空 groups/capabilities、NNP、控制文件不可读、输入不可写、禁止无关 mount、detached writer 停止和非懒卸载均通过。详见 [节点验收](NODE_ISOLATION_VALIDATION_20261027.md)。

## 尚未通过的真实引擎任务

`candidate-pi-ppt-r2` 已执行最终 Pi 镜像，隔离和任务代理 Responses 往返通过；随后原生阶段返回 `state=failed, error_code=engine_error`。代理记录只有 1 次模型请求（连接性回显），原生任务没有成功产出。真实租约到期后同一 grant 得到 HTTP 401，未重发付费请求。

Worker Pod UID：`c49743cb-d411-4e79-8158-b710545c89a3`，实际镜像为上方 Pi digest；独立观察到容器 terminated、exit 2、restart 0，结束时间 `2026-10-26T17:31:31Z`。证据：`/root/agent-service-deploy-20261026/evidence/candidate-pi-ppt-r2/`。

初轮 `candidate-pi-ppt-r1` 在 mount 验证处失败，未进入模型请求；证据同目录下 `candidate-pi-ppt-r1/`。两轮候选都已先保存实际 Pod/镜像/终止证据，再精确清理 Pod、Service、NetworkPolicy、ConfigMap、临时 Secret。`42000000..42000005` 已永久消费。节点七轮预检的 `41000000..41000020` 也永久保留，不复用。

Hermes 真实领域执行、科研真实生成/修订、完整 PPT 五阶段/预览发布、业务端网络/新后端切换、完整生产组合及回滚验收尚未完成。没有生成虚假 acceptance，也不能将控制面 Ready、924 项离线/PG测试或单次回显解读为双引擎生产可用。

## 问题与解决方案

| 实际问题 | 处理 |
| --- | --- |
| 无 Docker Buildx | 复用现有 Docker 原生 build/push，核对平台和镜像摘要；未安装宿主插件。 |
| Hermes 与中立代码依赖冲突 | 中立依赖放 `/opt/agent-service/.venv`，原生 Hermes/PPT 保留 `/opt/hermes/.venv`。 |
| donor 源码 group 可写 | 镜像内将能力包规范化为 root 所有并去掉 group/other 写权限，再测量摘要。 |
| Vault token lookup 参数差异 | 改用捕获 JSON，不打印含 token 的响应。 |
| 无 venv/ensurepip | 部署依赖装入独立 `deploy-python`，通过原 deploy.sh 的 PYTHONPATH 使用，不改系统包。 |
| 跨库 sequence 权限检查 SQLSTATE 42809 | 用 CASE 保证按真实 relation 类型调用权限函数。 |
| 审计误把标准扩展 PUBLIC 函数当业务数据权限 | 只读按类别/语言/扩展/DEFINER/直接授权聚合，再实施有限审定规则。 |
| 同文件系统 bind 的 ismount=false | 改为真实 mountinfo 挂点/标志与 statvfs ST_RDONLY 核验。 |
| Docker Inspect 声称 AppArmor，实际 OCI/profile 未附着 | Worker 授权读取前仅允许从精确 unconfined 自收紧到固定审定 profile，并重新读取内核标签/NNP/seccomp；错误、complain、未生效一律拒绝。无新增能力或宿主运行时改动。 |
| 首轮 Linux 5 个 fixture setup error | 只读测试白名单补入唯一 revision fixture 文件，最终完整 924 项通过。 |
| 递归候选父镜像达到层数上限 | 改为固定原始 donor 和共享依赖构建阶段。 |

## 日常部署与下一步

日常仍使用 `agent_service/build.sh` 和 `agent_service/deploy.sh`。bootstrap 用于首次数据库/凭据准备，catalog 固定能力组合，节点/候选探针用于运维验收；不要求每次源码更新重做全部初始化。完整命令见 [部署文档](../deploy/README.md)。

本机管理依赖位于 `/root/agent-service-deploy-20261026/deploy-python`。保持当前关闭路由的配置重新部署：

```bash
cd /root/agent-service-deploy-20261026/panghu_agent
PYTHONPATH=/root/agent-service-deploy-20261026/deploy-python \
KUBECONFIG=/etc/kubernetes/super-admin.conf \
bash agent_service/deploy.sh \
  --config /root/agent-service-deploy-20261026/catalog-4452253/config.production.json \
  --host-config-dir /root/agent-service-deploy-20261026/catalog-4452253/worker-configs
```

下次先在不重复付费的前提下定位 Pi `engine_error`（固定同 CLI/模型配置/工具启动路径，补有界且不泄漏密钥的诊断），再使用新名字和新 UID 做真实候选。之后验证 Hermes 和各阶段，登记真实独立验收后逐业务开放。节点重启后须重新装载审定 AppArmor profile；尚无自动开机维护，不能靠旧标签视为就绪。
