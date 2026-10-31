# 科研项目工作台

2026-10-31 修复进展见 [证据整理与人工恢复](EVIDENCE_RECOVERY.md)：包含原 DSH 项目失败复验、旧证据保留、人工导入、诊断和元数据边界。

2026-10-30：新工作台已在原科研域名上线。真实浏览器链路与原 HTTP 项目完整链路、
独立来源/账本/修订/下载审计和最终健康检查均已通过。科研 API 与五阶段执行绑定已
部署，任务结束后 Worker Pod 为 0、全局槽可用；失败历史和原有报告继续保留。

新流程围绕持久研究项目推进：研究目标 → 研究方案 → 文献与证据 → 报告初稿 →
审阅修订 → 定稿导出。保存要求不调用模型。确认研究方案后才能检索，检索后自动进行
一次证据整理并停下；用户确认证据与大纲后才能写初稿。每次规划、检索、大纲、写作或
修订分别提交有限执行，结束、确认所有写入者停止并保存证据后，按清理流程回收任务 Pod。
等人阅读、确认、定稿或离开页面不占 Worker；下一次模型修改创建新的尝试，并发提交
进入全局队列。任务并发仍由 Agent 服务统一限制。

规划、检索、大纲、成稿分别使用 `research.plan.v1`、`research.retrieve.v1`、
`research.outline.v1`、`research.draft.v1`。只有检索阶段可调用学术检索工具。写作核对
原始受保护检索观测、用户材料、纳排选择、证据快照和确认记录，不伪造本次写作检索。
章节修改继续使用 `research.revise.v1`，固定来源和选区，生成候选后明确采用。

项目按登录身份隔离。业务状态保存在已有 SQLite 服务的新表
`scientific_workspace_projects` 中，整行 JSON 使用修订号 CAS 原子更新；不更改旧报告和
任务表。每次写操作要求期望修订和幂等键。模糊提交只通过查询恢复，不能自动再次创建
同一模型任务。旧输入完成的成果保留历史标识，不能推进最新项目。

## 公共配置

域名仍为 `https://scientific-agent.panghuer.top`，继续使用现有 OAuth2 代理、Casdoor
应用和 Cloudflare 入口。无需新建入口或供应商 Key。业务 API 使用原有 Vault/ESO
投影的 Agent 调用令牌，模型仍由 Agent 服务和 llm-service 管理。

现有 `ui:7860` 由 React 静态页面和同源 Python 网关提供；内部 API 仍为
`api:80 → 8000`。网关只接受已验证 OAuth 代理写入的身份，转发时丢弃浏览器的
Authorization、Cookie 和额外身份头。写请求同时校验精确 Origin 与 CSRF token。
网络策略仅允许既有 `oauth/oauth2-proxy-scientific-agent` Pod 进入 UI，UI 进入 API；
两条均只约束入站，不改变共享服务的出站配置。

## 在服务器构建与部署

在服务器拉取本分支的已提交代码，然后从仓库根目录执行。沿用服务器原生 ARM64
Docker 和私人 Registry，不在本机代构建。

```bash
export REGISTRY=arm-cluster-master:5000
export PLATFORM=linux/arm64
export IMAGE_TAG=latest
bash scientific_agent/build.sh --target api
bash scientific_agent/build.sh --target ui
```

构建只发送 Dockerfile 所需源码，不包含密钥、运行数据、渲染目录或 node_modules。
UI 在镜像构建内根据 lockfile 安装并构建。成功推送后检查 Registry 内容摘要，写入
`scientific_agent/rendered/api-image.txt` 和 `ui-image.txt`；失败不留下可部署的摘要记录。

通用 Agent 的控制镜像和 Pi Worker 也需要包含新阶段代码。先按
[Agent 部署说明](../agent_service/deploy/README.md) 构建、完成各新合同的独立真实验收，
再更新实际生产目录中的合同、科研调用方权限和已验收绑定。目录生成器的新增绑定默认
关闭；旧验收记录不能充当新镜像或新合同的验收。

Agent 控制目录由多个业务共用。发布科研绑定时，应基于最新线上完整配置，只合并
本次已验收的科研组合，并核对资源版本及历史账本；保留其他业务新增的路由、验收、
pool、host 和公共参数。构建过 control 镜像不代表需要替换同时上线的更新版本。
不要用历史 `rendered/` 快照整份覆盖共享目录。本轮并发 PPT 发布的保留过程见验证记录。

业务部署脚本默认只生成补丁供检查：

```bash
bash scientific_agent/deploy.sh --kubeconfig /etc/kubernetes/super-admin.conf
bash scientific_agent/deploy.sh --kubeconfig /etc/kubernetes/super-admin.conf --apply
```

应用时先保存 API/UI 的完整回退规格，再更新 API。脚本使用 API 内已有的调用凭据
查询 Agent 能力，只有四个新阶段及修订均真实 ready 才替换公开 UI。未就绪会停下并保留
旧页面。部署不删除旧 Worker、PVC、代码 ConfigMap 或报告，不负责启用尚未验收的引擎。
不要使用旧 `scripts/deploy-ui.sh scientific_agent` 覆盖新 UI，也不要用缺省 Pi 部署脚本
把业务执行后端改回独立 Worker。

模型代理先完整保存单次 Responses 响应，再将同一响应交给任务执行器。代理的网络读
等待上限为 600 秒；持续收到数据时，完整缓冲可能更久。使用该代理的 Pi 驱动按合同的
阶段总时限设置无输出容差，且不低于 630 秒；科研阶段仍先受 1800 秒绝对截止时间和
取消检查约束。直接连接模型的原生驱动仍默认 120 秒。较长的单次模型等待不会延长整个
阶段的时限，不要只调业务 HTTP 超时来处理此类问题。

首次真实业务验收曾在检索完成三个学术调用后，等待下一次模型响应时触发
`runtime_idle_timeout`：代理允许的等待时间大于执行器原来的 120 秒无输出时限。
修复需要重新构建执行镜像，并为四个新阶段取得新镜像的独立验收；不能沿用旧镜像的
验收记录。已失败任务和不确定模型请求账本保留，用户明确重试会创建新的有限任务，
不会再次提交原来那个不确定模型请求。

后续大纲验收还发现：权威 context 超过 50 KiB 且整份只有一行时，Pi 的按行 `read`
无法完整读取；只减少勾选文献不会移除冻结历史批次。执行层现保留原 context 与 digest，
额外生成有界、分块的只读 `context-view/`，无需给 Agent 增加 shell 或改用未确认来源。
该修复需要新执行镜像和节点 AppArmor 的三条固定视图挂载规则；只更新 API/UI 不足以
修复它。另已精确处理“已获取摘要，无全文”的全文披露误判，真正无依据的全文获取断言
仍然拒绝，不能通过关掉引用或访问校验来恢复任务。

r6 的原 HTTP 草稿重试实际以 `model_error` 结束：最后一次 Responses 流返回
`upstream_error`，即使其 HTTP 状态是 200，也不能视为模型请求成功。此前模型写出的
候选已单独保存；对该候选进行离线校验时，另外发现“不能据链接声称已阅读全文”被
误判为已阅读全文。源码现精确识别这类相邻、位于子句末尾的禁止声称表达，仍拒绝
反转否定、后续独立的获取断言等情形。这项修正不改变原失败状态；离线候选复验与
新的真实任务验收分别留证，不把候选通过记成旧任务成功。

后续 r7 草稿在同一模型配置下也遇到上游明确失败事件。模型请求账本中的 `complete`
仅表示 HTTP 响应字节完整保存，可供重放；判断模型成功还必须检查 Responses 的
终结事件。HTTP 200 内的 `error / upstream_error` 仍是失败，不能跳过此检查、采用
尚未完成的草稿或自动重发不确定请求。排障保留响应与阶段身份；恢复使用原确认的
输入、独立的显式重试意图和新任务，不删除此前失败。现有通用日志不足以确定供应商
具体失败原因时，应明确记录这一限制，而不是推断 Vault 或 API Key 有问题。

共享凭据更新时还需注意 Kubernetes Secret 的 `subPath` 挂载：ExternalSecret 刷新
后，旧 Pod 可能继续持有可读但链接数为 0 的旧 inode。运行中的进程可能仍使用已加载
配置，但重新读取配置的严格加载器会以 `secrets_file_unavailable` 拒绝该文件；这不
等同于 Vault 中缺少凭据，也不能单凭此现象认定模型流错误的原因。排障只记录挂载
元数据和必要的比较结果，不输出 Secret、令牌或回执签名密钥。

处理这类旧挂载时，先确认任务排空，并将 API Pod 内的最新恢复 checkpoint、执行
账本和回退规格保存在 Pod 外；核对当前 Secret，保留既有调用令牌和历史回执验签
密钥。正常的新镜像 control rollout 会创建新 Pod 并刷新挂载，随后复核文件链接数、
配置加载、部署就绪和账本保留情况。不要放宽配置加载校验、重置数据库或清除失败
历史；仅重新应用未改变的 Deployment 模板未必会重建 Pod。

这些故障、精确镜像和分层验收状态见
[工作流验证记录](../agent_service/docs/SCIENTIFIC_WORKFLOW_CANDIDATES_20261029.md)。
原生候选通过只能支持其实际测试范围；正式 Jobs HTTP、业务接收与浏览器全流程需
各自完成。失败记录继续保留，用户明确重试会产生新的有限任务，不覆盖原失败或模糊提交。

需要回退时，用本次 `rendered/deploy-*/api-rollback.json` 和 `ui-rollback.json`
执行 `kubectl patch --type=json --patch-file …`。新入口网络策略可保留，它允许同一个
既有 SSO 代理访问恢复后的旧页面；回退不删除新项目或旧报告。

## 首版边界

每项目最多 30 份资料，每文件 5 MiB，提取正文最多 512 KiB；PDF 最多 100 页，解析
在独立进程中限时 12 秒，Linux 还限制内存和 CPU。PDF 必须有可提取文本；扫描件、
加密文件和解析失败项明确显示失败，可从本次研究剔除后继续。DOI 登记只保存标识符，
不等同于已抓取元数据或全文。

每用户最多 50 项目，每项目最多 100 次执行；项目 JSON 总量上限 24 MiB，达到上限会
原子拒绝新操作，不悄悄清除历史。检索来源、结果数、调用次数由冻结任务策略限制。
引用访问范围区分元数据、摘要和用户提供的全文，不宣称经过科学真实性审定。

支持 Markdown 和来源记录下载。定稿为项目内的私有版本里程碑，不表示公开发布、
投稿或邮件发送。DOCX/PDF 导出、OCR、任意网页抓取和完整系统综述流程尚未提供。

设计见 [RESEARCH_WORKBENCH_DESIGN.md](RESEARCH_WORKBENCH_DESIGN.md)，运行层合同见
[SCIENTIFIC_WORKFLOW_CONTRACTS.md](../agent_service/docs/SCIENTIFIC_WORKFLOW_CONTRACTS.md)。
