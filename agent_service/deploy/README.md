# 通用 Agent 服务的正规部署

本轮已完成本地回归：596 passed、21 skipped，详见 [本地验证记录](../docs/LOCAL_VALIDATION_20261026.md)。本文中的镜像构建、Linux 节点隔离和真实引擎/模型/K8s 流程仍需服务器独立验收，不能将本地测试作为真实组合已通过验收的记录。现有 PPT、科研业务不会因为生成或部署这些文件自动切换。

服务固定使用 `agent-service` 控制命名空间、`agent-workers` 执行命名空间。业务调用地址为 `http://agent-service.agent-service.svc.cluster.local:8080`。控制 API、Dispatcher、模型代理分别运行；Pi/Hermes 由 Dispatcher 为每次尝试创建独立 Pod。没有个人 Agent HOME、会话或宿主 kubeconfig 挂载。

## 公共服务配置的位置

| 配置 | 权威位置 | 接入方式 |
| --- | --- | --- |
| 数据库和服务间认证 | Vault `secret/agent-service/runtime` | 规范清单 `armbianbegin/vault/inventory/agent-service-externalsecret.yaml`，由 `vault-backend` 同步到 `agent-service/agent-service-runtime` |
| 模型调用方凭据 | Vault `secret/llm-service/callers` 的 `LLM_TOKEN_AGENT_EXECUTION` | 同一份 ExternalSecret 直接引用，不复制供应商密钥 |
| 上游模型、Responses API 和稳定模型别名 | 原有 `armbianbegin/llm-service/` | Agent 模型代理访问 `http://llm-service.llm.svc.cluster.local/v1` |
| 网站 OAuth/Casdoor 和 Cloudflare | 原有业务站点的规范 OAuth/Cloudflare 配置 | 继续由 PPT/科研等业务站点提供用户入口；内部 Agent API 不新增域名、Tunnel 或 Casdoor 客户端 |
| 任务、能力包、工具环境、执行绑定、Worker 候选镜像 | 受控的公开目录 `config.production.json` 和独立 Worker ConfigMap | 不含密钥，镜像固定 digest，已开始的 Job 保留原始绑定 |

独立 Agent 仓库中的 `deploy/platform/vault/agent-service-externalsecret.yaml` 是规范清单的来源副本，便于独立检出部署；主仓库的 `vault/inventory/` 是平台实际应用的位置。`deploy.sh` 优先使用该规范路径，独立检出缺少平台目录时使用来源副本，也可通过 `--external-secret-file` 显式指定。业务自身的 ExternalSecret 应从同一个 Vault 记录引用专用调用令牌，不能复制一份新的权威值。

同一清单已包含业务侧的两个凭据投影：`ppt-service/ppt-agent-service` 的 `token` 引用 `PPT_SERVICE_TOKEN`，对应 PPT 已实现挂载 `/etc/ppt-agent-service/token`；`scientific-agent/scientific-agent-service` 的 `token` 引用 `SCIENTIFIC_SERVICE_TOKEN`，对应科研配置路径 `/run/secrets/scientific-agent-service/token`。部署脚本应用一次清单并等待三个 ExternalSecret Ready，不需要手动另建两份秘密。`ppt-service`、`scientific-agent` 命名空间由现有业务部署先行创建；通用服务不会接管其命名空间管理。

## 1. 准备数据库、Vault 和模型调用方

在现有 PostgreSQL 中准备独立数据库和专用角色 `agent_service`。专用角色需能够在此数据库中创建和迁移本服务自己的表，不授予其他业务数据库权限。应用进程不创建数据库或角色；首次部署可用下面的管理脚本完成初始化。

### 服务器上的初始化脚本

在检出的 Agent 仓库根目录执行。脚本只需服务器已有的 `python3`、`kubectl` 和管理 kubeconfig，复用 `vault/vault-0` 内已登录的 Vault CLI；不要求新建凭据 JSON 文件。若 Vault 登录失效，先按平台仓库中的 `armbianbegin/vault/scripts/login.sh` 恢复登录，再继续，不向 Agent 服务增加 Vault 管理凭据。

```bash
# 默认只读：检查 Vault 字段、数据库角色/权限和 ESO 策略覆盖情况。
python3 agent_service/deploy/bootstrap.py \
  --kubeconfig /etc/kubernetes/super-admin.conf

# 显式初始化缺失项；已有凭据保持原值。
python3 agent_service/deploy/bootstrap.py \
  --kubeconfig /etc/kubernetes/super-admin.conf \
  --apply
```

初始化固定作用于 `data/postgres-0` 中的 `agent_service` 数据库和角色；数据库管理员使用容器现有的 `POSTGRES_USER`（包括通过 `envFrom` 注入的值），未配置时才使用 `postgres`。脚本会为业务、Worker、结果签名及模型代理分别生成独立随机凭据，通过带版本号的 CAS 合并写入 `secret/agent-service/runtime` 和 `secret/llm-service/callers`，保留其他字段及已配置值，不输出凭据、数据库 URL 或原始 SQL 错误。重试不会轮换已有密码或令牌。运行中断后可再次执行；如专用角色已存在而 Vault 中缺失其数据库 URL，脚本会停止，要求先恢复原凭据，避免猜测或重置密码。

新角色使用 `NOINHERIT`、无管理权限和独立数据库所有权；脚本拒绝接管具有其他角色成员关系、其他数据库所有权/显式授权或其他业务对象权限的已有角色，只收紧本服务数据库及其 `public` schema 的 PUBLIC 权限。共享 PostgreSQL 的 PUBLIC 可能仍授予其他数据库 CONNECT；报告中的 `other_database_connect_count` 会显示这类继承连接数，不代表具有其他业务表的读写权限。若其他业务通过 PUBLIC 暴露了 schema 创建、表、序列或函数执行权限，脚本也会停止交由平台检查。绝对禁止向其他数据库建立连接涉及共享 HBA/ACL 策略，不由此脚本修改。

ESO 仅做只读检查：读取现有 `eso-role` 及其策略，保守判断两条 Vault 数据路径是否可读，不修改共享策略或创建登录 Token。`--apply` 返回 0 表示凭据、数据库登录与可证明的策略覆盖已完成；返回 2 且状态为 `credentials_database_ready_eso_review_needed` 表示凭据和数据库已就绪，但仍需在规范平台配置中确认 ESO 权限，并在部署时实际等待三个 ExternalSecret Ready。返回 1 给出固定错误类别；不要为诊断将 Vault 记录或原始数据库错误粘贴到日志。默认只读检查返回 0 仅表示检查命令完成，需要同时查看 `ready`；该模式不会验证运行时密码登录。

初始化脚本完成后，仍按下面的流程构建镜像、准备目录，再执行 `deploy.sh --init-db` 创建应用表。新增模型调用令牌也需等待原有 llm-service ExternalSecret 同步，并按其既有流程重启读取环境变量的进程。脚本不发布工作负载、不启用 route/pool，也不生成引擎验收结论。

### 手动准备的替代方式

已手动准备数据库时，也可按以下字段格式在有权限的管理终端写入。此处 runtime 的 `put` 仅用于首次创建完整记录；已有记录用 `patch` 更新指定字段，避免覆盖其他值。密钥不要写入仓库。`receipt_keys` 和模型签名密钥都是独立随机密钥的 **base64** 文本（至少 32 个随机字节），不是十六进制。业务/Worker 令牌各自独立且至少 32 个可打印字符。

```bash
vault kv put secret/agent-service/runtime \
  DATABASE_URL='postgresql+psycopg://agent_service:替换密码@postgres.data.svc.cluster.local:5432/agent_service' \
  PPT_SERVICE_TOKEN='替换独立PPT调用令牌' \
  SCIENTIFIC_SERVICE_TOKEN='替换独立科研调用令牌' \
  PI_WORKER_TOKEN='替换独立Pi池令牌' \
  HERMES_WORKER_TOKEN='替换独立Hermes池令牌' \
  PI_RECEIPT_KEY_BASE64='替换独立base64密钥' \
  HERMES_RECEIPT_KEY_BASE64='替换独立base64密钥' \
  MODEL_SIGNING_KEY_BASE64='替换独立base64签名密钥'

# 对已有调用方记录使用 patch，保留其他服务的令牌。
vault kv patch secret/llm-service/callers \
  LLM_TOKEN_AGENT_EXECUTION='替换独立模型调用令牌'
```

首次创建 `secret/llm-service/callers` 才使用 `put`；已有记录不能用单字段 `put` 覆盖。`llm-service` 原有 ExternalSecret 会导入所有调用方字段；若该服务通过环境变量读取 Secret，待同步后需要按其原有部署流程重启。Agent 服务自己的 Secret 中只引用这一调用方 token。

Vault 的既有 ESO 读取策略也需覆盖 `secret/data/agent-service/runtime`；ClusterSecretStore 仍使用规范的 `vault-backend`。不用在新服务里另建 Vault 登录、Cloudflare 或 OAuth 管理凭据。

控制进程将 `config.json` 和 `secrets.json` 通过固定 `subPath` 挂载为普通只读文件，符合配置读取器拒绝链接的要求。Vault/ESO 更新后，当前 Pod 不会热切换这些文件；完成同步后按受控部署流程 rollout 控制面，使新进程读取新凭据。正在执行的旧尝试仍保留其原始绑定和停止/恢复约束，不能以密钥轮转为由绕过它们。

## 2. 构建候选镜像并推入私人 Registry

在服务器仓库根目录执行。`build.sh` 沿用外层 `cluster_config.sh` 的 Registry 设置，也接受显式 `REGISTRY`、`IMAGE_TAG`、`PLATFORM`。控制面和两种引擎分别构建；Pi/Hermes 候选均使用已经包含完整 PPT Master、字体、Chromium 及 Python 环境的审定 PPT 镜像。Pi 另外从审定 Pi 镜像复制固定 Node 和 `/opt/pi`。

```bash
export REGISTRY='arm-cluster-master:5000'
export PLATFORM='linux/arm64'
export PPT_DONOR_IMAGE='替换审定PPT镜像仓库@sha256:替换64位摘要'
export PI_DONOR_IMAGE='替换审定Pi镜像仓库@sha256:替换64位摘要'

bash agent_service/build.sh --target control
bash agent_service/build.sh --target pi
bash agent_service/build.sh --target hermes
```

成功推送才写入 `agent_service/rendered/control-image.txt`、`pi-image.txt`、`hermes-image.txt`。部署使用这些固定摘要，不能用 `latest` 或只构建未推送的标签代替。构建上下文使用明确的源码/中立校验器/科学检索工具清单，不包含 `.env`、个人 HOME、会话、数据库或部署凭据。Worker 镜像里的入口是 `/opt/agent-service/.venv/bin/python -m agent_service.worker_entry`；中立控制依赖安装在该独立虚拟环境。Hermes 网关和 PPT 脚本继续使用 donor 原有 `/opt/hermes/.venv`，继承自 PPT 镜像的网关入口会被替换。

## 3. 生成并审定公开目录

优先使用本服务的目录生成器准备 PPT 五阶段和科研合同，保持全部路由关闭。生成器的参数以 `python -m agent_service.catalog --help` 为准；不能拿示例中的零摘要当作候选运行。目录生成不产生验收报告、不调用模型、不启用业务路由。

公开配置中必须包含以下部署设置；其余合同/绑定由受审定的目录提供：

```json
{
  "api": {
    "host": "0.0.0.0",
    "port": 8080,
    "max_request_bytes": 4194304,
    "max_input_bytes": 67108864
  },
  "model_proxy": {
    "upstream_base_url": "http://llm-service.llm.svc.cluster.local/v1",
    "upstream_token_ref": "model-upstream",
    "signing_key_ref": "model-signing",
    "max_calls_per_attempt": 128,
    "max_output_tokens": 32768
  }
}
```

这只是配置片段，不能作为完整目录部署。生产配置采用 Kubernetes pools，不能同时填入旧静态 `workers`。Caller 的 `token_ref` 为 `ppt-service` / `scientific-service`；Pi/Hermes pool 的 token/receipt ref 分别为 `pi-worker-pool` / `hermes-worker-pool`。

每个 pool 的 `binding_id`、`binding_revision`、完整镜像 digest、host ConfigMap 名称和域内容额固定。`uid_start` 必须来自节点上专门预留且与其他服务不重叠的 UID 区间。`uid_count` 是**总 UID 数**：每次 fence 永久消耗相邻两个 UID，分别用于 Agent 和独立 PPT 预览；无需预览的任务也保留这对 UID，禁止后来复用。区间耗尽后更换受控区间，不能重置历史记录强行复用。

Worker 配置目录格式：

```text
worker-hosts/
  <pool.host_config_map>/
    config.json
    acceptance.json
    acceptance-evidence.json
```

`config.json` 包含固定 host 身份、显式 `binding_id`/`binding_revision` 和对应的完整 `execution_catalog`。停用候选可以暂时没有验收文件；启用池必须带真实的独立验收和证据。渲染器对照当前登记的该 binding 完整 pins、schema、额度及 timeout 验证 host，允许旧 host 继续使用自己的不可变历史目录来恢复已有 Job。修改当前全局 route 不会要求重写旧 host，也不会改变已提交 Job 的绑定。

配置使用内容摘要命名的不可变 ConfigMap；Worker host 名称也必须随实质配置/验收变化使用新名称。不能覆盖同名不可变 ConfigMap，或直接删除仍被未结束 Job 引用的旧镜像/旧配置。

## 4. 节点上的只读输入挂载

具有文件/检查点输入的候选池需设置 `read_only_mounts: true`，并在所有可调度执行节点装载本目录 `apparmor/agent-capsule-inputs` 配置。示例供后续真实 Linux 验收，当前未运行解析器或装载验证。

宿主缺少解析器时，使用[一次性节点管理工具镜像](../docs/NODE_APPARMOR_TOOLS.md)装载：解析器仅安装在容器内，不要求宿主安装额外软件。工具只添加 `MAC_ADMIN`，规则只读挂载，写入权限限于 AppArmor 的 securityfs 子目录；Worker 的 Localhost AppArmor 和默认 seccomp 保持启用。该文档同时说明逐节点验收、重启后重新加载，以及尚待实现的 pool 节点选择方案。

控制器只允许将 `/run/agent-control/input-sources/<attempt>/inputs` 和 `checkpoint-sources/<attempt>/base` bind 到对应 `/work/<attempt>/inputs`、`base`，重新挂载为 `ro,nosuid,nodev,noexec`，并验证实际只读挂载。仅 chmod 不算隔离，失败不降级为可写复制。Agent 子进程会清除能力、切换独立 UID，控制器确认进程停止、收集文件、完成非懒卸载后才冻结和签署结果。

静态阅读确认 `mounts.py` 的两个 source/target 与 AppArmor bind/umount 路径模式相符。第二次调用使用空 source 和 `MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC`，策略以目标路径上的 `remount options=(ro,bind,nosuid,nodev,noexec)` 表达相同意图。尚未确认目标节点的 `apparmor_parser` 是否接受该组合、内核如何匹配 bind-remount/尾随斜杠，以及具体容器运行时的 `RuntimeDefault` seccomp 是否放行所需 mount/umount 系统调用；这些必须在真实节点验收时核对。不能仅凭本次静态路径一致就认定挂载可用，也不能通过开放任意 mount 或移除 AppArmor/seccomp 来绕过失败。

`agent-workers` 是唯一采用 Pod Security `enforce: privileged` 准入级别的专用命名空间，因为 `baseline` 禁止只读 bind 操作所需的 `SYS_ADMIN`。这不等于 Worker 容器以 `privileged: true` 运行：实际 Pod 禁止宿主 PID/IPC/网络，禁用 service-account token，根文件系统只读，drop ALL 后仅添加控制器所需能力；仅输入池添加 `SYS_ADMIN` 和固定 Localhost AppArmor。执行命名空间的 create 权限只授予受信任 Dispatcher，不向业务 API、Agent 或站点开放。

## 5. 渲染、初始化与部署

构建脚本只使用标准库；涉及完整目录语义校验的部署渲染器需要本服务 Python 依赖。可以给管理终端建立专用环境，不改变系统 Python：

```bash
python3 -m venv .venv-agent-deploy
.venv-agent-deploy/bin/python -m pip install -r agent_service/requirements.txt
export AGENT_SERVICE_PYTHON_BIN="$PWD/.venv-agent-deploy/bin/python"
```

若服务器已有 `pip`，但缺少 `venv/ensurepip`，也可以把依赖装入独立管理目录，使用同一套 `deploy.sh`，无需更改系统 Python 包：

```bash
export AGENT_DEPLOY_LIBS='/root/agent-service-deploy/deploy-python'
python3 -m pip install --target "$AGENT_DEPLOY_LIBS" -r agent_service/requirements.txt
export PYTHONPATH="$AGENT_DEPLOY_LIBS${PYTHONPATH:+:$PYTHONPATH}"
```

此目录仅用于管理员部署，不挂载给业务或 Worker。不要将它加入 Git。

首次部署显式初始化数据库：

```bash
bash agent_service/deploy.sh \
  --config /受控目录/config.public.json \
  --host-config-dir /受控目录/worker-configs \
  --init-db
```

以后部署省略 `--init-db`；涉及新增持久表/字段的版本按变更说明再次显式执行。脚本先创建普通命名空间和 RBAC、应用 ExternalSecret 并等待 Ready、创建不可变目录和网络策略，再运行单独 init-db Job，最后发布控制面并等待 rollout。不会输出数据库 URL、Secret 内容、环境变量或私人 Pod 日志；失败会给阶段、Pod phase/reason，便于再做定点检查。

控制面 K8s API 出站策略会发现 `default/kubernetes` Service VIP 与实际 endpoint IP/port，兼容 CNI 在 DNAT 前后执行策略的情况；不用 `0.0.0.0/0` 开放 Kubernetes API。数据库默认只允许 `data` 命名空间中的 `app=postgres`；若数据库是独立固定地址，额外传入 `--database-cidr 地址/32 --database-port 5432`（IPv6 使用精确 `/128`）。K8s API endpoint 变化后需重新渲染部署策略。

需要先查看输出、完全不请求集群时，显式给定 API endpoint：

```bash
bash agent_service/deploy.sh \
  --config /受控目录/config.public.json \
  --host-config-dir /受控目录/worker-configs \
  --kubernetes-api-cidr 192.168.137.101/32 \
  --kubernetes-api-port 6443 \
  --render-only
```

这里的 API endpoint 是格式示例，应使用集群实际 VIP 和 endpoint。输出在 `agent_service/rendered/deployment/`，包含配置、工作负载、网络策略、Worker host 清单和独立 init-db Job。该目录和镜像摘要回执都不提交 Git。

## 6. 业务切换与后续验证

API 入站只开放 `ppt-service` + `app.kubernetes.io/name=ppt-hermes-controller`、`scientific-agent` + `app=research-api`，以及本控制面 Pod。模型代理带 `llm-client=true` 标签，符合既有网关入站策略；Worker 只能访问 DNS 和任务代理，不能直接访问模型供应商、数据库、Vault 或 Kubernetes API。学术检索通过受保护任务代理执行，Agent 不获得通用外网权限。

业务自身还需对应的出站策略和专用服务 token。先完成真实 Pi/Hermes × 具体任务/能力包/工具环境/模型的验收，再登记真实 acceptance 并开启对应 route/pool，最后切业务后端为统一 Agent connector。PPT 网页、两轮确认、业务 outbox、结果审核和独立发布保持由业务实现；科研同样保留证据校验和发布事务。

候选完成不等于业务发布：结果携带可验证停止身份、冻结文件 manifest、输入/输出 hash 和 portable checkpoint。检查点是业务文件树，禁止携带原生 Pi/Hermes 会话和密钥。服务中的 publication acknowledgement 只记录业务已接收/决定，不赋予执行服务发布权限。文件引用尚存在或 Job 被 quarantine 时不会通过删除接口移除底层材料。

接口、引用事务、检查点、双 UID 和部署渲染的本地回归已通过。服务器仍需真实只读挂载、AppArmor、代理租约、双引擎能力、取消与超时、固定镜像构建和 K8s 端到端验收；这些过程完成前，不宣称新实现已可用于生产接管。
