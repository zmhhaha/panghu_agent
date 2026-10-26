# 合同与执行绑定注册验证

2026-10-26，分支 `codex/agent-execution-service`，在架构提交 `4adc03b` 上实现。
配置和边界见 [REGISTRY_BINDINGS.md](REGISTRY_BINDINGS.md)。本轮没有模型调用、
生产镜像构建/推送、K8s 业务部署或现有 PPT/科研迁移。

## 结果

| 验证 | 结果 |
| --- | --- |
| Linux 完整 `tests/agent_service`，真实独立 PostgreSQL | 578通过，0失败/错误/跳过；pytest 38.661秒 |
| 其中 PostgreSQL 用例 | 11项，包含新增绑定重连、旧幂等请求和资源请求摘要持久化验证 |
| Windows 完整同目录 | 557通过，21跳过，28个subtests通过；191.26秒 |
| 更新后的公开配置示例 | 3份解析通过、全部禁用，Worker catalog与控制端组合一致 |
| 差异检查 | `git diff --check` 通过 |

Windows 跳过项涉及本机未配置 PostgreSQL、Linux 专属能力和符号链接权限，均已在
Linux 完整运行覆盖。Windows 有一条既有 Starlette/httpx 弃用警告。两平台数量是
各自完整运行，不相加为新的通过总数。

覆盖内容：独立资源目录及严格交叉引用、schema2直接迁移/旧结构拒绝、未知领域或
动态执行选择拒绝、包/环境/适配器/模型策略变化使组合批准失效、不可变解析、原
幂等 Job/排队任务保持冻结组合、数据库重连、完整绑定进入请求摘要及资源授权、
API安全投影、Worker readiness/原生admission/K8s scope精确核对、Pi镜像实际资源
与清单一致性、独立宿主验收记录绑定。既有取消、停止、事件及产物回归一同通过。

## Linux 证据

主机：`root@192.168.137.101`。
隔离目录：`/root/agent-service-registry-validation-20261026-r1/`。

| 项目 | 值 |
| --- | --- |
| 运行ID | `agent-pg-20261026084614-47d469cf8e11` |
| 源码快照 | `source/`，71个文件及独立 `source-hashes.json` |
| 清单SHA256 | `c4ac595b36bbeac1e278c3baaf86cf663af393e23c38d34baa153d5805a6e0e0` |
| 上传归档SHA256 | `614bec1578f0d69a806aa28421b66e577f29b3c165dbf3a412e774d07ee4f9af` |
| 报告和日志 | `linux-evidence/report.json`、`postgres-tests.log`、`postgres-container.log` |
| 测试依赖镜像 | `arm-cluster-master:5000/ppt-service-site@sha256:96070732086f057bb9b653a889e134f481ecda90801352f8e143042f76a88f1e` |
| PostgreSQL镜像 | `sha256:ef738a34a8651d11b2bace81c55c7e2187f786b484add6c83070884340074368` |

运行沿用 `run_isolated_postgres.py --suite all`：已缓存镜像、`--pull=never`、禁网、
只读、测试UID10000、PG UID70、无capabilities、NNP、临时PGDATA和独立Unix socket。
只挂载服务及其测试源码，不挂生产数据库、模型凭据或私人Agent HOME。

报告 `passed=true`、`model_requests=0`、`cleanup_complete=true`；本轮临时容器与
socket均已清理，快照和日志保留。源码清单与上传归档摘要在服务器读回核对一致。
54个可执行/依赖源码文件中，53个与最终工作树逐一一致，另1个测试文件仅删除末尾
多余空行。测试后补写文档，并将3份公开配置示例改为真实内置CONTRACT.md描述及
完整Worker catalog，已另行解析并核对组合。

在已缓存同一镜像的独立 Linux Docker 主机，可从 checkout 根目录复现：

```bash
python3 tests/agent_service/run_isolated_postgres.py \
  --code-root /absolute/path/to/checkout \
  --report-dir /absolute/path/to/new-evidence-directory --suite all
```

Windows 回归命令：

```powershell
python -m pytest tests/agent_service -q -p no:cacheprovider --tb=short --basetemp=.agent-validation-registry-win
```

## 验证边界

新增绑定测试使用明确的synthetic合同批准记录、假Worker/HTTP/K8s客户端和本地任务，
不代表验收报告摘要本身证明真实模型能力。Linux真实数据库验证持久和事务语义；
实际候选镜像、CNI、外部模型/工具、短期模型授权签发、Hermes生产宿主、领域
输入/collector/checkpoint以及PPT/科研连接器仍待完成。生产readiness保持关闭。
