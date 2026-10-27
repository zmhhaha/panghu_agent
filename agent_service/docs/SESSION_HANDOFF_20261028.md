# 通用 Agent 服务收尾与接续（2026-10-28）

用户要求先以 Pi 覆盖 PPT、科研再试接业务；Hermes 不作为前置。随后用户要求休息收尾，已停止新模型请求、测试、构建与部署。本文记录已核对的边界，不代表业务已迁移。

## 代码与线上状态

- 业务仓库 `zmhhaha/panghu_agent`，分支 `codex/agent-execution-service`。功能代码最新提交 `df3343ffc97d68a4c34d1c536fb429536e1e6902` 已提交、推送，服务器独立检出 `/root/agent-service-deploy-20261026/panghu_agent` 已同步。原 scientific 检出未切换。
- `0fa6c11` 合并当前主线的 PPT 审核、资料和恢复功能。主线 schema 3 属于审核记录，Agent Outbox 正常新增为 schema 4，保留已有数据、追加约束和权限；修正部署、迁移 Job、readiness 与清单检查。尚未在生产执行此迁移。
- `df3343f` 明确每个 PPT 阶段的 manifest、项目相对路径、真实 SHA/大小和 quality 报告引用；不放宽 collector。科研将经哈希验证的原始 CLI 指令渲染为真实工具 API，并关闭重复加载原始科研 skill；资源文件与包哈希不变。WorkerHost 增加固定阶段/错误分类，不输出正文、路径或凭据。
- 线上 Agent 控制面仍为 `9623df8`，control digest 为 `d5f5f6fba8cc11fe899e9f5d48b43a003d3335a1765029e74a354c0bc78bf796`。API、dispatcher、model-proxy 均核对 `1/1 Ready`。七条 route、十四个 pool 继续关闭，没有登记生产 acceptance，PPT/科研未切换。
- 平台仓库 `zmhhaha/armbianbegin` 的 `codex/agent-native-responses` 已推送并部署 `e5351c4`。llm-service 实际 digest `4090cb323de19140b07983f8b8e9fe812d2f1f3085136df2270e9a4ba7e96c86`，仅镜像变化，现有模型路由及凭据保留；回退镜像 `arm-cluster-master:5000/llm-service:03f6b68`。
- `a067949` 的 control/Pi 镜像已构建，但新 `df3343f` 尚未构建或部署。Pi 使用上游 `@mariozechner/pi-coding-agent 0.73.1`，不是 pi-cage；现有独立 pi-cage 未调整。

## 验证结论

完整 Linux/真实 PostgreSQL 回归最近覆盖 **a067949**：1071 passed（1034 Agent +22 科研 +15 PPT），零失败、错误、跳过、模型请求，临时容器/socket 清理完成。报告 `/root/agent-service-linux-evidence-20261027-r5/report.json`，run `agent-pg-20261027134721-9a173de9a2c9`。此结果不覆盖之后的主线合并及 `df3343f`。

新版本本机定向回归为 79 passed、1 skipped；PPT 部署/迁移相关五组为 190 passed、21 skipped、56 subtests，另一次窄构建 28 passed。它们是不同范围的独立运行，不累加成一次完整通过。Windows 完整 PPT server/control 检查最终 731 passed、66 failed、41 errors、120 skipped、234 subtests；这是失败结果。已发现 MAX_PATH、Windows 缺少 Unix 权限/信号、25 毫秒 fake sandbox 时限等限制，不能据此声称 Linux 全套通过，也未修改生产隔离来迎合测试。

llm-service 的实际生产镜像禁网/只读/非 root 回归为 140 passed、1 个可选官方 OpenAI SDK 测试跳过，模型请求 0。

真实 Pi PPT r8 和科研生成 r1 均未完成领域验收：

| 候选 | 模型账本 | 结果 | 永久消耗 UID |
| --- | --- | --- | --- |
| `candidate-pi-ppt-r8` | 17 项 complete/200 | `worker_output_invalid`，0 交付产物 | 42000030–42000032 |
| `candidate-pi-research-r1` | 10 项 complete/200 | `worker_output_invalid`，0 交付产物 | 42000033–42000035 |

两轮均使用 `a067949` Pi digest `cd6af229bb0f33c6e45944472824ae510e596d5426aa94c58a88e2f12f85e827`；已独立观察到 terminated、exit 2、restart 0，全部 writer 停止，过期 grant 401，临时资源清理。证据保存在服务器 `evidence/<候选名>/`。HTTP 200 只证明请求成功返回，不能证明领域任务或业务发布成功。

离线只解析保存的 write 参数，没有执行模型的 bash/edit 命令。PPT manifest 的类型和 stage1 主要字段正确，但路径/quality 引用有确定不匹配，已据此补精确合同。write-only 恢复缺少 bash 创建的文件，不能声明已完整重现其文件树。科研恢复的七个文件在实际 Python 3.13 Pi 镜像中通过原结构校验，后续缺少未恢复的控制器保护观测；这不是线上观测丢失的证明。宿主管理 Python 3.11 的 `Path.is_junction` 异常已排除为不同环境，未修改生产代码。

科研 r1 保存的模型响应中没有 academic 工具调用，需要继续核对实际 CLI 的工具激活。初次 SDK 探针虽成功加载扩展，但 active tools 连 read/write 都为空，尚未复现完整 CLI 装配，结论不充分；没有据此改动 `allowed_tools`。固定 Pi 源码探针结果在本机 ignored `agent_service/rendered/pi-tools-source.jsonl` 和服务器同名文件。

## 已完成的清理

- Agent Worker Pod 数为 0，没有新模型请求、测试、构建或部署进程留在运行。
- 独立 PPT validator 曾用真实 Kubernetes transport 完成无模型挂载、8 项拒绝网络探测、精确 Pod UID 停止与清理验证。镜像为 `ppt-agent-validator@sha256:83f0cf2fcbabd001388fad142ff0107fb9623c269bfc82a847c6a53c6fd0784c`。
- 收尾已删除合成 namespace `agent-candidate-ppt-r8b` 和静态 local PV `candidate-ppt-r8b-data`；先确认无 Pod，PV 精确指向合成目录且 reclaimPolicy=Retain，再清理资源。底层测试数据、数据库和证据未删。专用 ServiceAccount 随 namespace 删除，私有短期 token 文件已移除；旧 validator settings 不能直接复用。
- 收尾证据 `/root/agent-service-deploy-20261026/session-close-20261028.json`。独立 validator 历史证据位于 `ppt-validator-r8b/`，响应账本保持受限私有权限，不打印模型正文或密文。

## 下次继续

1. 从 `df3343f` 新建源码归档，跑现有 `run_isolated_postgres.py --suite connectors`。现在第四进程包含真实主线 schema 3→4、审核追加约束测试。再独立跑 Linux PPT server/control/deployment；排除顶层执行 Docker 的独立运维 CLI `test_network_isolation_inspect.py`，公共 OAuth ConfigMap 源码单独核对哈希后只读挂载，不挂任何平台凭据。
2. 读取固定 Pi SDK 源码，完成 CLI 装配后的无模型工具激活验证。read/write 必须已激活后，才判断 academic_search/verify；当前不能先认定 `--tools` 排除了扩展。
3. 按原 `build.sh` 构建新 control/Pi，用固定原始 donor，不能以候选镜像递归作 donor。生成全关闭目录，再用新 schema 4 测试项目跑 PPT initial→options→prepare→generate→真实发布门→revise。旧 r8 合成库来自另一种未上线 schema 3，不伪造迁移账本、确认或发布基线；保留其历史即可。
4. 重新建立独立 validator 临时资源及短期调用身份；旧环境已清理。无模型预检历史可参考，不能将已删除的资源视作仍 Ready。
5. 科研在工具激活确认后重新验证真实受限搜索，再做已发布基线的固定证据修订。所有候选顺序执行，避免服务器同时承担多个原生任务。
6. 只有真实 Pi 七合同、独立领域校验及完整组合证据通过，才登记 acceptance、重算最终 pins/host 记录，再逐业务单槽试接。Hermes 继续保持未放行，不阻塞 Pi。正常构建/部署入口不变，不用会话运维 helper 代替生产入口。

下一组候选 UID 从 **42000036** 起，仍须先查服务器 `uid-reservations.json`；失败范围也不得复用。OpenSpec 变更仍为 `add-pi-first-agent-execution-service`，任务保留 10/24，不 apply/archive。
