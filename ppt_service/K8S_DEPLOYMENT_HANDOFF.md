# K8s 部署对接与后续验收交接

更新时间：2026-10-23。用户已要求：由另一 agent 负责整体 K8s 部署，本会话暂停独立 Docker 环境配置与真实模型重试，等 K8s 部署完成后再做集成校验。

## 当前事实

- API/UI/Hermes 原有交互与两次确认保持；本轮没有把 Pi 接到线上规划路由。
- Pi 的队列 fencing、资料 staging/解析、冻结候选、UID 隔离、任务模型授权，以及正常/取消/过期/恢复的真实容器测试已有证据。
- 新增挂载接收校验 `PreparedPptContext.export_for_mounts()/from_mounts()`，核对任务、库存、解析、素材、checkpoint 与 project/base。支持五阶段上下文验证，但不能据此声称五阶段实际制稿已通过。
- `worker/pi_stage_entry.py` 是受控开发的 plan_initial 入口；`worker/pi_proxy_entry.py` 是任务授权代理入口。基础 `PptProfile.production_ready=False`，开发入口不能当作整个生产链已开放。
- `scripts/run_pi_plan.py` 和 `control/pi_container_backend.py` 是 Docker 主机适配/开发验证代码，不是 K8s 后端。

最近真实规划尝试：`/root/ppt-pi-real-plan-20261023-a1`。在容器启动前的拓扑检查以 `container_network_mismatch` 结束，Worker/代理均未开始模型执行；authority 撤销、两个容器停止、网络和防火墙清理均有记录。没有真实供应商调用、规划候选或发布。Docker29创建态的实际 IPAddress 为空、请求地址在 IPAMConfig 中，已用无启动无凭据探针确认并修复；修复后尚未重跑真实规划，按用户新指示等待 K8s。

## 代码与验证

主要代码：

- `worker/pi_context.py`：挂载前后不可变上下文校验。
- `worker/pi_profile.py`：已有 PPT 各阶段提示词、严格数据与确认校验。
- `worker/pi_stage_entry.py`：固定 /work namespace、只读挂载/权限/网络检查、官方技能、真实 Pi CLI；只允许 plan_initial 开发调用。
- `worker/pi_proxy_entry.py`、`worker/model_proxy.py`、`worker/model_grant.py`：provider key 留在代理，任务 token/预算/租约绑定、流撤销。
- `control/pi_model.py`、`control/pi_container_backend.py`、`worker/container_lease.py`：授权与物理生命周期参考实现。
- `scripts/run_pi_plan.py`：有显式付费开发门、两容器网络 cage 和原始素材重建的 Docker CLI。

Linux 只读无外网回归70/70通过，0skip，9.561秒；覆盖 mounted context、旧 profile/runner、代理 entry、CLI和网络拒绝入口。对应代码归档 `bc0713e5bd54389b4871af7b84b9a20b61dfb060da7ef11caa2723fcd127d3e8`（1136640bytes），服务端 `/root/ppt-pi-planning-code-20261023-a2/`。之后 Docker29 pending/assigned 拓扑修复的 CLI 回归20项、18通过/2明确 Windows FIFO与symlink跳过；该最后修复及 worker等待Running放行未在真实模型或K8s重验。

新候选基础镜像本地主机 ID：`sha256:2a09398d26a387ac79b3b7b635202062ee7856f151b47eba0cb773b7e020e1a1`，Node24.21.0/Pi0.73.1/PPT Master c350bc31。它是本地测试衍生镜像，继承 TEST-ONLY entrypoint，代码此前通过只读 /code 挂载提供；不能仅把此 ID 当作已推送可直接运行的 K8s 业务镜像。部署构建必须包含当前源码、固定依赖和正确实际 entrypoint。

## K8s 必须对接的真实差异

1. **运行入口与路由**：Web默认 Pi 路由、完整阶段执行/可信检查点发布仍未接通。部署代码不等于自动获得 Pi PPT 功能。保留 Hermes 回退及现有 API/确认数据，不更改 UI 契约。
2. **固定网络参数**：当前开发入口校验 `model-proxy:8080`、172.30.240.2 和 Docker bridge/无默认路由；Docker iptables cage 不适用于 K8s。K8s 应提供实际 Service/NetworkPolicy 或等效限制并调整可信配置及前置校验。Worker 不得直接接供应商凭据，也不能放开宿主/metadata/任意互联网访问来绕过检查。
3. **租约时钟与存储**：当前 `ContainerLease` 使用同一 Linux boot ID 和 monotonic 时钟、原子控制文件、递增序号。跨节点 Worker/代理不可直接共享这种期限数值；要么明确同节点并共享适当私有控制存储，要么改为经过验证的分布式租约协议。缺失、过期、撤销、回退都须 fail closed，不能复活旧 attempt。
4. **凭据与控制文件**：K8s Secret 仅给代理 provider key；Worker 只得每次任务的短期 token。/work/inputs、/work/task、/work/base 只读，project/result独立可写，audit/authority仅可信控制器可写且Pi UID不可读，runtime/HOME为新的临时空间。不要迁移历史 HOME、Pi/Hermes native session 或 SQLite。
5. **身份/权限**：按 attempt/fencing token 分配可核验且不复用的 UID，使用持久的部署级分配，不沿用每次临时 CLI 根的测试账本。可信监督器需要经验证的 CHOWN/DAC_OVERRIDE/KILL/SETGID/SETUID；实际 Pi 子进程须清空组/各类有效权限并启用 NNP。不得以 privileged/SYS_ADMIN 代替适配。
6. **停写证明**：Docker inspect/CID 证明需要换成 K8s 实际 pod/container/进程终止证据。Job succeeded 不能自动证明代理、预览与全部写入者已停止。收集、恢复、清理和发布必须持完整身份/fencing且确认停写；验收不接受单纯的完成状态或空 UID 扫描。
7. **阶段与技能**：开发 entry 目前只开放 plan_initial；后续阶段须绑定真实确认和指定 checkpoint/base_version，再对接受管官方预览与独立质量检查。技能取只读镜像内官方路径，固定 Python工具链 `/opt/hermes/.venv/bin/python`；不能开启全局生产标记来掩盖未完成接线。

## 部署完成后的验收顺序

请提供实际访问 URL、namespace/工作负载名、所用镜像版本及就绪状态。届时本会话基于 K8s 实际部署验证：健康/权限 → 真实公开素材初始规划 → 两次确认防越阶段 → 生成/修订 → 取消/过期/恢复/停写 → 独立产物检查。PowerPoint 人工打开编辑、最终验收与正式发布仍单独保留，不能由 agent 自报通过。

当前本地18505转发指向旧Docker开发站，不代表新K8s服务。部署工作由另一agent负责，本会话不再继续配置或替换这套独立Docker验证环境。

## 2026-10-24：先跑通 Hermes 网站流程

用户已决定先使用独立 Hermes Worker 跑通 PPT，之后再实施通用 agentic 层。当前实现与部署应以
`deploy/hermes/README.md` 为准；前面的 Pi 章节保留其研发和验收历史。

网站继续使用现有 API、数据库队列、两次真实方案确认和阶段检查点。`control/dispatcher.py`
通过 `PPT_EXECUTION_BACKEND=kubernetes` 使用新的 Kubernetes 后端；材料解析和结果校验在
无网络沙箱内运行。Worker、模型代理和沙箱均为任务专属 Pod，使用精确镜像 digest 与狭窄
PVC subPath；不连接私人 Hermes 仪表盘、HOME 或数据卷。

`control/kubernetes_preflight.py` 提供无模型的实际网络与停止证明检查。网络预检通过、镜像与
配置就绪后，才明确开启 Controller 和网站执行准入。部署配置的默认值仍禁用付费执行。

生成阶段通过独立程序检查后，可以保存 `status=review` 的待审核草稿，提供所有者专属的
PPTX 下载和独立 PNG 预览。草稿不更新最新正式版本，不允许作为修改基线；完整导出等价、
视觉与 PowerPoint 打开/编辑/保存/重开等剩余门须真实完成。失败或 needs_input 不转成草稿。
没有独立审核的图表源数据预期时，默认只接受无图表候选；实际图表不能由 Agent 自报数据
充当核验基准，不把这个阶段表述为通用图表工作流已经验收。

以上说明记录代码接线与运行边界。真实镜像、网络、模型执行和人工确认的部署回执需另行
补记，不能从本节推导生产全流程已经通过。
