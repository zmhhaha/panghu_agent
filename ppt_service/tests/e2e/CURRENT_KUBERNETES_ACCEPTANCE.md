# 当前 Kubernetes 合成隔离验收（仅服务器执行）

入口 `run_current_kubernetes_acceptance.py` 只使用 Python 标准库、服务器已有的 `kubectl` 和固定的 validator registry digest。先提交、推送，再在 Linux 集群操作服务器通过 Git 拉取该提交执行；不在 Windows 或开发机运行 PPT 测试，不传源码压缩包、不安装依赖、不构建镜像。

```bash
PPT_RUN_K8S_ISOLATION=true python3 ppt_service/tests/e2e/run_current_kubernetes_acceptance.py \
  --kubeconfig /root/.kube/config \
  --output /tmp/ppt-kubernetes-acceptance-20261103-01
```

按服务器实际配置指定 kubeconfig；若需选择已存在的 context，可追加 `--context <名称>`。输出目录必须全新、父目录已存在，且位于 Git 源码目录外。Kubeconfig 只留在宿主；不会进入 ConfigMap、Pod、证据或日志。入口要求自身字节与当前 Git 提交完全一致，并记录 commit、runner/probe SHA256；不能运行尚未提交的 runner。

固定使用：

```text
arm-cluster-master:5000/ppt-agent-validator:latest@sha256:4492a878db0d575f20542379a61986500a1c5263b5ecc5460026a87937549bc9
```

该 registry digest 必须已存在，镜像内 Python 路径为 `/opt/hermes/.venv/bin/python`。不接受 Docker 本机 image ID 替代 registry digest；实际 Pod 的 `imageID` 也须匹配该摘要，若集群返回不同架构 manifest 摘要，本次失败并保留证据，不能放宽校验后声称使用了同一镜像。应用镜像仍以 `latest` 构建、推送，验收只固定既有摘要，不创建版本 tag。

## 创建与范围

每次创建一个全新 `ppt-isolation-<随机值>` namespace，Pod Security 设为 restricted；其中只有一个不可变 synthetic ConfigMap、三个微型 Pod、三条 NetworkPolicy 与一条配额。总 request 为 30m CPU / 96MiB，limit 为 750m CPU / 288MiB，Pod 最长运行 600 秒。Quota 禁止 Secret、PVC 与 Service。

所有 Pod 使用 UID/GID 10000、drop ALL capabilities、no-new-privileges、只读根文件系统、RuntimeDefault seccomp，禁用 SA token 自动挂载与 Service 环境注入。只读 `/inputs` 来自本次 synthetic ConfigMap；每个 Pod 各自独立的 `/output` 与 `/tmp` 是内存 emptyDir。没有 hostPath、生产 PVC、数据库、真实资料、模型请求或业务 Agent 调用。

## 实际测量

- 读取真实 Pod spec 与 `/proc/self/status`、`mountinfo`：验证 UID/GID、五组 capability 全零、NNP=1、Seccomp=2；确认 root/input 实际为 ro、output/tmp 为可写 tmpfs。分别尝试 root/input 写入，要求拒绝且输入摘要不变。检查固定 SA token 路径不存在。
- client-a、client-b 各写自己的 output owner，再交叉时序读取原值；结合各 Pod 的独立 emptyDir spec 证明这两份合成输出没有互相覆盖。
- 默认 deny-all 在 Pod 启动前存在。明确添加只允许本次 client → target TCP 18080 的 egress/ingress；先取得 nonce 正向响应，再删除仅 egress 允许，要求连续三次 timeout 或明确 EPERM/EACCES/ETIMEDOUT，再恢复允许并验证相同 Pod UID/IP 的连接成功；ingress 独立重复同一序列。拒绝阶段还验证 target loopback 响应，避免把服务死亡误当网络隔离。ECONNREFUSED、reset 或 EOF 不算拒绝证据。
- 真实子进程写心跳，发送 SIGTERM，wait/reap，要求返回 -15、原 PID 消失、心跳停止。随后优雅删除三 Pod，观察原 UID 对应 Pod 全部消失，再删除 namespace。

网络结论仅覆盖记录的同 namespace TCP 18080 流量与本次策略。没有测量 Internet、DNS 或所有协议，没有证明生产 `agent-workers` 策略和本次完全等价。Pod 删除是 API 观测，不是独立节点运行时取证。真实 child SIGTERM 也不代表完整 Agent 任务恢复、OOM、节点崩溃或 SA admission 全矩阵验收。

## 清理与证据

只允许删除本次 namespace 及其中已校验 owner 的 Pod/NetworkPolicy。删除 namespace 前重新验证随机名称格式、唯一 owner label 与首次捕获的 namespace UID；不操作其他 namespace、不 prune、不强制、不改 finalizer。清理在异常和中断时仍尝试；若 namespace 60 秒后仍存在、owner/UID 无法确认或 API 不可达，本次失败，不宣称清理完成，`cleanup.json` 留下原因供管理员核对。

`manifest-*.json` 保留实际提交的合成资源；`pod-observations.json` 保留被检查的 spec 与镜像身份，`probe-steps.json` 记录实际内核、挂载、写入、网络和停止探针。分阶段 JSON、`namespace-identity.json`、`source.json`、`cleanup.json` 和最终 `status.json` 保留在权限 0700 的新目录。只有全部测量成功、source hash 未变且 namespace 清理确认完成，`status.json.passed` 才为 true。入口自身未执行任何本地验收；实际结果以服务器生成的证据为准。
