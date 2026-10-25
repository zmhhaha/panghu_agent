# K8s 部署对接与后续验收交接

更新时间：2026-10-25。当前部署与业务验证以文末 Hermes 进展为准。8页DSH白底架构导读review草稿已成功入库，后端已具备所有者预览与下载；浏览器实际核对待恢复登录。前面的 Pi 开发、暂停独立 Docker 环境配置与模型重试等说明保留为历史记录。

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

## 2026-10-25：真实部署与 DSH 源码项目

Hermes执行扩展现已实际部署到 `ppt-service`，Controller和网站均已开启执行。
Controller采用UID/GID10000、runAsNonRoot、drop ALL，与现有CephFS state所有者一致；
真实文件复制、冻结、同UID权限操作及清理已通过，namespace继续执行restricted策略。
网站CSRF多标签页修复已部署，真实登录后的设置保存和源码资料上传成功。

第二次真实规划使用的执行恢复修复提交为 `12eb8a1`。相应Controller、Worker digest、
完整无模型预检，以及两次真实规划的失败记录见 [DSH业务回执](docs/dsh-code-learning-live-20261025.md)。
build4预检scope `8616ce31-6787-4852-893c-e57a67ee1e37`：三角色各14项网络检查全部通过，
真实writers-stopped及cleanup均通过，退出0。预检修复显式处理Proxy探针SIGTERM，
停止观察45秒覆盖30秒终止宽限期；没有强删Pod或绕过finalizer。

用户选择直接依据仓库里的DSH集成源码，制作“dsh代码学习”项目的8页中文PPT。
源资料已上传并解析成功，原项目及演示要求保留。首次真实规划因HermesError失败后
已正常停止清理，观察到一次gpt-6.1-sol请求完成，输入685/输出65 tokens，费用unknown。
新Worker明确配置上下文预算和Ceph SQLite journal模式，并对原run状态GET作有界恢复，
第二次真实规划job `daaa0f6a-1cca-48e0-8a27-fe4343ec2c3b` 的Worker已实际完成：
222.64秒，`hermes_status_retries=2`，候选manifest为 `awaiting_confirmation`。
独立校验sandbox退出2，实际 `validated.json` 为 `stage1_contract_invalid`；
方案未入库，也没有PPT。不能将Worker完成或候选自报状态当作网站接受方案。

本次Worker提示没有给出 `control/planning.py` 接受的严格结构，候选使用
`stage1_items/service_form_parameters`，未匹配官方stage1或outline/design格式。
后续 `ad97c11` 已补充明确的stage1、stage2提示合同，保留严格校验并重建镜像。
transport错误码过滤原本不接受数字，将包含数字1的错误泛化为
`validation_execution_failed`；`ad97c11` 已修复错误传播，原始具体校验回执仍保留。

网站状态同步修复 `f2d8838` 已build/deploy并Ready，活跃任务使用权威GET补同步，
“刷新状态”包含当前任务；其上线不改变上述历史校验失败。

## 2026-10-25：build5 及两轮规划确认完成

Controller为 `sha256:db94823530c997989b768533d615b4da876e3386539b5dfd01b533f6d41bc1dc`，
Worker为 `sha256:f19950f0bf09bcf80f822e03b703891b6162d4f0b586e43e52fd4d53e2d5c995`。
完整无模型预检scope `fd0d4a1a-ab59-4945-947f-e5c1fa82d25d` 四个成功flags全true，
三角色各14项通过，退出0，之后恢复Controller/site执行。

第三次初始规划 `fdddea19-dd1e-4e43-8def-2c7258be1698` 成功入库方案修订1。
用户明确确认后，第二阶段 `4b1d35ff-eeaf-4438-9115-7f429e216f1c` 成功入库方案修订2，
包含三套完整8页方向。用户再次选择 `direction-a` 白底架构导读，已保存网页确认。
规范准备job `fb19dbf7-4bee-49b6-90e4-320a39c8f6c6` 已成功且停止，项目修订15；
scope为 `6d51eaa5-8c08-5a98-9604-b28703d07d15`，规范与锁文件已齐全。

`7e11b96` 修复官方沟通字段和对象形式逐页表达未显示的问题，99项前端测试及TS/build通过；
实际网站digest `sha256:47fb9b2f17dba7e8ba5c6a88c56f2ce43f6092dff3b6e6039dfcf24d947de453`，
浏览器已核实完整正文与逐页表达显示。目前仍无PPT产物、预览/下载或PowerPoint人工验收，
最终进展及用量边界见上述业务回执。P1/P2总项和正式发布门继续待验。

## 2026-10-25：首次生成失败与build6修复

prepare成功后自动创建generate job `92a705d4-3068-4416-ab71-cdf91b897ae7`，
scope为 `a74b24ad-d169-54a2-afcf-815277ef4c44`。任务已失败并停止，公开错误码为
`quality_gate_failed`，manifest为 `needs_input`。虽然8份SVG已写出、作者early/final静态
检查均0错误0警告，官方 `visual_review.py` 仍以退出码3报 `playwright not installed`；
未完成真实业务PNG、视觉检查、finalize或PPTX导出。失败SVG只保留为私有证据，
不能手动提升为成功checkpoint或可发布草稿。

已复现的根因是Hermes的 `bash -lc` 将 `python3` 解析为 `/usr/bin/python3`，
而运行库在 `/opt/hermes/.venv/bin/python` 中。固定venv下Playwright及真实Chromium
渲染、像素检查均通过，并非缺包或审批拒绝。修复 `94a18c6` 已推送、服务器已快进拉取：
官方Python工具全部使用固定venv和脚本绝对路径，保留拒绝/超时停止规则，不安装运行库。
对应回归61 passed、5 skipped、90 subtests passed，独立review通过。

同一提交修复网页串联prepare/generate后退回显示prepare成功的问题，保留最后实际任务，
避免掩盖generate失败；前端100项、TypeScript及production build通过。

build6镜像已构建推送。新Worker的官方8页无模型渲染检查通过：`passed=true`、
`no_model_calls=true`、`rendered=8`、`failed=0`、`all_background=0`、`png_count=8`、
`preview_stopped=true`，退出0；8张1280×720 PNG及确定色块检查通过。
报告 `/root/ppt-official-render-smoke-20261025-b6.log`；检查使用无网络、只读根、
UID10000、drop ALL及no-new-privileges容器，不能代替DSH业务PPT或人工视觉验收。
build6执行镜像已按关闭准入、禁用状态部署、完整预检、恢复启用的顺序成功部署。
本次预检scope为 `f02133c8-46d9-4625-b49c-8e31104b3598`，`passed`、`no_model_calls`、
`writers_stopped`、`cleanup_complete` 全true，proxy、worker、sandbox各14项探针均0失败，
报告 `/root/ppt-network-preflight-20261025-b6.log`。网站通过标准
`bash deploy.sh --skip-build` 部署成功，日志 `/root/ppt-site-deploy-20261025-runtime6.log`；
site2/2、Controller1/1就绪，网站digest为
`sha256:96070732086f057bb9b653a889e134f481ecda90801352f8e143042f76a88f1e`，
Worker为 `sha256:cc3c6765e5b7c84adebab7fe54b3e49b7085266ca5249cf5c7308d996c5b774e`。

两轮用户确认保留。网页重试prepare `e8aa2e0e-ceda-469d-9578-69b9cb1b4369` 已通过，
自动创建generate `c4aec6c4-4ac9-4e9f-85d8-12e9bbab956b`，scope为
`bca0747a-1c8f-5ad5-9835-bcf38d09c231`，最终failed、stopped=true；阶段截止时间为
`2026-10-25 11:54:46.876179+00`。私有候选包含8份SVG、8份PNG、1份PPTX，
独立沙箱 `ec1cb298-970f-4057-8fdb-47004f6e0d2d` 退出2，原诊断
`validation_execution_failed`。候选未成为版本，不提供下载。代理61次请求中60次completed、
1次client_disconnected；token与费用未汇总。
OpenSpec的build6回执revision为 `9bba9bc451fad31e5090804ba96d71e0450333a6`，validate=true，
不据此记录业务或人工验收通过。最终业务结果继续补记到
[DSH业务回执](docs/dsh-code-learning-live-20261025.md)。

## 2026-10-25：build7备注与诊断修复，审核稿成功入库

build6候选的官方delivery检查passed，8slides、8notes、errors空、motion0、hidden0；
服务独立 `render_validation` 却硬编码notes必须0，拒绝了方案已确认的文字讲者备注。
修复 `c696a52` 已推送、服务器已拉取：只接受无备注或每页一个纯文本备注，并严格检查
根、内容类型、双向关系、孤儿/共享/重复部分和备注资源，要求官方与原生计数一致。
新增脱敏 `failure.json` 与K8s只读失败诊断回收；这些诊断不会把失败改成成功。
范围191 passed、1 skipped、14 deselected、110 subtests passed；首次4项Windows平台环境
失败及随后排除项不计通过。K8s范围46 passed，覆盖范围不相加。

build7Worker镜像为 `sha256:0b7aeb56f48a81a162e8863e7dbef08e4b6f8d44ff421e6b2047d291b6ff7d73`，
已构建推送。Controller为
`sha256:ec99998b3379048f95d8db4acdd8452b903f60ac754b086851bd780a543f079e`。
已按disabled→完整预检→enable部署通过；网站未重建，沿用
`sha256:96070732086f057bb9b653a889e134f481ecda90801352f8e143042f76a88f1e`。
预检scope为 `2b5f36c0-828b-4c37-87a7-73fdf58f3238`，四个成功flags全true，
proxy、worker、sandbox各14项探针均0失败，退出0，日志为
`/root/ppt-network-preflight-20261025-b7.log`。
已停止候选的只读测试副本 `/root/ppt-actual-candidate-check-20261025-b7-r2` 使用manifest
SHA256 `ab5ee2ba5c93fb5824b2a0113c0a8def8edd10df755a290c8dcb5eaef6691e43`。
首个副本因缺home报 `session_database_missing`，仅补齐5个 `.db`、不拷配置/凭据后，
禁网、只读根、UID10000、drop ALL的完整 `run_candidate_delivery` 退出0，
4个程序质量门通过，`review_artifacts_ready=true`。

测试仍为 `accepted=false`、`publication_authorization=false`，人工、视觉及导出构建合同待验。
无模型调用，原job和数据库不变；未把旧候选变成版本，也未开放下载。日志为
`/root/ppt-actual-candidate-check-20261025-b7-r2.log`。OpenSpec已补notes及failure诊断规格，
revision `54d486cdbbe079e5fbc344c8677c32e05a3acceb`，validate=true。

网页新prepare `be8d728f-a180-46ae-bb7c-195ecdb66159` 已通过，自动generate为
`94a9e5a7-fe29-4e8c-a5ff-db32bb509a1d`，scope为
`38fe0ae8-74c0-52b7-8ff4-4ec3e4fee57f`。prepare最终succeeded、stopped=true，scope为
`90f1a9d1-fd09-5913-82de-3e5e917a8f19`；generate最终succeeded、stopped=true、error=null。
项目修订18、stage为 `awaiting_review`、`active_job_id=null`。

审核稿Version `31d2bb16-f372-44eb-a2bf-16178b1d0eca` 已入库，status为 `review`，
包含8PNG、8SVG、1PPTX。PPTX为56768bytes，SHA256为
`8ed10a226228b4b65ec733f861204bcf56d9c3b46078a99251624f1325d7761c`，最终存储hash核对match=true。
独立证据nonce为 `2b5c0a40-c6fd-4eec-b306-18a558e49cbd`；source manifest SHA256为
`e6d1a17de2b8fd16db6944ff737ba2bfe637e54fa1010252c8b437d0f74f8773`，receipt SHA256为
`2c34f42647e091421b15f313a968a6083ce6100390fe2773801c498b59132caf`，source fingerprint为
`7bf19ed0c29eb7a97f1eb405275949b7598e628f843057a48fffe646ef787548`。

草稿仍为 `eligible_as_revision_base=false`、`publication_authorization=false`，必需待验门为
`independent_export_build_contract`、`independent_visual_review`、
`powerpoint_open_edit_save_reopen`、`postflight_warnings_review`。不能把review入库当作正式验收或发布。
prepare代理18次请求（16completed、1client_disconnected、1proxy_shutdown），generate代理50次
（49completed、1proxy_shutdown）；完整tokens及费用未汇总，不记为0。

最后浏览器会话已过期，已打开Casdoor并请用户登录。后端所有者专属预览与下载已具备，
网页实际预览8页和下载仍待恢复登录后核对，未宣称已经在浏览器完成下载。

服务器审核稿入库成功结果已同步到OpenSpec，最终revision为
`cf6fb80c68c3af97cc3a81eba403623ca7b60760`，validate=true。任务仍26完成、35待办，
P1/P2总项未勾选；浏览器核对及四项质量门继续pending。
