# 科研项目工作台

新流程围绕持久研究项目推进：研究目标 → 研究方案 → 文献与证据 → 报告初稿 →
审阅修订 → 定稿导出。保存要求不调用模型。确认研究方案后才能检索，检索后自动进行
一次证据整理并停下；用户确认证据与大纲后才能写初稿。各次执行结束后释放任务 Pod，
等人阅读和确认不占 Worker。全局任务并发仍由 Agent 服务统一限制。

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
export IMAGE_TAG="workflow-$(git rev-parse --short=12 HEAD)"
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
