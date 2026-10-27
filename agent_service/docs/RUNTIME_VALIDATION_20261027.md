# 2026-10-27 原生引擎恢复验证

本记录接续 [上次服务器交接](SERVER_DEPLOYMENT_20261026.md)。控制面已部署；本轮保持生产路由关闭，现有 PPT/科研后端不切换。真实候选使用独立数据库、临时凭据和新 UID，不写入生产执行队列。

## 已定位与修复的问题

Pi 原先的 `engine_error` 发生在启动隔离准备阶段：控制器先 `chown` 到任务 UID，再对共享工作目录执行 `chmod`。控制器虽为 root 且有 `CHOWN`、`DAC_OVERRIDE`，但没有 `FOWNER`，不能对其他所有者的 inode 改权限，内核返回 `EPERM`。

提交 `9377090` 调整 Pi、Hermes、TrustedPreview 三处顺序：先在控制器持有 inode 时设置最终权限，再交给任务 UID。保留工作区 `0710`、共享项目目录 `2770`、共享文件 `0660` 和私有目录 `0700`，不增加能力、不改 AppArmor，也不修改只读 inputs/base 子树。补充固定词表的阶段/错误类别/errno 日志；不记录异常正文、命令、路径、凭据或模型内容。

第二个问题在进入 Pi 原生模型请求后显现：Pi 0.73.1 即使使用 `--no-session`，仍发送临时会话的 `prompt_cache_key`。llm-service 当前 Responses schema 不接受该缓存提示，返回 422。提交 `3d59029` 在验证该字段的类型、长度和控制字符后省略此缓存提示，先规范化再计算持久 admission 摘要；不改模型、输入或工具参数。不同缓存提示的同内容请求归入同一持久身份，未完成的 admission 仍禁止再次付费请求。模型代理同时补充固定词表的上游阶段、状态码和响应类型诊断，不记录响应正文或秘密。

第三个问题来自截止时间的精度差异：权威 envelope/grant 使用浮点秒，`datetime.fromtimestamp()` 会四舍五入到微秒，可能令原生请求的时间戳比精确 grant 晚不足一微秒，触发严格的截止时间检查。提交 `9623df8` 仅在转换结果向后越界时将原生 datetime 减去一微秒；envelope、grant、lease、watchdog 和持久状态保留原始数值，不增加容差或延长期限。新增测试覆盖相邻浮点值、Pi/Hermes 请求和 watchdog 的权威截止时间。候选 wire builder 的 scope、已消费状态、grant 过期和截止时间四项 guard 也分别使用固定错误码，便于辨别下一轮失败原因。

这一精度风险已由代码和测试确认；不能据此断言它就是 `candidate-pi-ppt-r4` 的实际触发项。r4 的历史日志只定位到 `wire_builder`，没有区分具体 guard；后续 r5 已越过该阶段，但不能从事后结果反推 r4 的具体触发项。

后续原生多轮请求还暴露了历史保真问题。Agent 代理提交 `96fad52` 保留受限工具结果内的文字/内嵌图片数组，提交 `5ad6c90` 保留经有限值校验的 assistant `phase`，不删图片或改变工具调用关联。r6 的第二次原生请求因未支持 `phase` 在 admission 前返回 422；加入 phase 修复后的 r7 已完成更多原生请求，但随后仍在 admission 前返回 422。最后一条已完成响应已确认包含 `reasoning` 类型项，目前只记录结构统计，相关历史输入支持仍在补充；尚不能认定它是剩余失败的全部原因。

## 禁网真实节点诊断

| 诊断 | 永久消耗 UID | 结果 |
| --- | --- | --- |
| `pi-startup-offline-r1` | 42000006–42000008 | 诊断脚本误用了 AppArmor 不允许的源路径，mount 失败；不作为产品缺陷证据 |
| `pi-startup-offline-r2` | 42000009–42000011 | 使用正式 input-sources 路径，准备/资源/版本检查通过，隔离准备报 EPERM |
| `pi-startup-offline-r3` | 42000012–42000014 | 原镜像只读挂载已提交的修复源码，Pi/Hermes/preview 权限交接全部通过，实际 SGID 保留 |

这些 Pod 使用 deny-all 网络、相同 capability 集、RuntimeDefault 和固定 AppArmor，未携带真实凭据、未启动原生 Agent、模型调用 0。r3 还验证 preview 私有 HOME 保持 `0700` 和独立 UID。实际 Pod JSON、日志保存到服务器 `evidence/<诊断名>/` 后精确清理资源；r2/r3 另外导出了包含探针源代码的不可变 ConfigMap，r1 未导出 ConfigMap，不能声称它也保留了同等源代码证据。UID 失败也不复用。

## 完整 Linux / PostgreSQL 回归

最近一次完整运行对应源码 `a067949`：**1071 passed，零失败、错误或跳过**。通用服务 1034、科研连接器 22、PPT 连接器 15；模型调用 0，`cleanup_complete=true`，临时容器/socket 清理完成。专项回归不累加到完整运行数字。

| 源码 | 完整通过数量 | 报告 | run ID |
| --- | --- | --- | --- |
| `b656a3b`（运行时与 `9377090` 相同） | 938（901 + 22 + 15） | `/root/agent-service-linux-evidence-20261027-r2/report.json` | `agent-pg-20261027071552-3872be206b85` |
| `3d59029` | 951（914 + 22 + 15） | `/root/agent-service-linux-evidence-20261027-r3/report.json` | `agent-pg-20261027074525-27057aaadcbc` |
| `9623df8` | 954（917 + 22 + 15） | `/root/agent-service-linux-evidence-20261027-r4/report.json` | `agent-pg-20261027082445-bbe3c5bd0362` |
| `a067949` | 1071（1034 + 22 + 15） | `/root/agent-service-linux-evidence-20261027-r5/report.json` | `agent-pg-20261027134721-9a173de9a2c9` |

最新运行耗时 82.987 秒，测试进程 77.866 秒；另行只读核验本轮容器列表和临时 socket 目录无残留。表中均为各版本独立完整运行，不将多轮数量相加。首轮有两项旧 Dockerfile 断言仍使用缓存重排前的 requirements 路径；修正断言后完整重跑。这些验证使用禁网模型/Worker fixtures 与真实独立 PostgreSQL，不代表真实领域执行通过。

## Pi 候选进展

`candidate-pi-ppt-r3` 使用 `9377090` 镜像，现已进入原生 Pi 执行，原权限错误消失；随后模型请求因上面的 422 返回 `model_http_transient`。代理记录为连接性回显 complete/200 和一条 admitted/native 请求，没有将后者重新付费重试。

实际 Pod UID `3ee26389-b768-4638-8c2f-85d2d658604f`，terminated exit 2、restart 0，结束 `2026-10-27T07:20:10Z`。Worker/preview writer 停止已观察到，租约到期后同 grant 返回 401。证据：`/root/agent-service-deploy-20261026/evidence/candidate-pi-ppt-r3/`。Pod/Service/NetworkPolicy/ConfigMap/临时 Secret 已清理，UID 42000015–42000017 永久消费。

`candidate-pi-ppt-r4` 使用缓存提示修复后的候选，但在 `wire_builder` 阶段失败，模型代理只记录一次连接性回显，未进入原生任务模型请求。该轮不能证明缓存提示修复后的真实原生请求已通过，也无法从现有日志确定是哪一项 wire guard 触发。证据目录为 `/root/agent-service-deploy-20261026/evidence/candidate-pi-ppt-r4/`。

`candidate-pi-ppt-r5` 使用 `9623df8` 的 Pi 镜像，已越过 wire builder 并进入 `execute_native`，但仍以 `model_error` 失败，产物 0。模型代理记录两项调用，连接性回显和原生请求均为 `complete`、HTTP 200；HTTP 200 和完整持久化只证明代理收到并保存了响应，尚不能证明其中 SSE 事件或原生模型结果语义有效，具体原因继续调查。原生输出计数为 `physical_output_bytes=50568`，没有记录或公开模型正文。

r5 Pod UID 为 `f8a66f6a-4dd7-4e7b-8e06-a517c514bffb`，独立观察到 terminated、exit 2、restart 0。运行时间为 2026-10-27 17:15:31–17:15:51（Asia/Shanghai；证据时间 `09:15:31Z`–`09:15:51Z`）。租约到期后同 grant 返回 HTTP 401。证据保存在 `/root/agent-service-deploy-20261026/evidence/candidate-pi-ppt-r5/`；Pod、Service、NetworkPolicy、ConfigMap 和临时 Secret 已清理，UID 42000021–42000023 永久消费，不复用。

`candidate-pi-ppt-r6` 的代理 ledger 共两项，分别为连接性回显和第一次原生请求，均为 `complete`、HTTP 200。随后 Pi 发起第二次原生请求，在持久 admission 前因尚未支持的 assistant `phase` 返回 HTTP 422，因此该拒绝请求不在 ledger 内。不能将这一轮写成“已 admission 的原生请求返回 422”，也不能将两项 HTTP 200 当作领域执行成功。

`candidate-pi-ppt-r7` 使用 `5ad6c90` 的 phase 修复后，ledger 共七项，分别为一次连接性回显和六次原生请求，全部 `complete`、HTTP 200。第七次原生请求随后仍在 admission 前返回 422，未登记为新的上游模型调用。最后一条已完成原生响应出现 `reasoning` 类型项，正在补齐其历史输入支持；当前仅确认结构，未公开模型正文，也未据此宣布根因全部解决或候选通过。

| 候选 | Pi 镜像 SHA256 | 实际 Pod UID | 永久消耗 UID | 实际运行时间（UTC，2026-10-27） |
| --- | --- | --- | --- | --- |
| r6 | `cddea6be06a3f4b0f493c96015a1f7e987d1b1a838019b1d9d2149e29a1c7f28` | `c7ae28d2-82dd-4051-8212-85e1b7999a60` | 42000024–42000026 | `12:05:50Z`–`12:06:11Z` |
| r7 | `fdd19f28a61a38a8bd8e815c610a1fb7213fcf12811949f638b60b2a2d98ac16` | `5c02c295-883b-4bd0-a29e-477073ec1266` | 42000027–42000029 | `12:31:58Z`–`12:33:00Z` |

r6/r7 均独立观察到 Pod terminated、exit 2、restart 0，全部写进程停止证据已确认；租约到期后原 grant 返回 HTTP 401，临时资源已清理，UID 不复用。响应 ledger 以 root 所有、`0600` 权限保存，文档只记录状态与结构，不公开其正文。这两轮均未通过真实 PPT 领域验收。

## 最新源码与业务试接准备

以下为代码准备进展，不代表它们已部署为生产控制面或已完成真实领域验收：

- `548fb33` 补齐 Pi 优先的 PPT/科研部署接线与试接文档。科研可显式选择 `agent-service`，默认仍为原 Pi 后端；复用既有 ESO 业务 token，并单独输出精确 API 网络规则。PPT 窄构建补齐 `diagnostics.py` 及归档隔离导入检查。
- `96fad52` 和 `5ad6c90` 分别补齐受限工具图片结果与 assistant phase 保真；对应修复的存在不代表完整原生多轮请求已通过。
- `03b5fff` 将候选验证入口扩展到 PPT 五个 profile 与科研生成/修订的分阶段场景。它提供验证路径，不能据此勾选七个 profile 已通过真实模型或业务发布验收。

共享 `llm-service` 的原生 Responses 修复位于平台独立分支 `codex/agent-native-responses`，`e358a5d` 保留 Pi 的 assistant phase 与工具内嵌图片历史，`e5351c4` 增加受限自包含 reasoning 历史。后者已构建并部署，实际镜像 SHA256 为 `4090cb323de19140b07983f8b8e9fe812d2f1f3085136df2270e9a4ba7e96c86`，rollout Ready；独立比对 Deployment 模板确认仅镜像变化，既有别名、模型路由、凭据与配置均沿用。回退镜像为 `arm-cluster-master:5000/llm-service:03f6b68`，证据 `/root/llm-service-pi-20261027/rollout-e5351c4.json`。

`e5351c4` 的实际生产镜像在禁网、只读、UID 10002、无 capabilities 的独立容器内完成 141 项测试：140 通过，1 项因未安装可选官方 OpenAI SDK 跳过，模型调用 0。日志 `/root/llm-service-pi-20261027/linux-tests-e5351c4.log`。该镜像验证不等于真实 Pi 多轮领域执行通过。

通用服务 `a067949` 同步支持受限 reasoning 历史回放，并修正 Linux 单元 fixture 的合成所有权边界；生产输入仍要求 root 所有。已推送并同步独立服务器检出。新镜像已构建推送：control `39b3b6e22c619a0ef28ccdd967f25f6cf9d2e2cf1b2f62a145c15278d2e42fc7`，Pi `cd6af229bb0f33c6e45944472824ae510e596d5426aa94c58a88e2f12f85e827`。新目录 `/root/agent-service-deploy-20261026/catalog-a067949` 的路由和池全部关闭；完整 Linux/PG 及真实领域结果另行记录，不能用构建成功替代。

当前优先级为先完成 Pi 对 PPT 五阶段及科研生成/固定证据修订的真实验收，再按单槽顺序接入两个业务试用。Hermes 不是本轮前置条件，共享网关的额外 Hermes 参数扩展暂停；业务接线步骤见 [PPT 部署接线](../../ppt_service/deploy/agent/README.md) 和 [科研接线](../../scientific_agent/AGENT_SERVICE.md)。

## 最新构建与部署边界

源码 `9623df8` 的三个镜像已构建、推送并核对摘要；control 使用固定 Python base，Pi/Hermes 使用固定原始 donor：

```text
arm-cluster-master:5000/agent-service-control@sha256:d5f5f6fba8cc11fe899e9f5d48b43a003d3335a1765029e74a354c0bc78bf796
arm-cluster-master:5000/agent-service-pi@sha256:224e21bd7b35fea6d7f685db6567c6487593b9084cd6d2e63c296d369ac117f0
arm-cluster-master:5000/agent-service-hermes@sha256:f216fcb19923ba6e12ea2a138eb0ec7b020f9cbcf6da436bcbcc3781f07ae8fb
```

`9623df8` 已成功部署，`agent-service-api`、`agent-service-dispatcher`、`agent-model-proxy` 三个 Deployment 均核对为 `1/1 Ready`，`agent-workers` 当前无 Pod；Pi r5 使用上方固定 Pi 镜像完成了这一轮候选执行。控制面部署引用新的目录和镜像不代表 Pi/Hermes 生产组合已放行。

最新线上控制面仍为 `9623df8`。候选镜像、后续 Agent 源码和平台共享网关构建不构成生产升级；构建、控制面就绪和 954 项回归也不等于真实领域验收。全部生产路由继续关闭，PPT/科研现有后端未切换，没有登记 production acceptance。下一步先完成 Pi 多轮 Responses 历史的剩余修复，使用全新候选身份和预留 UID 验证，再按实际证据推进 Pi 各领域阶段、业务网络、单槽试接和发布/回滚验收。
