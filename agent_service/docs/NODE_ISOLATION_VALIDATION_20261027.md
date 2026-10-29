# master 节点输入隔离实测记录

本记录仅覆盖节点挂载、进程隔离和停止预检；不证明 Pi/Hermes 的模型任务、完整 PPT/科研流程或生产 binding 已验收。没有生成 production acceptance，没有开启 route/pool，也没有迁移现有业务。

## 固定环境

- 节点 `arm-cluster-master`，`192.168.137.101`，ARM64，内核 `6.1.115-vendor-rk35xx`。
- 策略 `agent-capsule-inputs`，文件 SHA256 `117d8c29d1fb817aa12c0a70b4d52136da0d17a31a01677965ea1c48778b8503`。
- 节点工具镜像 `arm-cluster-master:5000/agent-service-node-tools@sha256:5dc9c8f88f3b34fd6bd3e7fedc44085819838d461f6e63717a128fdd7ed04a09`，parser 4.1.0。
- 工具容器仅 MAC_ADMIN、drop ALL、no_new_privs、network none、只读根；挂载仅规则文件和 AppArmor securityfs 子树。没有在宿主安装软件。
- 预检 Pod 无 hostPID/hostNetwork、无 service-account token，固定 master、只读根、RuntimeDefault、Localhost AppArmor；控制器只添加 CHOWN、DAC_OVERRIDE、KILL、SETUID、SETGID、SYS_ADMIN，没有 MAC_ADMIN。

## 遇到的问题和修复

首次 bind/remount 实际成功且 `statvfs` 已报告只读，但 Python `os.path.ismount` 返回 false：其设备/inode 判断不能识别同一文件系统上的 bind mount。`mounts.py` 改为读取内核 `/proc/self/mountinfo` 的精确挂点，并同时核对 `ro,nosuid,nodev,noexec` 和 `ST_RDONLY`；堆叠或缺失的挂点拒绝通过。对应回归为 19 passed、3 个 Windows symlink 跳过。

修复挂点核验后，预检发现无关 tmpfs mount 居然成功。内核 feature mask 包含 mount/umount/pivot_root，规则已 enforce，Docker Inspect 也记录正确 profile；但实际 PID 1 和 exec 子进程的 generic/specific AppArmor attr 均是 `unconfined`。在运行中的无凭据诊断容器中，OCI 配置的 `process.apparmorProfile` 缺失。重新加载规则和显式 `--kernel-features` 后仍相同，所以不能只信任 Kubernetes/Docker 的声明。

r5 在同一受限 Pod 中对固定 `/proc/self/attr/apparmor/current` 写入精确 ASCII 字节 `changeprofile agent-capsule-inputs`，无换行、无尾 NUL。读回 PID 1 和真实 exec 子进程均为 `agent-capsule-inputs (enforce)`；没有增加能力、移除 seccomp 或改变 Pod 声明。后续生产入口的严格核验/自收紧实现应由新镜像的候选测试再验证。

## 实际结果与证据

| 运行 | 永久消耗的 UID | 结果 |
| --- | --- | --- |
| `node-isolation-probe-20261026` | 41000000–41000002 | bind 核验被 ismount 误判，exit 1 |
| `...-r2` | 41000003–41000005 | 内核挂点核验通过，任意 tmpfs 未被拒绝，exit 1 |
| `...-r3` | 41000006–41000008 | 显式 kernel features 重载后，仍未附着实际 profile，exit 1 |
| `...-r4` | 41000009–41000011 | 直接测得 PID 1/exec 子进程 unconfined，exit 1 |
| `...-r5` | 41000012–41000014 | 固定自收紧后，全部隔离/停止预检通过，exit 0 |
| `...-r6` | 41000015–41000017 | 旧镜像缺少更新探针导入的 confinement 模块，任务启动前拒绝，exit 1 |
| `...-r7` | 41000018–41000020 | 新镜像正式 helper 自收紧，checkpoint base 路径隔离/停止预检通过，exit 0 |

r1–r3 镜像为 `arm-cluster-master:5000/agent-service-pi@sha256:5f0785b1c8f0b0ca16bc09015e49c6376e3f77be954c893b3e0427c3a3164779`；r2/r3 只在独立预检挂载了待提交的 mountinfo 修复文件。r4/r5 镜像为 `arm-cluster-master:5000/agent-service-pi@sha256:24deeab7e9e61cf41f1422e02e8309be966e5549c9bfa22675284c9406578bc9`，其源码已经包含相同修复。所有探针协调脚本和挂载文件的原文均随 ConfigMap 证据保存。

r5 的实际 Pod UID 为 `d40ec23a-7990-41f7-8d5c-9e6a3b2c49e6`，实际 imageID 与指定 digest 相同，restartCount=0，container terminated exitCode=0、reason=Completed，finishedAt=`2026-10-26T17:03:37Z`。预检测得：

- 两次 mount 系统调用完成，精确挂点具有 `ro,nosuid,nodev,noexec`，`ST_RDONLY=true`；无关 tmpfs mount 被拒绝。
- 降权子进程 UID 三项均 41000014、附加 groups 为空、有效/许可/继承/ambient capabilities 全空、NoNewPrivs=1。
- root 私有控制文件不可读，输入文件不可写。
- detached session writer 被实际 UID stopper 停止，独立观测无活跃同 UID writer、心跳不再变化。
- 使用非懒卸载，停止后内核 mountinfo 不再包含测试输入挂点。

五轮完整 Pod spec/status、实际 imageID/terminated、日志、不可变 ConfigMap、摘要及永久 UID 记录保存在服务器：

`/root/agent-service-deploy-20261026/node-isolation-evidence-20261027/`

OCI/实际进程标签诊断位于同目录 `apparmor-label-diagnostic.json`；汇总为 `summary.json`。已先导出这些证据，再按精确名称清理五轮测试 Pod/ConfigMap/deny-all NetworkPolicy。UID 预留记录保留，失败运行也不复用；没有为节点打永久就绪标签，节点重启后仍须重新加载/验收。

## checkpoint base 的独立复测

r7 使用新镜像 `arm-cluster-master:5000/agent-service-pi@sha256:2096ff87e130775bdab66afe171e21e91d9f4b1d509d983812e845b37be79c4e`，并直接调用其中的正式 `ensure_controller_confinement`。返回实际 NNP/seccomp 均 true、`apparmor_self_restricted=true`，标签从 unconfined 变为固定 enforce；再次核验不会重复转换。PID 1 与真实 exec 子进程均保持 enforce。

独立探针将同一套只读输入/降权/停止流程的受控 staging 路径改为 `checkpoint-sources/<id>/base`，目标为 `/work/<id>/base`，测试 `base/input.txt` 不可写。真实 mountinfo 和 statvfs 同时确认 `ro,nosuid,nodev,noexec`；无关 tmpfs 被拒绝。降权 UID 三项均 41000020、groups/capabilities 为空、NoNewPrivs=1；root 私有文件不可读、base 不可写、detached writer 停止以及非懒卸载全部通过，卸载后内核无该 base 挂点。

实际 Pod UID `3731720a-c373-4d0c-9966-29bd7b8f83c9`，imageID 与指定 digest 一致，restartCount=0，terminated exitCode=0、reason=Completed，finishedAt=`2026-10-26T17:32:03Z`。探针仍通过不可变 ConfigMap 固定协调源码及同版本 mountinfo 校验文件；原文与真实 Pod spec/status 一并留存，可核对测试范围，不能替代原生引擎验收。

r6/r7 证据保存在服务器 `/root/agent-service-deploy-20261026/node-base-isolation-evidence-20261027/`。已先导出，再精确清理这两轮 Pod/ConfigMap/NetworkPolicy；全部七轮的 UID 41000000–41000020 永久保留消费记录。没有启动额外模型请求。
