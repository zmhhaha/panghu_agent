# 当前 Agent 联合恢复验收（只在服务器执行）

`run_current_recovery_kubernetes.py` 创建一个完全独立的短期 namespace，使用真实
Agent API、PostgreSQL Store、Dispatcher、KubernetesWorkerAdapter、RemoteWorkerAdapter、
WorkerHost、持久 capsule journal、lease watchdog、Linux UID stopper 和签名/验签路径。
实际调度器是独立 OS 进程，实际 Worker 是独立 Kubernetes Pod；没有 mock HTTP、
FakeKubernetes、伪造停止回执或管理员修改业务状态来制造恢复。

原生执行负载明确使用固定的 synthetic Python 程序，**不启动 Pi/Hermes，不调用模型**。
它使用单独的 legacy unbound `workspace-task.v1` profile，与生产 binding 和 acceptance
无关；不会创建验收凭证或启用任何生产 route。验收专用 manifest 子类仅固定脚本入口、
只读脚本/公开配置挂载、owner 标签和较小资源额度；基类的资源身份检查、create/start CAS、
原 attempt 恢复、finalizer、真实容器终止观察、停止签名与清理逻辑均保留。

## 前置条件与一次性 UID

先提交、推送两个 Python 文件，再在 Linux 集群管理服务器通过 Git fetch/checkout 获取。
入口拒绝未提交/被修改的 runner 和所选 Agent 源文件，不上传应用源码压缩包，不安装依赖。
所用 immutable registry digest 必须已存在；只允许 `repository@sha256:...`，不接受本机
image ID、可变 tag 或运行时安装依赖。Control/Worker 镜像内所选应用源码的实际 SHA256
必须与该次 Git 源一致；不匹配即失败，不把旧镜像报告改绑到新提交。

要求管理员事先在平台永久 UID 总账中预留 **32 个 UID**，并核对总账、passwd/group、
实际进程和其他预留。失败也消费整段。回执为 root-owned、单硬链接、0600 普通 JSON：

```json
{
  "schema_version": 1,
  "purpose": "ppt-disposable-recovery-v1",
  "run_id": "45d1b044c96beab5f9c8",
  "node": "arm-cluster-master",
  "uid_start": 42000412,
  "uid_count": 32,
  "ledger_path": "/root/agent-service-deploy-20261026/uid-reservations.json",
  "ledger_entry_name": "ppt-recovery-45d1b044c96beab5f9c8",
  "ledger_sha256_after": "以实际总账摘要替换",
  "checks": {
    "ledger_no_overlap": true,
    "passwd_group_no_overlap": true,
    "proc_no_overlap": true,
    "other_reservations_reviewed": true
  }
}
```

上例 UID/run_id 只属于已独立预留的首轮；不能复制用于另一次运行。入口只读验证回执和
精确总账摘要、对应 `reusable:false` 条目及全账区间，然后在总账同目录以 O_EXCL/fsync
写永久 `.ppt-recovery-uid-<start>-32.consumed.json`。该标记不会自动删除；复制回执到其他
目录也不能自动复用这段 UID。失败重跑需要新目录、新回执、新 UID 段。

## 运行

默认固定使用已审定的 control/worker digest，并分别使用 `/usr/local/bin/python` 和
`/opt/agent-service/.venv/bin/python`。PostgreSQL 也必须显式传审阅后的 registry digest
及镜像内的实际 postgres UID。以下命令中的 PostgreSQL 值必须替换，不能照抄占位符：

```bash
PPT_RUN_K8S_RECOVERY=true python3 -B \
  ppt_service/tests/e2e/run_current_recovery_kubernetes.py \
  --source "$PWD" \
  --kubeconfig /etc/kubernetes/super-admin.conf \
  --reservation-receipt /root/ppt-p0p1-recovery-reservation-45d1b044c96beab5f9c8.json \
  --postgres-image '审阅的仓库:latest@sha256:实际64位摘要' \
  --postgres-uid 999 \
  --output /root/ppt-p0p1-joint-recovery-20261103-r1
```

输出目录须全新、在源码目录外，父目录须存在。`--pod-cidr` 可多次提供审阅后的私有集群
Pod CIDR；默认使用选定节点的 `spec.podCIDRs`。Calico 地址规划与该字段不同的集群必须
显式提供实际 CIDR；不要用全网段兜底。Python 路径和 control/worker digest 可显式参数化，
但实际 Pod imageID 与镜像内源码仍须吻合。

## 实际联合场景

1. 通过真实 API 生成、收集并下载一个 synthetic Agent artifact；保留完整 bytes/SHA256。
2. 等实际降权子进程及 detached writer 心跳出现后，SIGKILL 独立 Dispatcher，wait/reap
   要求 -9；启动新 Dispatcher，要求同 job/attempt/fence/request hash/Pod UID/原生 nonce，
   PUT count=1，旧槽保持到实际停止观察，最后成功收集。
3. 停止该次 Dispatcher后，由外层管理员对本次 Worker 精确 UID 发
   `DeleteOptions(gracePeriodSeconds=0)`。保留生产后端的停止 finalizer，独立记录所有容器
   的真实 `terminated`、containerID、finishedAt、exitCode、restartCount=0。重新启动
   Dispatcher，仅沿原 allocation 恢复，持久写入停止观察后释放槽；没有重新创建尝试。
   不依赖同 PID namespace 中的 `kill(1,9)`，不使用 `--force`，不把 Pod 404 当停止证明。
4. 实际 Worker watchdog 执行原始绝对 deadline，任务 timed_out，后端完成真实停止回收。
5. 对同一个运行中 Worker 删除本次 control→Worker TCP 许可。默认 deny 和独立 DB、DNS、
   Kubernetes API 许可始终存在；Worker 本地健康作为正对照，使用新 TCP 连接取得三次
   连续 timeout。随后 SIGKILL 本次 Dispatcher，冻结其最后持久 lease，不延长绝对 deadline；
   观察 Worker 自己的 watchdog 停止同 UID 的所有 writer、心跳不再变动。恢复原许可，要求
   相同 Pod UID/IP/原生 nonce；新 Dispatcher 回收原 quarantined attempt，才释放槽。
6. 正式 WorkerHost 对实际坏 output schema 的拒绝，失败任务不暴露 artifact。它是 output
   handoff/schema 子矩阵，**不是 PPT renderer 或业务 quality gate**。
7. 固定降权负载写满本次独立 memory emptyDir，要求实际 errno=28 ENOSPC；journal 与基线
   在其他存储中保持。真实停止/失败/回收后，旧 Agent artifact 仍可按原 bytes/hash 下载。
8. 再提交一次独立 synthetic 任务，确认队列可继续工作。所有任务要求恰好一条 durable
   attempt 和 allocation、不同 Pod UID 和永久 UID、持久 stop_observed_at 与 retired。

每个故障后均通过真实 API 再次下载原 artifact，比较完整字节及摘要。该基线是合成 Agent
产物，不是 PPT 业务 Version；它不替代 owner 项目的旧版保持、真实 Pi/模型故障或完整
PPT renderer/quality 拒绝。C8 可以与单独、明确绑定版本/来源的实际 validator/renderer
组件证据组合记录；不能仅凭这个 runner 宣称上述业务链已经全验收。

## 隔离和清理

新 namespace 使用有限 ResourceQuota；计入自动 `kube-root-ca.crt`。PostgreSQL 使用独立
memory emptyDir、随机密码和自己的数据库，无生产 PostgreSQL/PVC；只有可信 control
获得仅本 namespace 的 ServiceAccount/RBAC。Worker 不挂 SA token、kubeconfig 或 caller/
master signing key。Worker egress 全拒绝，没有模型/供应商/生产 DB 出站。仅 control
到实际 PostgreSQL Pod、DNS 和有限 Kubernetes Service/endpoint IP/port 的出站被允许。

Worker 使用只读根、RuntimeDefault seccomp、NNP、drop ALL 后仅 UID/停止所需能力；没有
SYS_ADMIN、hostPath、hostPID/network 或输入 bind。本次不声称生产 AppArmor/输入挂载矩阵。
降权子进程实测 UID/GID、空 groups/capabilities、NNP、控制/Worker key 不可读、无 SA token。

异常也只清理本次 owner 和精确 UID 的资源。未终止 Worker 保留 finalizer，直到完整
`terminated` 正证保存后再以 UID/resourceVersion CAS 删除该项 finalizer；不强制删除未知
资源或所有 finalizer。随后确认 Pod 消失，再以 namespace UID precondition 删除 namespace。
任何清理不确定、镜像/源码不符、旧基线改变或未完成场景均使结果失败。UID 消费标记和证据
目录永久保留。`status.json.passed=true` 必须同时有联合场景、镜像/来源和 cleanup 成功证据。

入口和本文均只提供执行方法；没有在本地运行任何 PPT 测试/构建/解析/渲染。真实结果只以
服务器生成的 `status.json`、`joint-recovery.json`、action/Pod/cleanup 文件为准。
