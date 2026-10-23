# Pi 工作台当前状态与交接

## 当前分工：等待另一 agent 完成 K8s 部署

用户已要求在整体代码部署 K8s 后再做集成校验，本会话目标已暂停，不继续配置独立 Docker 环境或重试真实模型。部署对接细节见 K8S_DEPLOYMENT_HANDOFF.md。新 mounted context/开发规划入口已实现；Linux70/70回归通过。最近实际规划在启动前的 Docker29拓扑校验失败，没有模型调用；对应 pending/assigned 修复已完成本机20项回归（18pass2明确skip），但未真实重跑。真实 Pi PPT 业务链仍未跑通，生产 profile、人工验收与发布门保持。


## 2026-10-23 主机受控容器生命周期更新

新增control/pi_container_backend.py与worker/container_lease.py：exclusive root私有intent/launch metadata、flock+原子fsync记录、完整identity/nonce/boot/monotonic deadline/seq/CID绑定；容器只读监控失效即撤销ModelGrant并取消Pi。create/observe/heartbeat/stop/recover/collect/cleanup接口已联调，productionUserTasks=False，仅固定离线探针。CID精确匹配、无restart/auto-remove、私有PID namespace，Docker exited+RunningFalse+RestartingFalse+Pid0才给可用StopProof，收集/清理前再验，unknown/paused/dead/foreign/daemon异常不能释放槽位。

真实PG+Docker+Node/Pi四场景4/4通过32.017秒：正常心跳/exit0→证明后freeze→validating但无Plan/Version；DB取消→真实静默模型流与Pi error=cancelled→外层证明后cancelled；无续期10秒到期→Pi退出2且禁止后续心跳复活；新backend从耐久记录恢复仅停止不重启，旧心跳失效。完整代码快照8c2aac197815e0647c57fbcb1bbc021e9da96ef41d9f5a8c94c5ba003103b79a，目录/root/ppt-pi-container-lifecycle-20261023-a2。首次正常用例沿用旧30秒自检断言失败，修正后四项重跑，不计失败为成功。

最后收紧容器/TaskModelProxy续期为提交前计算deadline、扣除DB事务耗时并按队列/配置TTL上限；最终定向实际正常回合1/1通过7.139秒，PG TaskModel11/11通过4.110秒，源码6b8f0aca074b6f64c4ada3f6c927e25566b4f2a1b1e248651a20ed76f75dc6c6在a3。backend本地30pass1Linux ownership skip，租约协议Linux8/8通过。权威细节control/PI_CONTAINER_BACKEND.md。

当前未调用真实供应商、未生成PPT、未切live站或生产profile。固定测试已接实际容器生命周期，但PPT业务context在挂载命名空间的验证/重建、真实模型准入和PiDispatch/API实际路由仍待实现，独立质量、PowerPoint人工及正式发布门保留。测试控制器持Docker socket，实际Pi容器不持socket/DB凭据；UID账本为隔离测试数据，不是生产UID池保留。


## 2026-10-23 任务模型授权更新

新增worker/model_grant.py与control/pi_model.py，并给原model_proxy增加可选任务授权；默认None保留Hermes行为。身份绑定project/job/attempt/fencing token/phase，严格monotonic TTL、有限预算/并发、过期撤销不可复活；续期不补预算。真实静默SSE撤销/超时会shutdown底层socket，late connect撤销后不发送HTTP，accepted连接上限8且grant模式header/body idle1秒；不声称能中断OS DNS或撤回已付费供应商请求。

TaskModelProxy使用独占root0700私有目录、0600原子fsync receipt及私有usage，新鲜随机任务key仅进入child env；上游key不进入child。只有真实RepositoryQueue fenced心跳事务提交成功才续期，取消/失去fencing/持久化失败均撤销。普通构造器无条件保留paid/network门，独立offline工厂只用固定合成provider key与可信connector。没有HTTP公共续期/权限配置接口。

Linux独立PG组合71/71通过0skip14.267秒；构造器最终收紧后单独10/10真实PG回归3.944秒。真实Node/Pi在network-none候选镜像上再次完成两个任务授权回合，第3请求被429拒绝且未触及上游，usage已归属job/attempt/phase，grant关闭activeFalse/inflight0，UID无残留容器已移除。报告87290e6e7c9ab4401f4e92258ab9da95dbe7f58e50ed1b955288d04a6c5db8a5，细节control/PI_MODEL_PROXY.md。

限制：本机既有actual-Pi测试在CLI版本探测阶段超时，0个代理请求，不算通过；实际CLI证据来自固定Linux镜像。本轮不调用真实供应商，不生成PPT，不切换网站；新的代理生命周期组件尚未装进live DockerBackend/PiDispatch的provision/heartbeat/stop路径，生产Pi profile与人工验收/发布仍关。


## 2026-10-23 实际执行器边界更新

固定离线入口现已在新候选镜像2a09398d26a3上跑通：真实Node24.21.0/Pi0.73.1、PiRuntime fixture_mode=False、真实UID31800降权、独立scratch/HOME、RO inputs/task/base与RW project/result。合成Responses在network-none容器loopback内提供固定工具指令；Pi经既有代理完成两个SSE回合，读取实际资料、写入两个文件，六项RO写入均EROFS、控制私有文件拒绝，子进程有效/允许/继承/ambient caps均0、groups空、NNP1，停止无残留且容器exit0/已移除。源码归档与完整测量报告哈希见control/PI_CONTAINER.md。

新镜像只修正继承镜像的/work父目录归属为root0755，未下载依赖或切换live站。固定入口依然TEST ONLY，使用明确的测试profile和合成readiness flags；PptProfile生产准入、真实供应商模型、任务绑定代理凭据、实际PPT制作/视觉与PowerPoint人工验收均未通过或开启。preview未在本次实际Pi回合执行。不能把实际CLI/降权/工具传输通过当作业务profile生成成功。


## 2026-10-23 验证更新（优先于下方历史快照）

现有开发站未切换引擎，生产Pi执行仍关闭；“只剩调度接线”的早前判断不完整。fixture模式下的模型命令由测试容器运行，验证一个空的Pi UID并不等于真实生产降权验收。固定容器supervisor、模型代理/任务凭据与真实引擎准入仍需实现，不得仅切换fixture_mode或production_ready打开。

新增pi_coordinator.py的显式fixture装配和单次CLI。实际独立PostgreSQL/schema验证：队列领取→engine=pi构建→现有解析器CLI读取真实Markdown→固定假Pi→停止证明→候选，任务停在validating，无Plan/Version发布。CLI要求显式--fixture-once，拒绝数据库名不以_fixture结尾的连接；不连接开发站业务库。测试使用已有解析器CLI在一次性隔离容器中执行，CLI的DockerSandbox嵌套容器解析入口尚未在本轮端到端运行，不能混同。

真实PG组合31/31通过（6.052秒，无跳过），覆盖并发领取仅一个胜者、完整token/attempt fencing、严格True停止证明、取消终态、永久预启动错误安全终结不重排队。Linux安全组90/90通过（5.582秒，无跳过），含真实预览与stopfailure/link sentinel回归；控制器使用已审查五cap CHOWN/DAC_OVERRIDE/KILL/SETGID/SETUID，无FOWNER/SYS_ADMIN。早前四cap测试不覆盖完整私有目录预检；本轮不再假定root无需DAC_OVERRIDE即可遍历foreign0700目录。

修复：错误事件映射到业务白名单；重复UID绑定verify拒绝歧义、writers列出所有角色/重复分配供恢复；Pi artifact checkpoint不要求Hermes session；所有writer停止前禁止reclaim/删除；特权权限修改先全面拒绝链接/硬链接/特殊文件，采用nofollow FD；不修改data_root及其祖先；预览audit父目录0711，私有HOME/tmp0700/config0600；保留live_preview证据，仅移除明确瞬时锁。

开发站服务器healthz确认test/manual_acceptance pending；本地18505转发已恢复。既有Hermes、UI、两次确认、独立验收和正式发布门保留。任务仍需推进至实际Pi容器与模型调用，不能以fixture产物开放网站规划。


日期：2026-10-22。范围：`panghu_agent/ppt_service` 的 Pi 执行链路。
**结论先行：链路已逐段实现并在 Linux 真机验证；尚未接入网站调度，未跑真实模型，`PptProfile.production_ready` 仍为 `False`，网站规划/生成仍返回 503，PowerPoint 人工验收与正式发布门未打开。**

## 1. 已完成且可复现的部分

| 模块 | 作用 | 验证 |
|---|---|---|
| `worker/pi_context.py` | 由真实 staging 产物构造不可变上下文，逐项核对任务/资料/检查点/确认 | 9 项；Linux 通过 |
| `worker/pi_profile.py` | 只在上下文精确匹配时放行资料与后续阶段；未绑定仍只允许初始规划 | 9 项 |
| `worker/pi_runner.py` | 执行前重建上下文，独立空目录运行，内部构建严格执行信封 | 6 项；Linux 通过 |
| `control/pi_staging.py` | 不可变资料/恢复布局，拒绝链接与越界 | 39 项；Linux 全通过 |
| `control/pi_uid.py` | 持久 UID 账本，每次分配新 UID；`verify` 只核对不分配 | 22 项；Linux 全通过 |
| `control/stop_gate.py` | 按 UID 杀净并证明（pidfd、连续两次空扫描） | 13 项；Linux 真实双 UID 验证 |
| `control/pi_collect.py` | 停止后冻结候选，实测哈希索引，失败仅隔离自己新建目标 | 22 项；Linux 全通过 |
| `control/preview_launch.py` | 以独立 UID 启动官方预览，`/proc` 实测降权生效 | 15 项；Linux 真实服务验证 |
| `control/pi_pipeline.py` | 串起 准备→执行→停止→（可选）预览→冻结 | 8 项；Linux 8/8 |
| `control/pi_container.py` | 固定 docker 参数向量：只读输入、可写项目、无网络、无 env | 9 项；主机探针实测 |

Linux 验证均在一次性容器内进行：固定镜像、root 仅带必要 capability、`no-new-privileges`、只读根与源码、无网络、私有 tmpfs，`--rm` 后按容器名确认无残留。

本机汇总（Windows）：

```
cd panghu_agent
python -m unittest discover -s ppt_service/tests/control -p 'test_pi_*.py'   # 100 项，22 平台跳过
python -m unittest ppt_service.tests.test_pi_context ppt_service.tests.test_pi_runner ppt_service.tests.test_pi_profile  # 24 项
```

## 2. 刻意保持关闭的部分

- `PptProfile.production_ready = False`；真实模型准入 `require_fixture_admission` 只放行 `fixture_mode=True`，生产准入直接拒绝。
- 流水线尚未被任何调度器调用；旧 Dispatcher 仍只服务 Hermes 路径，且不会自动重试可能已付费的阶段。
- 生成期间的并发预览未实现，当前是"先生成、后预览"。
- 候选产物不是发布：无 `accepted`、无 `version_id`、无可用恢复检查点（generate/revise 的 checkpoint 仍需业务侧权威版本绑定）。
- 跨进程/跨服务的 `StopProof` 只是进程内可信契约，不是认证证书。

## 3. 要走通"网站真正用 Pi 出 PPT"，还缺三件事

1. **调度接线**：从数据库领取 job/attempt，调用 `pi_pipeline`，把状态与错误映射回现有 API；需要业务侧 outbox 或等价幂等提交，避免旧 dispatcher 与新路径同时为一个阶段启动执行。
2. **生产准入配置**：模型凭据的注入路径（当前刻意未接）、容器 entrypoint、以及真实 Pi 工具/图像的能力验收。
3. **人工验收**：用真实模型生成一份样稿，完成打开 → 编辑图表数据 → 保存 → 重开的 PowerPoint 人工验收；在此之前不得开放邀请或正式发布。

## 4. 复现 Linux 验证

把需要的模块打包上传后，在一次性容器内运行（示例为容器挂载）：

```
docker run --rm --name <name> \
  --entrypoint /opt/hermes/.venv/bin/python --user 0:0 \
  --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID --cap-add KILL \
  --security-opt no-new-privileges --read-only --network none \
  --tmpfs /tmp:rw,nosuid,nodev,mode=1777,size=512m \
  --memory 1g --pids-limit 256 --cpus 2 \
  -e PYTHONPATH=/code -e PYTHONDONTWRITEBYTECODE=1 \
  -v <host-code>:/code:ro -w /code <pinned-image> \
  -m unittest ppt_service.tests.control.test_pi_pipeline -v
```

预览相关用例需要固定 PPT base 镜像（含 `/opt/ppt-master` 与 `/opt/hermes/.venv`）；其余用例可用现有 API 镜像。

## 5. 仍需保持不变的门

- 网站两道真实确认（方案选择、页面计划确认）与指定版本修订基线；
- 独立产物检查与视觉审查；
- **PowerPoint 人工验收**与**正式发布授权**：本次任何测试通过都不代表这两项已完成。

## 6. 关键文档

`control/PI_STAGING.md`、`PI_UID.md`、`STOP_GATE.md`、`PREVIEW_LAUNCH.md`、`PI_PIPELINE.md`、`PI_CONTAINER.md`；OpenSpec change `add-hermes-ppt-workbench` 为规格与任务状态权威来源（远端），另有设计提案 `add-pi-first-agent-execution-service`。
