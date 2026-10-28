# 科研接入收尾：2026-10-29

用户要求休息前收尾，已停止新增模型请求、候选任务与业务切换。本页接续
[科研验证记录](RESEARCH_VALIDATION_20261028.md)，不表示科研生产迁移完成。

## 当前结论

- **科研生产 API 尚未切换**，继续使用原专用 Pi 后端；旧 Worker、PVC、报告和版本记录保留。
- 通用服务控制面继续使用 `772bef1` 镜像；临时开放过的两个科研路由已关闭，所有生产 route/pool 均关闭。
- 正式 Jobs HTTP 首轮失败，失败 Job、分配、停止证明和隔离业务数据库已保留；不能将此前候选通过写成正式业务交付通过。
- 正式入口的 Kubernetes 挂载权限问题已修复并推送。新 Worker 镜像已构建推送，无模型真实启动检查通过；尚未完成该镜像的真实生成、修订和正式 HTTP 交付验收。

## 代码与镜像

代码仓库 `zmhhaha/panghu_agent`，分支 `codex/agent-execution-service`：

| 提交 | 内容 |
| --- | --- |
| `6107b20fd23e099a0b354e1d82434fac86402df2` | 修正正式 Worker 的配置挂载检查和空工作目录初始化，新增启动边界测试。 |
| `1ea30e4` | 只修正测试夹具：无 capability 容器写只读测试文件前临时调整权限，写完恢复。 |

镜像均位于 `arm-cluster-master:5000`：

| 用途 | 不可变镜像 | 状态 |
| --- | --- | --- |
| 现有控制面 | `agent-service-control@sha256:74f564bc307bae4734699d56b7d0a5450061b163d946666c82ec9873219ae024` | 已部署，生产路由关闭。 |
| 启动修复 Worker | `agent-service-pi@sha256:c7208ced7b634686b3a8660da4506754538b1c1e493876d3a001fea17edd76ba` | 从 `6107b20` 正常 build.sh 构建推送，真实启动检查通过，未作为生产执行绑定放行。 |
| 新科研 API | `scientific-api@sha256:3043932ba7481e753ed5689a2081eb69381c79336047bd6e157fa615942156e0` | 从 `db4e998` 构建，离线工厂检查通过，尚未替换生产 API。 |

Worker 仍使用用户 `zmhhaha/pi-cage` 的固定提交
`e0262e65e43c6924164e995394f715dc5d19c210`、入口 `worker-cli.mjs` 和 Pi `0.99.2`。

## 本轮证据

服务器工作目录为 `/root/agent-service-deploy-20261026`。

1. 对原 `772bef1` Worker 镜像补做精确镜像科研生成：
   `candidate-pi-cutover-gen-r1`，生成和隔离业务发布通过；报告 3020 字节，
   SHA256 `eea05189387a4bc89863770e80c118b1a7fae3ca26119434ba1cd9bb05ac7809`。
   12 条模型请求均 complete/200，真实执行一次 Crossref 搜索和一次标识符核验。
   证据在 `evidence/candidate-pi-cutover-gen-r1/`，独立发布在
   `research-business-cutover-r1/verified-generation/`。该证据不覆盖新 `6107b20` 镜像。
2. 对原生成和修订证据离线复核后，通过正常 deploy.sh 临时开放了两个科研 Pi 路由。
   预留 UID `43000000..43065535`，全局单槽，不复用历史身份。
3. 正式 HTTP 验收 `scientific-agent-jobs-canary-r1` 使用新科研 API 镜像、实际科研调用
   token 和独立临时业务 SQLite。首个 Job
   `a1689dba-4241-4b6f-8372-eed2fb744f7d` / attempt
   `0f7b5973-53ac-4c50-a044-b943570cc772` 在启动阶段失败：
   `capsule_stopped_without_result`，`worker_start_claimed=false`。
   Pod `e19fb7af-4680-4541-89dc-38e44ebd7341` 已确认停止并退休，UID
   `43000000/43000001` 已消耗。失败业务数据在 `scientific-jobs-http-canary-r1/data/`。
   未生成报告、修订或交付回执，未切科研入口。
4. 无模型诊断 r1 复现配置读取失败。源码发现候选 probe 绕过了正式入口：整目录
   ConfigMap/Secret 挂载根带可写模式位，但挂载本身只读；`/work` emptyDir 初始为
   `0777`，原程序在收紧权限前就拒绝。修复使用实际 `statvfs/ST_RDONLY` 判断只读，
   保留 root 归属、文件模式、大小、链接范围等检查；空工作目录在初始化前收紧为
   `0711`，拒绝可写且已有内容的目录。
5. r2 诊断未启动：新候选 ConfigMap 尚未创建，Pod 因 FailedMount 到期；不计作代码
   回归。r3 补齐只读配置后，新镜像的 `read_config`、`controller_preflight`、
   `read_secrets`、`capsule_scope`、`assemble_app` 全部通过，模型请求为 0。
   实测目录初始模式为 `/work=0777`、control=0777、config=0777、secrets=1777。
6. Linux 禁网、只读源码、cap-drop ALL 定向回归 **143 passed**，包含 19 项新增启动
   用例和既有 host/confinement/binding 测试。运行时为 `6107b20`，测试包含
   `1ea30e4` 的四行夹具修正；首轮 3 项夹具写权限错误已修正，不重复累计测试数量。
   这不是重新完成全部 Linux/PG 回归或真实模型验收。

## 收尾与接续

关闭配置和部署记录在服务器 `session-close-20261029/`，诊断日志/Pod 元数据已导出。
临时诊断 Pod、ConfigMap、Secret 已清理；失败 canary 的数据和所有 UID 预留记录保留。
永久 UID 历史不得重置。候选诊断已用 `42000066..42000071` 与
`42000078..42000080`；下一轮优先从 `42000081` 开始并先复核预留与节点身份。

下次按以下顺序继续：

1. 以 `catalog-6107b20-scientific-startup/generated/` 为关闭状态候选目录，使用新镜像
   重新跑科研生成及固定证据修订，再独立校验产物、引用、选区范围和隔离业务发布。
   已准备但尚未执行的运维程序为 `revalidate-scientific-startup-fix.py`；修改其中候选
   UID 起点前先核对预留记录，不重用失败身份。
2. 根据新镜像的真实证据生成新的 acceptance 和 pins；保留已失败正式 Job 的旧绑定
   与 host 配置，不删分配历史，不把旧镜像验收自动继承给新镜像。
3. 仅开放两个科研 Pi 路由，串行重跑正式 Jobs HTTP：生成、固定证据修订、真实成果
   下载、独立业务发布、本地 publication ack、远端 candidate_received。
4. 通过后再排空旧科研任务，保存当前 API/网络配置，使用已有 renderer 的
   `--execution-backend agent-service` 补丁替换 API。检查两个 capability 都 ready，
   历史任务和报告可读，再验证正式 API 的幂等、取消与重启恢复。

PPT 与 Hermes 保持未开放。科研采用 `local-procedures-v2` 和受限元数据/摘要工具，
未安装 K-Dense 技能，不将结构校验等同于科学结论正确。普通更新步骤见
[科研接入指引](../../scientific_agent/AGENT_SERVICE.md)。
