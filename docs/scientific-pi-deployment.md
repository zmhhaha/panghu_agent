# 独立科研 Pi worker：部署与验收说明

本实现不改变人用 Pi 网页工作台。源码分为：无框架共享契约、专用 worker/私有 SQLite、Pi 子进程适配、精选本地科研流程与工具、兼容科学 API/UI、可恢复报告发布。

**2026-10-22 当前状态：独立 worker、配套 API/UI 已进入生产部署与 canary 验收。** 最新镜像、凭据/PVC、实际网络与模型证据、部署结果和剩余问题统一记录在[本次上线记录](https://github.com/zmhhaha/armbianbegin/blob/codex/scientific-pi-worker/docs/scientific-research-pi/rollout-20261022.md)。本文保留部署方法和早期候选历史；旧 digest、146 项测试及“未部署”观察不能代表当前线上状态。实际 canary 是否通过，以该上线记录为准。

## 本地验证

在 panghu_agent 根目录执行：

```bash
python scripts/test-scientific-pi.py
```

测试需要现有 FastAPI/HTTPX；不需要模型密钥或外部网络。真实 Pi CLI 的可选协议测试还需要先在独立且已忽略的目录安装锁定依赖：

```bash
npm ci --ignore-scripts --no-audit --no-fund --prefix pi_worker/node
```

该可选测试只使用 loopback 模拟模型，不证明真实提供方可用或科学结论正确。Windows 无法覆盖 Linux 权限、网络策略和 FIFO 等平台测试；各包验收说明明确列出未验证项。

## 版本与来源

- 独立 npm 运行时为 `@mariozechner/pi-coding-agent@0.73.1`，由 [包清单](../pi_worker/node/package.json) 与 [锁文件](../pi_worker/node/package-lock.json) 固定。它是经过本次源码核对的独立运行时，不声称等同于人用工作台的 `@earendil-works` 包。
- npm 对 mariozechner 系列给出迁移到 earendil-works 的弃用提示；本版保留已核对的精确 pin，后续升级需重做协议验收，不能静默更新。
- [科研技能 manifest](../scientific_skills/manifest.json) 当前为本地原创流程，不包含未验证的 K-Dense 上游内容；候选技能及未完成审查如实记录。
- worker Python 依赖的用途列在 [requirements.txt](../pi_worker/requirements.txt)，实际镜像安装 [requirements.lock](../pi_worker/requirements.lock) 中的 **17 个精确版本**，不安装 CrewAI。锁文件来自已验证的 Linux ARM64/Python 3.13 安装集；修改依赖后需要重跑运行时验收。

## 构建与镜像隔离

worker 使用 [Dockerfile.scientific-pi](../Dockerfile.scientific-pi)。`NODE_IMAGE` 与 `PYTHON_IMAGE` 的默认值已固定为实际验证的多架构基础镜像 digest，也可由操作者传入已审查的替代 digest。Python 安装使用精确锁文件，Node 安装使用 `npm ci` 和现有 npm 锁文件。最终部署镜像强制使用 `repository@sha256:...`。操作系统软件仓库的元数据没有全部锁定，因此不声称构建字节完全可重复。

科学 API 使用 [Dockerfile.scientific-pi-api](../Dockerfile.scientific-pi-api)，SCIENTIFIC_API_BASE 必须是现有兼容 CrewAI API 镜像的固定 digest。此镜像只叠加新 API 和 Pi client/projection/contracts，保留原 CrewAI 依赖；不覆盖共享 agent-api:latest。

```bash
docker build -f Dockerfile.scientific-pi -t <dedicated-worker-tag> .
docker build -f Dockerfile.scientific-pi-api --build-arg SCIENTIFIC_API_BASE=<existing-api@sha256:digest> -t <dedicated-scientific-api-tag> .
```

以下是 **2026-10-22 较早一轮、生产上线前的历史候选** 构建记录。该轮只在 Linux ARM64 完成独立镜像构建、推送与容器验收，当时未修改生产 Deployment 或就绪标志；后续正式 worker 的最终 digest 与验收次数以本次上线记录为准。Registry manifest digest 不可与 Docker image config ID 混用：

| 工件 | Registry manifest digest | Image config ID |
| --- | --- | --- |
| worker | `sha256:dbdf5c05fdf669b16ed065fbbe19f094244542e2a26639c880115ac284e6a04b` | `sha256:46ec347ce3080a6f53f2fff29d2450a2a582eb7f6e14c0175f9c4299bb295869` |
| 验收专用派生镜像 | `sha256:944b3a3add1778b803fed88475d8e1ff171ef93873cd40c1859512e6dd27facf` | `sha256:76b30d39df6b6f4146fe892bec6bd27097092857c0508b47a320702b6e28ff6d` |
| 独立 scientific API | `sha256:89caf56457ee7f778ebe26337278ca3ede6bf6550d7f6339386473c18dcfbaa9` | 未在本记录另列 |

该历史候选的构建输入为：

- `NODE_IMAGE=node@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20`，实测 Node **24.21.0**。
- `PYTHON_IMAGE=python@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f`，Python **3.13**，实际为 **trixie** 基础镜像；该 digest 现在也是 Dockerfile 的默认值。
- `SCIENTIFIC_API_BASE=arm-cluster-master:5000/agent-api@sha256:62f908474e4df08436ea64f5a54f663a11cdadb5b304c1675bcf5c77de035674`，与既有运行 API 的不可变 digest 一致。
- 精选源码包 SHA256：`87d68c0f93ed2536e942e4ffb749af8ab97fde5df381b58107570b88b58e23a5`；这是构建输入归档摘要，不是 Git commit。

该历史候选的 Linux 套件 **146 项全部通过，零 skip/失败**，其中 runtime **27 项**含真实锁定 Pi CLI、严格网关形状 loopback 工具回合及进程组清理；另有 **7 项 Linux OS 隔离测试全部通过，7.204 秒**。独立 API 在只读文件系统、无网络、无额外 capabilities 下，用 synthetic CrewAI 包的只读整目录 bind mount 模拟既有 `/app/scientific_agent` 覆盖，实际导入验证通过；没有 patch 既有 API Deployment。这里只验证 **ARM64**（当前全部节点为 ARM64）；amd64 未构建/验收。真实网关模型探针随后在独立 Pod `scientific-pi-model-probe-20261022` 成功：Pi 0.73.1、Node 24.21.0，alias `llm-trusted`，SSE 自报 `gpt-6.1-sol`（不是独立上游身份验证），两次请求完成精确 bash/marker 工具回合；UID 300000 清理与真实 key 未进入结果检查通过。该探针 `fixture_mode=false`，但 `synthetic_harness_gates=true`、`production_acceptance=false`；使用既有 scientific caller、root loopback proxy 与 child dummy key，NetworkPolicy 仅放行 DNS/模型目标，未做真实研究、邮件或报告数据库写入。详见[模型探针记录](scientific-pi-live-model.md)。

## 渲染、凭据与控制面

[部署脚本](../scripts/deploy-scientific-pi.sh) 默认 `--dry-run`，只调用标准库 [渲染器](../k8s/scientific-pi/render.py)，不连接集群。

```bash
export SCIENTIFIC_PI_WORKER_IMAGE=<dedicated-worker@sha256:digest>
export SCIENTIFIC_PI_API_IMAGE=<dedicated-api@sha256:digest>
bash scripts/deploy-scientific-pi.sh --render <review-directory>
```

输出 worker.json、api-patch.json、api-egress-review.json。worker 为 scientific-agent namespace 中的单副本 Recreate Deployment、专用 Ceph RBD RWO PVC、ClusterIP 控制端口及 NetworkPolicy。既有人用 Pi 的 Service、OAuth、HOME/workspace、PVC 与 frpc 均不修改。

凭据通过外层仓库 Vault inventory 中的 scientific-pi-worker ExternalSecret 映射：

- SCIENTIFIC_PI_WORKER_TOKEN：仅 API/worker 控制进程使用；Pi 子进程不继承。
- SCIENTIFIC_PI_MODEL_API_KEY：独立模型 caller；若走 llm-service，必须先按该服务既有流程注册有权限的 caller。ExternalSecret 只同步密钥，不自动创建网关授权。

模型配置通过 SCIENTIFIC_PI_MODEL_PROVIDER/MODEL/MODEL_BASE_URL/MODEL_API 指定；默认渲染为 existing scientific 网关 `/v1`、`llm-trusted`、`openai-completions`，不恢复已退役的 pi-trusted 路由。该 API 的真实工具回合已由隔离探针验证，专用生产 worker caller 的 Vault 凭据与网关注册已在本次上线中完成，当前结果和证据见上线记录。为满足网关严格请求 schema，可信 factory 为 completions 固定 `supportsStore=false`、`supportsUsageInStreaming=false`、`supportsReasoningEffort=false`、`maxTokensField=max_tokens`，模型 `maxTokens=8192`；不放宽网关。经审查的直接端点可用 `SCIENTIFIC_PI_MODEL_COMPAT_PROFILE=none` 清除此兼容 profile；openai-responses 不附带 completions compat，仍限制 8192 tokens，并需单独验证端点/网络例外。

新 API 显式运行 `app.api.scientific_agent:app`，避免旧 ConfigMap 对 agent.py 的覆盖掩盖新代码。另将新 Pi 模块放在镜像固定目录 `/opt/scientific-pi-extension`，在 mounted `scientific_agent` package 路径末尾追加，保留原 CrewAI 整目录覆盖优先级；该布局已通过上述只读 Linux 模拟覆盖验证。SCIENTIFIC_DEFAULT_ENGINE 保持 crewai，先用显式 engine=pi canary。

## 科研 UI 的公网 HTTPS 地址

科研 UI 当前安装 Gradio 6.19.0。它通过请求头生成配置、主题 CSS 和队列请求的根地址；当入口最终传到应用的是 HTTP 协议信息时，HTTPS 网页会拿到 `http://scientific-agent.panghuer.top` 根地址，浏览器出现混合内容错误和 `Connection to server lost`。

部署时应用专用的 [ui-public-url-patch.yaml](../k8s/scientific-pi/ui-public-url-patch.yaml)：

```bash
kubectl -n scientific-agent patch deployment/ui --type strategic \
  --patch-file k8s/scientific-pi/ui-public-url-patch.yaml
kubectl -n scientific-agent rollout status deployment/ui --timeout=120s
```

该补丁持久化 `GRADIO_ROOT_PATH=https://scientific-agent.panghuer.top`。已安装源码 `blocks.py` 会在启动时读取该变量；`route_utils.get_root_url()` 遇到完整 HTTPS URL 会直接使用它。科研专用 OAuth 代理已开启 `--reverse-proxy=true`，本次修复不依赖通配 `FORWARDED_ALLOW_IPS`。实际检查中，未带 HTTPS 转发头的内部 `/config` 原来返回 HTTP 根地址；修复后由协调者在登录后的浏览器验证按 ID 查询成功，未出现新的混合内容错误。以后替换 UI 镜像或 ConfigMap 时，应保留该 Deployment 环境变量。

## Linux 权限例外与进程终止

原设计希望整个容器 nonroot/drop ALL；实现核实后，这与“控制进程按任务切换唯一 UID，并清理 detached 子孙进程”的方案不兼容。本地实现采用明确的受限控制进程例外：

- 控制进程 UID0，drop ALL 后仅保留 CHOWN、DAC_OVERRIDE、SETUID、SETGID、KILL；无集群 RBAC/token、hostPath、hostNetwork、容器 socket，根文件系统只读。
- SQLite 事务在启动前分配 >=200000、持久化且永不复用的任务 UID。子进程 UID/GID 唯一，清空 supplementary groups、effective/permitted/inheritable/ambient capabilities，设置 no_new_privs 和 RLIMIT_NPROC。
- 控制目录与结果目录 root0700；任务祖先只允许 traverse，各 attempt HOME/workspace0700 且使用独立 UID。launcher 先验证控制 sentinel 不可读，再 exec Pi。
- 先 TERM 进程组，再 KILL；针对脱离进程组的后代，用 pidfd 与 /proc UID 扫描终止并确认两轮无活动进程。父控制进程意外退出另有 launcher parent-death 保护。
- 无法证明旧进程停止时 worker 进入 degraded、保留 fence/独占锁，不启动替代任务。可选 cgroup 是更强边界，默认不依赖宿主 cgroup 挂载。

早期 Windows 验证不能覆盖上述 Linux 边界；2026-10-22 的实际 Linux ARM64 容器已通过 7 项 OS 隔离测试。完整套件曾暴露 same-UID fixture 在 root 已退出时只等待 root、未确认子进程收到信号的 race；现仅对 Linux fixture 原进程组增加有界扫描/KILL 与连续两次无活动成员确认，生产 UID/pidfd 清理不变，最终 Linux 套件无失败。Windows 新 strict CLI 测试仍在五秒 version preflight 超时，直接 CLI 十二秒也未返回；该未解问题未被跳过掩盖、未延长生产 timeout，也不抹去此前 Windows fixture/CLI 通过记录。这些是较早候选阶段的观察；当前生产部署与就绪标志的证据以本次上线记录为准。

## 就绪、网络与验收门槛

SCIENTIFIC_PI_CAPABILITY_VERIFIED、SCIENTIFIC_PI_MODEL_VERIFIED、SCIENTIFIC_PI_ISOLATION_VERIFIED 默认都是 0。不能为让 Pod Ready 就盲目改成 1。只有记录实际 Pi 协议、真实模型工具回合、Linux 唯一 UID/权限/清理及网络证据后，操作者才可用渲染选项声明已验收；这些标志本身不是验证。

worker 入站仅 scientific API；出站仅 DNS、所选内部模型端口与明确的广泛公网 HTTPS。公网 HTTPS 不是学术域名白名单。默认排除 LAN/node/link-local/metadata/fake-IP 范围，不自动为 fake-IP 放宽策略；本集群依赖 fake-IP；worker 学术桥接已使用固定公共 IPv4 引导的受控 HTTPS DNS，并在真实 worker 镜像的同节点基线/受限 Pod 完成 TLS、学术检索和标识符验证。完整结果与保留的未知项见[网络验收记录](scientific-pi-network-acceptance.md)。透明 TCP CONNECTED 不算来源可用。

API 出站策略单独输出，不自动 apply：若原 API 没有 egress policy，单独新增只放行 worker 的 policy 会意外阻断其数据库、模型和邮件。部署者必须检查现有 policy union 后再应用增量。

`--apply` 是显式部署入口，先等 ExternalSecret Ready、做 server dry-run 再 apply worker；只有 SCIENTIFIC_PI_APPLY_API=1 才 patch 现有 API。本次生产部署的实际操作、资源与回滚快照见上线记录。

## 状态、保留与回滚

Pi 任务独立于旧 clear_stale_tasks。明确拒绝持久化为 worker tombstone，避免 API 拒绝索引保存失败后被重放。报告进入 publishing 后不可撤回；API insert-if-absent、正文 digest 读回成功，再以 attempt+result_digest ack。数据库暂时失败只重试发布，不重跑研究；通知失败不翻转报告成功。

成果默认 30 天，metadata/index/拒绝/UID 分配保留至少 180 天（worker 当前无限保留这些小记录）。publishing 成果不自动清理。过期附件410，已发布 Markdown 继续由共享报告库提供。

回滚先暂停 Pi 接单或将默认后端恢复 crewai，确认在途任务已完成/取消，保留 PVC 和报告，再恢复记录中的旧科学 API 镜像/命令及必要配置。不重置共享仓库，不删除人用 Pi 数据，不将 shared latest 当回滚版本。

具体测试证据分别见 [API 记录](scientific-pi-api.md)、[运行时记录](scientific-pi-runtime-verification.md)、[技能记录](scientific-pi-skills.md) 与 [集成验收](scientific-pi-acceptance.md)。
