# 五个通用共享执行槽

日期：2026-11-02（Asia/Shanghai）。科研、PPT、视频继续共用公共执行底座；用户选择跨平台共享五槽，同一平台也可以并行执行多个任务。

## 配置与行为

在实际生效的公开目录中加入以下片段，保留其余调用方、合同、绑定、池、镜像和模型配置：

```json
{
  "dispatcher": {
    "poll_seconds": 1.0,
    "lease_seconds": 60,
    "execution_slots": 5
  }
}
```

`execution_slots` 是严格整数1至16，省略时为1。多槽拒绝静态 `workers`，仅使用每 attempt 独立的 Kubernetes Worker。任务请求不能选择槽或修改槽数。

五个任务可同时占槽，第六个排队。每个认证 `tenant/caller` 内按提交时间及ID领取；调用方之间优先选择最近领取的全局 fence 最小者，再比较提交时间。因此有新平台等待时，它在后续空槽获得轮转机会；没有其他平台等待时，一个平台可以占满五槽。不会抢占正在执行的任务。

每槽独立调度、恢复、轮询和取消。一个Worker网络请求阻塞不延迟其他槽的续租或启动。停止不确定时只保留该槽，其余四槽仍能执行；全部占用或隔离时仍需等待。

排队仍消耗首次接收时冻结的绝对截止时间。本次不延长历史任务、不改变幂等键、不自动重试付费任务。业务确认、候选采用和正式发布继续由各平台负责。

## 持久化与身份

`agent_slot` 的id 1至N保存占用；只有id=1的 `next_fence` 分配全局执行编号。增加、减少槽数和重启不重置该序号；Agent/helper UID仍由全局fence派生，永久allocation记录继续阻止身份复用。

`agent_scheduler_config` 持久保存槽数。初始化仅在全部槽空闲时允许更改；存在运行或隔离占用则返回 `scheduler_busy`。新进程配置与持久槽数不一致时，领取或查询活动槽返回 `scheduler_configuration_mismatch`。

完成、确认未创建和隔离恢复的释放路径都核对精确job/attempt/fence及当前占用。租约过期只进入隔离，不直接释放；陈旧回执不能清除后续任务或其他槽。重启读取原attempt，不重复创建已经运行的Worker。

## 上线与回滚

1. 核对实际控制镜像、ConfigMap、合同和正在进行的其他发布，保存配置、工作负载、数据库备份及digest。新控制代码必须支持实际目录，不能用旧目录覆盖已上线的增量。
2. 完成独立PostgreSQL回归，核对节点资源和上游并发限制。五槽增加实际Worker Pod容量，不能绕过物理资源或模型限流。
3. 暂停旧dispatcher，确认全部 `agent_slot.job_id` 为空、无未退休allocation和活动Worker Pod。任务自然完成或经原取消/停止流程结束，不手动清槽。
4. 保留公开目录其他字段，设置 `execution_slots=5`，用新控制镜像执行 `init-db`，再更新API、dispatcher及model-proxy。镜像使用 `latest@sha256:...`，标签和部署注解记录来源commit/digest。
5. 读回持久槽数、五行占用、Ready、配置摘要和实际调度/取消/恢复。完整领域产物质量仍按各平台独立验收。

回滚先排空全部槽，用新版初始化器切回 `execution_slots=1`；确认只剩id=1且全局序号未重置后，才运行旧单槽二进制。直接用旧二进制覆盖五槽不是有效回滚。

旧运维工具若要求 `agent_slot` 只有一行，会在五槽下拒绝执行；应检查预期槽数和全部占用、未退休allocation与Worker。不要删槽绕过检查。仅查询id=1不能证明空闲；`Store.active_jobs()` 返回全部占用（含隔离），`active()` 返回首个占用。

## 验证

本地专项覆盖共享五槽、第六等待、同平台并行、轮转、单槽隔离、精确释放、重启、忙迁移拒绝及全局fence。真实PG用例未设置专用测试URL时安全跳过。

服务器使用现有隔离测试器，不读取生产数据库或模型凭据：

```bash
python3 tests/agent_service/run_isolated_postgres.py \
  --code-root /absolute/path/to/reviewed-source \
  --report-dir /absolute/path/to/new-evidence-directory \
  --suite connectors
```

测试器使用已缓存固定镜像、禁网临时PG及独立schema，多进程验证真实锁竞争。测试结果与生产启用状态分别记录；本地通过不等于线上已启用五槽。

### 2026-11-03 验证记录

调度代码来源为 `a8cb04ea66f8a653f07089b6952839404c45c350`。独立 Linux / PostgreSQL 回归：Agent Service **1575 passed、1 skipped**，PPT 连接器 **62 passed**、迁移 **8 passed**，科研连接器 **22 passed**；去重合计 **1667 passed、1 skipped**，无剩余失败。跳过的是现有需要 root 才能实际切换 UID 的 DAC 用例，测试容器使用非 root。测试器缺少科研 workspace 挂载及测试镜像缺 `python-multipart` 的首次收集错误保留原记录，修复后科研专项通过；没有把重跑结果重复计数。

最终候选镜像另在禁网、只读根文件系统、非 root、无凭据和无生产数据库的临时容器内验证真实 CLI / Store / Dispatcher。显式合成的 Worker 适配器证明五次 start 同时进入、第六排队、同平台占满五槽、新调用方获得下一空槽、单槽隔离、其余四槽继续、精确取消/完成和重启不重复启动；全局 fence 持续至 11。该验证不代表五次真实模型生成。上线脚本另有 **11 项离线检查通过**，覆盖配置保留、Service 选择器、迁移失败、响应丢失及正常新任务不会触发误停调度。

公开证据位于 `192.168.137.101`：

- `/root/platform-slots-validation-20261102-r2-evidence/{report.json,postgres-tests.log}`
- `/root/platform-slots-validation-20261102-r4-scientific-evidence/{report.json,postgres-tests.log}`
- `/root/platform-slots-image-smoke-20261103-r1/report.json`

上述隔离验证的模型调用数均为 0。

只读容量核对：每个 Worker 固定请求 `100m CPU / 128Mi`，五个合计 `500m / 640Mi`。实际 90 个池的节点约束为未指定或主节点；主节点当时按 requests 计算尚余 `3.5 CPU / 3472.6Mi`，Pod 数为 `74/110`，`agent-workers` 没有 ResourceQuota，因此没有发现五个任务的调度准入阻挡。三个约 1Gi 的 NanoPC 节点有内存保护污点，未计为可用容量。单 Worker 上限仍为 `1 CPU / 2Gi / 4Gi ephemeral-storage`；本次没有验证五个 Worker 同时到达全部资源上限或上游模型的五路峰值负载。公开容量记录：`/root/platform-slots-capacity-20261103.json`。

### 2026-11-03 生产启用

用户授权协调并上线后，视频与 PPT 均交出公共控制面发布窗口。已暂停并排空旧 dispatcher，使用独立 init-db Job 扩为五槽，再升级 API、model-proxy 和 dispatcher。Job 不匹配两个线上 Service 的选择器。仅公开配置的 `dispatcher.execution_slots` 从缺省 1 改为 5，保留全部 **20 条路由、90 个 Worker 池、既有绑定和 Worker 镜像**。

| 项目 | 已读回结果 |
| --- | --- |
| 调度代码来源 | `a8cb04ea66f8a653f07089b6952839404c45c350` |
| 保留的视频控制面基底 | `arm-cluster-master:5000/agent-service-video-control:latest@sha256:4ad2742ca9cb551bfb856eaaa02c3a0e46869c0469d01c605b563d6e2b762eac` |
| 上线控制镜像 | `arm-cluster-master:5000/agent-service-control:latest@sha256:eff3ade9c08bb297c27a69e4015a8ab28bdb326a7b78b4217292a8d1e2785c13` |
| 补丁文件清单 SHA-256 | `377152f4229e4577f5851f7dc5e885cba36f7f17bbf8d8c61fb3851d531ce450` |
| ConfigMap | `agent-service-config-slots5-e0f0eb750a9c4671ffab` |
| 公开配置 SHA-256（规范 JSON） | `e0f0eb750a9c4671ffab974c20f96080e69a154bd76325e80741f63420c5e25b` |
| 持久布局 | `agent_slot` id=1…5；`agent_scheduler_config.execution_slots=5` |
| 全局序号 | id=1 的 `next_fence` 切换前后均为 **79**；其他行不分配全局序号 |
| 调度进程 | 配置 5 槽；6 个进程线程（主线程及五个调度线程） |
| 控制组件 | API、model-proxy、dispatcher 均 1/1 Ready，实际镜像摘要相同 |
| 切换后即时占用 | 五槽均空、未退休 allocation 为 0；未新增业务任务 |

镜像仅覆盖经过验证的四个调度文件，继承当前视频基底的其余代码；源文件 `root:root / 0644`，运行用户继续为 `10000:10000`。构建核对基底文件哈希、非 root 可读性、继承层与入口、推送 manifest 摘要；没有用较旧 main 全量重建去覆盖视频动态合同。发布使用 `latest` 并以摘要固定，来源记录在镜像标签和 Deployment 注解中。

最终收据：`192.168.137.101:/root/platform-slots-rollout-20261103-r1/verified-state.json`；同目录保存原工作负载、公开配置、迁移前状态及 Job。构建收据：`/root/platform-slots-overlay-prepared-20261102-r1/receipt.json`。生产读回确认配置、布局、调度线程和组件就绪；并发、故障与隔离行为由前述独立 PG 和候选镜像合成验证支撑。本次上线没有发起真实模型生成或重新验收各平台成品质量。
