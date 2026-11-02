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
