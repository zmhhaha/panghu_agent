# 用一次性容器装载节点输入策略

此镜像提供 `apparmor_parser` 和发行版 include 文件，`apt-get` 只在镜像构建内部执行。节点继续使用已有 Docker，不安装宿主软件。工具容器仅用于管理员装载内核策略，完成即退出；它与 Worker 镜像、任务 API 和模型凭据无关。

前提：目标节点已启用 AppArmor，且宿主 `/sys/kernel/security/apparmor` 存在。当前操作是节点内核策略变更，实际执行前需审定规则文件和构建出的镜像摘要。本文尚未作为真实节点验收结果。

## 构建并固定工具镜像

在服务器仓库根目录执行。示例为 ARM64；平台须与现有 Docker daemon 一致。构建只从标准输入传入 Dockerfile，没有仓库构建上下文，也不要求 buildx。

```bash
export NODE_TOOLS_IMAGE_TAG='arm-cluster-master:5000/agent-service-node-tools:review-20261026'
DOCKER_BUILDKIT=0 docker build --platform linux/arm64 \
  --tag "$NODE_TOOLS_IMAGE_TAG" - < agent_service/Dockerfile.node-tools
docker push "$NODE_TOOLS_IMAGE_TAG"
docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$NODE_TOOLS_IMAGE_TAG"
docker manifest inspect --insecure --verbose "$NODE_TOOLS_IMAGE_TAG"
```

核对远端 `Descriptor.digest` 与本次成功推送后、本仓库的 `RepoDigests` 一致。将完整引用设置为 `NODE_TOOLS_IMAGE`，后续只运行这个 digest；不要使用未推送的本地标签。`--insecure` 用于平台已有的私人 HTTP Registry，Docker push 仍遵循 daemon 既有配置。构建时 Debian 软件源验证包签名，最终镜像内容由推送后的 digest 固定。

```bash
export NODE_TOOLS_IMAGE='arm-cluster-master:5000/agent-service-node-tools@sha256:替换本次核对过的64位摘要'
```

## 每个执行节点分别装载

在目标节点已有的 root 管理终端中执行，提供同一份审定规则文件的绝对路径。只挂载这份规则和 AppArmor 对应的 securityfs 子目录；不挂载整个 `/sys`、宿主根目录、Docker socket、Kubernetes 凭据或任何业务秘密。

```bash
export AGENT_INPUT_PROFILE="$PWD/agent_service/deploy/apparmor/agent-capsule-inputs"
test -f "$AGENT_INPUT_PROFILE"
test -d /sys/kernel/security/apparmor
sha256sum "$AGENT_INPUT_PROFILE"

# 先仅解析规则，不写入内核。
docker run --rm --network none --read-only --user 0:0 \
  --cap-drop ALL --cap-add MAC_ADMIN \
  --security-opt no-new-privileges:true --security-opt apparmor=unconfined \
  --pids-limit 32 --memory 128m --cpus 1 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
  --mount "type=bind,src=$AGENT_INPUT_PROFILE,dst=/profiles/agent-capsule-inputs,readonly" \
  --mount type=bind,src=/sys/kernel/security/apparmor,dst=/sys/kernel/security/apparmor,readonly \
  "$NODE_TOOLS_IMAGE" --skip-kernel-load --skip-cache /profiles/agent-capsule-inputs

# 解析通过后装载/替换此策略；只有这个命令需要可写 AppArmor 子树。
docker run --rm --network none --read-only --user 0:0 \
  --cap-drop ALL --cap-add MAC_ADMIN \
  --security-opt no-new-privileges:true --security-opt apparmor=unconfined \
  --pids-limit 32 --memory 128m --cpus 1 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
  --mount "type=bind,src=$AGENT_INPUT_PROFILE,dst=/profiles/agent-capsule-inputs,readonly" \
  --mount type=bind,src=/sys/kernel/security/apparmor,dst=/sys/kernel/security/apparmor \
  "$NODE_TOOLS_IMAGE" --replace --skip-cache /profiles/agent-capsule-inputs

grep -Fx 'agent-capsule-inputs (enforce)' /sys/kernel/security/apparmor/profiles
```

工具只添加 `MAC_ADMIN`，不使用 `--privileged`、`SYS_ADMIN`、宿主 PID/网络或 `seccomp=unconfined`。默认 seccomp 保持开启。仅此管理工具使用 `apparmor=unconfined`，使它能通过指定的 securityfs 接口装载策略；业务 Worker 继续使用 `Localhost: agent-capsule-inputs` 和 `RuntimeDefault` seccomp。

若内核、用户命名空间或运行时拒绝装载，应停止并检查原因，不扩大挂载或能力。没有既有 Docker 的节点先不作为工具执行目标，不为此安装新宿主软件。策略成功装载只证明内核接收规则；仍须用真实 Worker 验证两个允许的只读 bind/remount、拒绝任意 mount 和输入写入，以及停止后卸载。验收失败不得开放 pool。

此方式不在 `/etc/apparmor.d` 写文件，也不安装开机服务。节点重启后须重新装载和核对，再恢复执行资格；一次手工加载不代表重启后仍然就绪。

## 可选的 pool 节点选择方案

**当前 `KubernetesPool` 尚无 `node_selector` 配置字段，以下是待实现方案，不能直接加入当前配置。** 当前开启输入池前，应确保所有可能调度到的节点均已加载并验收策略；只给节点添加标签不会限制现有 Worker 的调度。

可增加操作员配置 `kubernetes_workers[].node_selector`，写入 Worker 的 `spec.nodeSelector`。节点仅在本次启动后实际加载并验收规则后，才标记 `agent-service.panghuer.top/input-profile=agent-capsule-inputs-<规则SHA256前20位>`；完整摘要放在节点 annotation，避免超过 label 的63字符限制。池同时选择该标签及镜像的 OS/架构。例如未来配置片段：

```json
{
  "node_selector": {
    "agent-service.panghuer.top/input-profile": "agent-capsule-inputs-<审定规则摘要前20位>",
    "kubernetes.io/os": "linux",
    "kubernetes.io/arch": "arm64"
  }
}
```

实现时 selector 必须来自服务端公开配置，并进入持久 Pod 意图及资源一致性检查；业务请求不得选择节点。改动只影响新任务，不重新解释已分配 Pod。节点标签需在重启、规则更换或验收失败时撤销，重新装载验收后再恢复；没有匹配节点时保持任务未就绪，不降级 AppArmor。节点开机加载和就绪标签的自动维护可作为独立平台工作实现，不能把永久标签当作内核策略当前存在的证明。
