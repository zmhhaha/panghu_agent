# 取消与提交恢复的执行工具

这些工具已经过离线测试，尚未执行真实模型任务或部署操作。只对新的 `video-workbench` 实例做故障演练；科研 Agent、全局网络和业务数据库均不修改。上线状态仍以实际证据为准。

## 预算和操作点

原四步制作使用 `verify_live --execute --pause-for-faults`。它会在以下位置保存 checkpoint 并等待绑定其 SHA-256 的继续文件；默认每处最多等待 900 秒，可用 `--checkpoint-timeout` 显式调整到最多 1800 秒：

1. `before-svg`：已经拆镜并固定 SVG 输入，尚未提交 SVG。此时部署临时转发器、配置工作台、记录重启前 Pod、给转发器设定唯一目标。
2. `after-svg`：SVG 已保存，尚未提交 HTML。在此核对恢复实验；失败时不继续消费后续制作任务。
3. `before-revision`：HTML 和首次采用／导出完成，尚未提交修订。在此执行临时项目的一次运行中取消；之后继续原项目修订，验证服务可继续接单。

恢复叠加在原 SVG 任务上，不增加生成任务。取消临时项目手工建一镜，只提交一次生成和一次业务取消，额外预算最多一次尝试。失败或竞态不自动再做一轮；模型内部可能有多个工具回合，上述数量不是模型代理请求数量。费用始终记录为未知，直到管理员补齐实际账目。

没有 `--pause-for-faults` 时，四步工具自动连续执行，不适合在其中临时插入这两项验收。继续文件只表示操作者让流程往下走，不能替代故障验收报告。checkpoint 超时后程序停止；不要用新一轮 `--execute` 来恢复，因为它会创建另一个项目。

## 临时转发器的部署条件

`fault_proxy` 使用固定的实际 Agent origin 和工作台专用凭据文件；仅放行视觉任务及所需输入、查询、取消和收取接口。它不是通用 HTTP 代理。所有 Jobs POST 先记持久化意图；相同 Agent 幂等键只向上游发送一次，后续重复直接拒绝。

把它作为**独立于工作台 Pod 的临时进程**部署在工作台隔离网络中，trace 目录必须持久化。临时 Service/NetworkPolicy 的准备是管理员 hook：仅允许这一个新工作台实例连接，且仍使用专用 caller 认证。不要给其他服务替换 Agent 地址。工具不生成或应用这些网络资源，也不从外网开放入口。

部署前需要这些明确配置：

- 临时 Pod 使用独立标签，例如 `app.kubernetes.io/name=video-fault-relay`，单副本、非 root、只读根文件系统；只有证据卷可写。证据卷独立于工作台数据库，且不随工作台 rollout 删除。不要把旧 trace 目录用于新进程。
- 直接只读投影工作台已有的专用 caller Secret。生成器默认 Secret 为 `video-agent-service`，键 `token`，路径 `/run/secrets/video-agent/token`，实际以本轮设置为准。Pod manifest 只写 `secretName`、键名、挂载路径和 `--token-file`，不写凭据值，不把文件读到环境变量、命令参数、ConfigMap、trace 或终端。工作台和转发器使用同一个**专用 video caller**；科研 caller 不经过这里。
- 给工作台当前的 egress 限制**额外允许**访问临时 relay 标签的 TCP8096；relay ingress 仅允许同一工作台 namespace、标签 `app.kubernetes.io/name=video-workbench` 的 Pod。现有工作台策略默认仅允许 DNS 和 Agent，单建 Service 不会让流量自动通。
- relay egress 仅允许 kube-system 的 `k8s-app=kube-dns` TCP/UDP53 和实际 Agent API 的 namespace、Pod 标签、端口；Agent API ingress 同时增加只接受这个 relay 标签的对应端口。生成器 Agent 默认标签为 `app.kubernetes.io/name=agent-execution-service,component=api`、TCP8080，但必须核对本轮实际配置。使用新增的窄规则，不替换科研现有策略。
- relay 不挂载 Kubernetes API 凭据，也不需要集群 RBAC。Pod 观察命令由管理员运行，只需目标 namespace 的 Pod get/list；工作台 rollout 权限留给管理员。该工具不能验证所挂 token 的实际 caller 身份，专用 Secret／scope 与入口网络限制是部署前必须确认的边界。

转发器需要当前分支代码及其 Python 依赖；生产工作台镜像的构建白名单不会自动带上这些管理工具。管理员需预先把工具安装到临时受控运行环境。以下命令中的目录必须是绝对路径，`VIDEO_AGENT_UPSTREAM` 是实际 Agent 地址，凭据不放进命令行：

```sh
python -m video_workbench.deploy.fault_proxy \
  --upstream "$VIDEO_AGENT_UPSTREAM" --token-file "$VIDEO_AGENT_TOKEN_FILE" \
  --output "$PROXY_DIR" --run-id "$VIDEO_RUN_ID" \
  --listen-host 0.0.0.0 --port 8096 --max-seconds 3600
```

默认只绑定 127.0.0.1；上面的 0.0.0.0 仅用于有上述网络限制的临时实例。目录已存在就拒绝启动，不能重开转发器来清除一次性状态。整个实验期间保持它运行；重启的是工作台。

## 在 SVG 阶段丢响应并重启

先启动带操作点的四步工具；保留原先必需的 URL、Host、Origin、owner 和凭据文件参数，增加 `--pause-for-faults`。到 `before-svg-checkpoint.json` 出现后，**只在新工作台 Deployment** 配置转发地址。操作者先记录原地址，后续使用它恢复配置：

```sh
kubectl --kubeconfig "$KUBE" -n video-workbench set env deployment/video-workbench \
  VIDEO_AGENT_URL="$VIDEO_FAULT_RELAY_ORIGIN"
kubectl --kubeconfig "$KUBE" -n video-workbench rollout status deployment/video-workbench --timeout=120s
python -m video_workbench.deploy.fault_evidence observe-pods \
  --kubectl "$KUBECTL" --kubeconfig "$KUBE" --namespace video-workbench \
  --selector app.kubernetes.io/name=video-workbench --output "$BEFORE_PODS"
python -m video_workbench.deploy.fault_evidence arm-relay \
  --proxy-dir "$PROXY_DIR" --checkpoint "$RUN_DIR/before-svg-checkpoint.json"
python -m video_workbench.deploy.fault_evidence continue-workflow \
  --checkpoint "$RUN_DIR/before-svg-checkpoint.json"
```

`PROXY_DIR` 是转发器本地的持久化目录；若工具和转发器位于不同 Pod/主机，管理员需把 checkpoint 和脱敏观测文件传入受控目录并在对应环境执行这些命令，不能假设客户端路径在服务端也存在。

转发器只对输入摘要匹配的 `video.scene.v1` POST 受理响应做一次丢弃。它先记录真实 202 的 Job ID，然后关闭下游连接，并阻断该 operation 的 lookup GET。等待 `trace.json` 同时出现 `response_dropped` 和 `lookup_blocked`；缺少任意一个都不能算丢响应演练。之后执行：

```sh
kubectl --kubeconfig "$KUBE" -n video-workbench rollout restart deployment/video-workbench
kubectl --kubeconfig "$KUBE" -n video-workbench rollout status deployment/video-workbench --timeout=120s
python -m video_workbench.deploy.fault_evidence observe-pods \
  --kubectl "$KUBECTL" --kubeconfig "$KUBE" --namespace video-workbench \
  --selector app.kubernetes.io/name=video-workbench --output "$AFTER_PODS"
python -m video_workbench.deploy.fault_evidence release-relay \
  --proxy-dir "$PROXY_DIR" --before "$BEFORE_PODS" --after "$AFTER_PODS"
```

放行要求同一 namespace、同一 PVC、同一镜像内容、新的就绪 Pod UID，且新 Pod 创建在响应丢失之后。文件只含必要元数据，不保存 Pod 环境、凭据、原始 spec 或日志。重启只使用同一 PVC；不能用重新建库来假装恢复。

到 `after-svg` 操作点，先核对脱敏 trace、唯一受理 Job、fence=1、停止证明、manifest 原始字节摘要、工作台唯一未采用 SVG 候选：

```sh
python -m video_workbench.deploy.fault_evidence check-recovery \
  --run-dir "$RUN_DIR" --proxy-dir "$PROXY_DIR" \
  --before "$BEFORE_PODS" --after "$AFTER_PODS" --output "$RECOVERY_REPORT"
```

检查通过后才通过对应 `after-svg-checkpoint.json` 继续。检查工具不会额外请求模型，也不会重复 POST。只做已知 Job 的重启、客户端关页或浏览器响应丢失，都不能替代这一项。

## 一次运行中取消

在 `before-revision` 操作点，对原项目保持静止。另开独立证据目录运行 `cancel_live`，使用同一套显式连接／身份参数，加 `--protect-project-id` 指定原项目。默认仍只读，只有 `--execute` 才创建标记临时项目：

```sh
python -m video_workbench.deploy.cancel_live \
  --workbench-url "$VIDEO_TRANSPORT_ORIGIN" --origin "$VIDEO_PUBLIC_ORIGIN" \
  --host "$VIDEO_HOST" --owner "$VIDEO_OWNER" \
  --agent-url "$VIDEO_AGENT_UPSTREAM" --agent-token-file "$VIDEO_AGENT_TOKEN_FILE" \
  --output "$CANCEL_DIR" --run-id "$CANCEL_RUN_ID" \
  --protect-project-id "$ORIGINAL_PROJECT_ID" --execute \
  --kubectl "$KUBECTL" --kubeconfig "$KUBE" --worker-namespace "$WORKER_NAMESPACE"
```

工具先观察实际 Agent `running`、同一 attempt/fence 和 `job.running` 事件，采集运行 Pod，再次核对仍在运行，然后只向工作台发送一次取消。发生完成竞态、未开始、隔离或停止证明缺失时立即停止并保留证据，不尝试凑第二次成功。最终核对远端 `cancelled`、同一停止证明、没有输出 manifest／产物、临时项目没有候选、原项目内容未变。

独立观察器只使用固定参数的 `kubectl get pods`，按 attempt 标签收窄。停止记录必须将 `k8s:namespace:uid` 对上真实 Pod UID，并看到所有容器实际终止；仅 Pod 不存在不算停止观测。若控制面已迅速清理 Pod，工具返回 `pending_pod_evidence`，需要管理员从既有控制面的预删除观测补证。工具不会给 Pod 加 finalizer、延迟清理或改生产控制器。

已经对照生产 `kubernetes_provider.result()`：它先确认全容器终止并持久化 `stop_observed_at`，再清理 Pod，最后返回覆盖 `k8s:<namespace>:<pod_uid>` 的外层停止证明。它不使用候选探针的 `pod:<uid>` 作为公开生产证明。因清理发生在公开终态之前，终态之后才 GET Pod 往往已经拿不到对象；真实演练需提前启动只读 Pod watch，或由管理员脱敏导出现有控制面保存的 allocation UID／request SHA／stop_observed_at。当前工具不自动导出私有控制面记录；这个预删除观测 hook 缺失时，保留待补证状态。

目前公共 API 不直接提供槽位释放时间；报告明确为 `not_directly_observed`。继续原项目修订成功只能证明后续能接单，不能据此编造精确释放时刻。运行超时仍单列未验证，不能由取消成功替代。

取消证据齐备后，通过 `before-revision-checkpoint.json` 继续原四步流程。完成后管理员把**新工作台**的 `VIDEO_AGENT_URL` 恢复为记录的原 origin，核对 Ready 和只读 capabilities，再移除仅供这次实验的转发 Service／进程。证据与测试项目保留，工具不自动删除远端资源。
