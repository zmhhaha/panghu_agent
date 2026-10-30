# 独立候选的真实执行探针

`deploy/acceptance_probe.py` 是管理员的一次性候选工具，支持 PPT 五阶段、科研生成／修订及 plan、retrieve、outline、draft 阶段，以及 `video.storyboard.v1`、`video.scene.v1`，可选择对应配置中的 Pi 或 Hermes。它不接入生产队列，不创建 `acceptance.json`，不启用 route/pool，也不发布业务产物。

仅 `ppt.plan_initial.v1` 和 `research.revise.v1` 提供内置合成 fixture。视频候选必须显式提供 `--scenario` 和 `--source-dir`；使用 canonical UTF-8 `video-input.json`，上传角色为 `video_input`，task 仅含该文件的 `input_sha256`。拆镜候选可设 `max_scenes=2` 控制样本规模，单镜候选使用简单 SVG。其他阶段的真实执行也应使用该阶段要求的明确输入，不把仅渲染兼容性当作无输入即可运行。

探针协调器在 `agent-service` 中运行，持有独立 SQLite、临时签名 key 和专用 llm-service 调用方 token。Worker 在 `agent-workers` 中运行，只得到一次尝试的模型 grant、结果签名 key 和临时 bootstrap token。两个 Pod 使用同一个固定候选镜像和同一份脚本；脚本可以通过不可变 ConfigMap 挂载，无须为了执行探针再构建镜像。

管理员工厂位于探针脚本内部。生产 `accepted_host`、`build_pi_driver`、HostConfig 和注册目录的就绪检查保持原有要求。Pi 的前置条件来自该次真实版本测量、Responses 请求和 Linux 隔离观测；Hermes 只为该次精确尝试使用本地候选 driver。它们都不会生成或伪造生产验收结论。

## 准备

1. 候选镜像和 host `config.json` 已由目录生成器产生，所有 route 关闭、host `profile.enabled=false`、选中 binding 没有 acceptance。镜像使用完整 `repository@sha256`，摘要必须与该 host 的 pins 一致。
2. `agent-workers` 的现有 NetworkPolicy 已生效。执行节点已加载 `agent-capsule-inputs` AppArmor；容器保留 RuntimeDefault seccomp。探针不能通过 privileged、hostPID、hostNetwork 或去掉安全配置来跳过失败。
3. 为本次运行永久预留连续三个 UID，与生产池和此前探针都不重叠。前两个给 Agent/preview，第三个给会实际启动并停止进程的隔离预检。一次失败或不确定的运行也消耗这三个 UID；下一次使用新名称、新 Secret、新 UID 区间。
4. 在两个命名空间预置同名的临时 bootstrap Secret，键为 `token`，值相同且为独立随机可打印 token（至少 32 字符）。仅在 `agent-service` 预置上游 Secret，其键为 `token`，内容是已有 `LLM_TOKEN_AGENT_EXECUTION` 调用方令牌。不要把供应商 key、Vault 管理 token 或整个生产 runtime Secret 挂给 Worker。通过现有管理工具传递这些值，不粘贴到命令日志或文档。

两个 Secret 都以 root 0400 的普通只读 `subPath` 文件挂载。Hermes 固定 launcher 的 `/run/agent-secrets/worker.json` 保护性 sentinel 复用同一 bootstrap 文件；它不是生产 worker 凭据文件，Hermes 进程也不能读取它。原生 Agent 只有自己的任务 grant。

## 渲染和执行

在已安装 `agent_service/requirements.txt` 的管理 Python 环境中执行，替换受控目录、已审定节点和真实镜像摘要：

```bash
python -B -m agent_service.deploy.acceptance_probe render \
  --config /受控目录/worker-hosts/选定候选/config.json \
  --name candidate-pi-ppt-20261026 \
  --worker-image 'registry/agent-pi@sha256:替换实际64位摘要' \
  --node arm-cluster-master \
  --uid-start 2100000000 \
  --bootstrap-secret agent-candidate-bootstrap \
  --upstream-secret agent-candidate-upstream \
  --output agent_service/rendered/candidate-pi-ppt.json

kubectl --kubeconfig=/etc/kubernetes/super-admin.conf create \
  -f agent_service/rendered/candidate-pi-ppt.json
```

输出包含两个不可变 ConfigMap、独立 Service、候选 proxy 的独立 NetworkPolicy 和两个 `restartPolicy: Never` Pod。渲染器只读取公开 host 配置和自身源码，不读取或输出 Secret 值。`--upstream-key` 可选择已有临时上游 Secret 中的精确键名，默认 `token`。

proxy 使用 `app=agent-model-proxy` 和 `llm-client=true` 以满足原有出入站策略，但使用独立的 `app.kubernetes.io/name`、component 和 Service selector；它不会被生产 `agent-model-proxy` Service 选中。Worker 仍是 `app=agent-capsule`，出站只经过既有任务模型代理边界。候选 proxy 只允许 DNS 和 llm-service，不开放公网出站。

先预拉取固定镜像，再启动这两个 Pod。proxy 创建尝试时开始计 300 秒 lease，Worker 必须在它失效前注册和完成初始隔离/模型预检；进入执行后每 5 秒续租。Worker 原生任务期限 600 秒，Pod 最多 900 秒；proxy 最多 1200 秒。任何失效、进程重启或不确定模型请求都不会自动创建第二次付费尝试。

脚本使用固定路径运行：

```text
/opt/agent-service/.venv/bin/python /probe/acceptance_probe.py proxy ...
/opt/agent-service/.venv/bin/python /probe/acceptance_probe.py worker ...
```

Worker 的 Python 直接作为 PID 1；不能再包装 shell、tini 或其他 supervisor。

## 观测和证据

预检真实执行只读 bind/remount，测量 `ST_RDONLY`，从降权子进程检查 UID、空 groups、空 capabilities、no_new_privs、受保护控制文件不可读和输入不可写；另起 detached session 的写进程，并由真实 UID stopper 停止、确认心跳不再变化。尝试挂载无关 tmpfs 必须被现有策略拒绝。

随后用该次 scoped grant 请求真正的 `/v1/responses`，只有 completed 的输出文本精确等于 `CANDIDATE_MODEL_OK` 才进入原生引擎。PPT 使用明确标识的三页合成规划材料；科研仅修改无证据合成报告中的 Findings 一节，保留“证据不足”，不检索或声称真实科研结论。任务执行、工具调用、结果校验、进程停止、文件冻结和签名都使用现有真实路径。

结果写在 proxy 的 root 私有目录：

```text
/run/agent-control/candidate-probe/candidate-result.json
/run/agent-control/candidate-probe/candidate-evidence.json
/run/agent-control/candidate-probe/candidate.sqlite3
```

证据记录实际结果状态、模型请求状态和输入/输出摘要、package/版本观测、准确 binding 组合摘要和脚本 SHA256。`candidate-result.json` 保留原始候选产物；停止签名只说明本地 UID writer 观测，不替代外部 Pod 停止证明。结果提交后，proxy 用真实时钟等待 lease 失效并调用实际授权器确认撤销；Worker 再发同一 Responses 请求，必须得到 HTTP 401，不会再次转给上游模型。

管理员还须通过集群 API 独立核对实际 Worker Pod UID、spec/imageID、节点和安全上下文，保存容器 `terminated`、`restartCount=0`、结束时间和退出码。不要只根据 phase、Ready=false、Pod 消失或探针日志认定已停止。证据中的 `pod_termination` 明确保留为 `external_operator_observation_required`。

`result_state=completed` 也只覆盖该引擎、镜像、binding、模型策略和单阶段的此次运行；不意味着完整 PPT 流程、科研生成或其他引擎已验收。失败会保留候选状态和固定错误类别，不能写一份全 true 的 acceptance 使生产绕过它。

先导出证据和实际 Pod 状态，再按精确名称清理两个 Pod、独立 Service/NetworkPolicy/ConfigMap 和本次临时 Secret。proxy 的 emptyDir 随 Pod 删除而消失；不要先删 proxy 再尝试找回证据。它不会改变已有业务路由，业务切换仍需真实完整验收后的单独受控操作。
