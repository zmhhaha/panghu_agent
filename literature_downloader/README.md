# Literature Downloader

> 当前版本：开始检索只执行多轮检索并生成检索报告、DOI 列表和待下载清单；PDF 下载必须在独立“下载文献”标签中通过任务 ID 手动触发。

独立的三阶段文献工具。用户提交一次任务后，后台先完成多轮检索、相关性筛选和报告生成；PDF 收集与校验由用户在下载标签中通过任务 ID 单独触发：

1. 检索本地库、OpenAlex、Crossref、arXiv 和 Semantic Scholar。
2. 按 `arXiv -> 元数据公开 PDF -> Unpaywall/OpenAlex OA -> DOI 落地页 PDF 发现 -> Semantic Scholar/PMC -> 元数据 URL` 下载 PDF，支持多轮重试。
3. 校验 PDF 文件签名、大小和文本可读性，并生成 EvidenceGate-new 风格 Markdown 报告。

检索阶段会保存独立的 `search_report.md`、`doi_list.md` 和 `need_to_download.md`，其中列出每篇文献的来源数据库、DOI/arXiv ID、元数据 URL 和公开 PDF URL。收集阶段的单次报告会记录实际尝试过的下载 URL、下载策略、成功/失败原因和本地保存路径；校验阶段会记录来源链、文件检查结果和文本可读性。最终下载报告会汇总这三阶段内容。服务器本地路径只表示下载产物的保存位置，不是文献来源。

检索阶段包含一个可选的文献检索专家 Agent：它使用统一 LLM 配置生成中英文术语、查询变体以及纳入/排除标准，再由程序调用学术 API 并对候选文献进行批量相关性重排。LLM 不能生成或修改 DOI、作者、元数据 URL 和 PDF URL；这些字段始终以 API 返回结果为准。LLM 未配置、调用失败或返回格式不合法时，自动回退到规则检索和规则相关性门槛。仅因为命中“研究进展”等泛化词、但没有命中主题术语的本地或外部文献不会进入待下载清单，避免不同任务之间出现主题串扰。

UI 只需要填写研究主题、检索轮数和通知邮箱，然后点击“开始检索”。检索完成或失败时会向通知邮箱发送一次结果邮件；检索报告和 DOI 列表可立即下载。需要 PDF 时，将任务 ID 复制到“下载文献”标签并点击“开始下载”，完成后可下载最终报告和已校验 PDF。历史任务页仅用于查询任务状态和任务 ID。

## 安装

```bash
pip install -r literature_downloader/requirements.txt
```

`requests`、`fastapi`、`uvicorn` 和 `pypdf` 是 API/核心功能依赖；Gradio 仅在启动 UI 时需要。

## 启动 API

从 `panghu_agent` 目录运行：

```bash
python -m literature_downloader.main
```

默认地址为 `http://127.0.0.1:8001`。也可以使用：

```bash
uvicorn literature_downloader.api:app --host 0.0.0.0 --port 8001
```

## 启动 UI

先启动 API，再运行：

```bash
python app/ui/literature_downloader.py
```

默认 UI 地址为 `http://127.0.0.1:7860`。API 地址可通过 `API_BASE` 或 `LITERATURE_API_BASE` 修改。

## Kubernetes 部署

在 ARM64 集群管理节点的 `panghu_agent` 目录执行：

```bash
(cd scihub_cli && bash build.sh)
bash literature_downloader/deploy.sh
```

脚本只构建并推送 Literature Downloader 专属 API 镜像；UI 使用 panghu_agent 根目录的通用 `agent-ui` 镜像和通用 Gradio Deployment。脚本会创建 `literature-downloader` 命名空间、5 Gi Ceph RBD 数据 PVC 和 5 Gi CephFS `scihub-papers-pvc`，并授予 API ServiceAccount 在本 namespace 创建/等待 SciHub Job 的权限。UI 通过集群内 Service 和 OAuth2 Proxy 暴露，不再使用专属 UI 镜像或 NodePort。

部署前请确保共享 API 基础镜像和通用 UI 镜像已经构建并推送：

```bash
docker build -f Dockerfile.api.base -t arm-cluster-master:5000/agent-api-base:latest .
docker push arm-cluster-master:5000/agent-api-base:latest
docker build -f Dockerfile.ui -t arm-cluster-master:5000/agent-ui:latest .
docker push arm-cluster-master:5000/agent-ui:latest
```

### 集群外围配置

Literature Downloader 使用现有的 OAuth2 Proxy 和 Cloudflare Tunnel 配置方式。学术联系邮箱是非敏感配置，已直接写入 API Deployment。LLM 密钥是可选的，只有启用检索专家时才需要 Literature Downloader 的 Vault ExternalSecret。部署 API/UI 后再配置 OAuth2 Proxy 和 TunnelRoute：

```bash
bash ../oauth/k8s/deploy-agent-proxy.sh literature-downloader
kubectl apply -f ../cloudflare-tunnel/operator/tunnel-routes.yaml
```

默认访问地址为 `https://literature-downloader.panghuer.top`。OAuth2 Proxy 将请求转发到 `ui.literature-downloader.svc.cluster.local:7860`，并继续使用集群现有的 Casdoor/OIDC Secret。

`literature_downloader/k8s/configmap.yaml` 包含 `PROVIDER=deepseek`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 和检索 Agent 参数，`deploy.sh` 会自动应用它。若需要启用 LLM，先在 Vault 写入 `secret/literature-downloader/api` 的 `DEEPSEEK_API_KEY`，再应用 `vault/inventory/literature-downloader-externalsecret.yaml` 并重启 API；没有该密钥时会自动使用规则回退。

## 主要 API

- `POST /literature-download`：创建检索任务（提交 `email` 后，检索完成或失败会发送通知；请求使用 `search_rounds`）。
- `POST /literature-download/{task_id}/download`：触发 PDF 收集；请求体必须提交本次下载阶段的通知邮箱，例如 `{"email":"user@example.com"}`。下载完成或失败会向该邮箱发送通知。
- `GET /literature-download/{task_id}`：查询状态和文献列表。
- `GET /literature-reports?q=...`：按关键词、任务 ID 或状态查询历史任务。
- `GET /literature-download/{task_id}/report/download`：下载 Markdown 报告。
- `GET /literature-download/{task_id}/doi-list/download`：下载检索阶段生成的 DOI/标识列表。
- `GET /literature-download/{task_id}/files/download`：下载通过校验的 PDF ZIP。
- `GET /literature-download/{task_id}/reports`：列出检索、收集、校验和最终报告。
- `GET /literature-download/{task_id}/reports/{report_id}/download`：下载指定阶段报告。

### SciHub Job 下载后端

生产 Kubernetes Deployment 默认设置 `LITERATURE_DOWNLOAD_BACKEND=hybrid`。每个收集轮次先使用原有的 OpenAlex、Crossref、Europe PMC、arXiv、开放获取页面等直接下载流程；只有直接下载失败或校验不通过的文献才会进入 SciHub Job。Job 会为 SciHub CLI 配置 `panghuer001@163.com`，启用 CORE，并保存失败页面的 HTML 快照；这些配置用于提高 Unpaywall/CORE 命中率和诊断 403/405 响应。Job 输入 DOI、arXiv ID 或文献 URL 通过临时 ConfigMap 注入，Job 将 PDF 写入共享 CephFS PVC，API 等待 Job 结束后从 `/data/scihub-papers` 读取并校验结果。单篇文献下载失败但已生成 `download-report.json` 时不再使整个 Job 失败；只有容器、命令或输出基础设施错误才会使 Job 失败。Job 输出目录为 `/data/scihub-papers/jobs/<task_id>/round-<n>/paper-<paper_id>/`。只有校验通过的 PDF 才由 API 写入 SQLite 的全局 `library_papers` 文献库；后续任务会先复用仍存在的全局 PDF，不会再次创建 SciHub Job。Job 本身不挂载或写 SQLite。

本地开发或没有 Kubernetes ServiceAccount 时可设置 `LITERATURE_DOWNLOAD_BACKEND=direct`，仅使用 API 容器内的直接下载逻辑；设置为 `scihub-job` 可强制所有待处理文献走 SciHub Job。可通过 `SCIHUB_JOB_IMAGE`、`SCIHUB_JOB_TIMEOUT`、`SCIHUB_RETRIES` 和 `SCIHUB_JOB_POLL_INTERVAL` 调整 Job 参数。

## 数据目录

默认写入 `literature_downloader/data/`：

- `literature.db`：任务、任务文献、跨任务全局 `library_papers` 文献库、下载尝试和报告索引。
- `pdfs/<task_id>/`：下载的 PDF 文件。
- `reports/<task_id>/`：检索报告、DOI 列表、待下载清单、单次收集/校验报告和包含三阶段明细的最终报告。

可通过 `LITERATURE_DATA_DIR`、`LITERATURE_DB_PATH`、`LITERATURE_PDF_DIR` 和 `LITERATURE_REPORTS_DIR` 修改路径。

## 配置

- `LITERATURE_SEARCH_ROUNDS`：默认检索轮数 3。
- `LITERATURE_SEARCH_LIMIT`：默认 `0`，表示不对去重后的相关结果设置服务层数量上限；设置为正数可作为运维资源保护值。服务会优先尝试带公开 PDF 地址、arXiv 或开放获取标记的记录。
- `LITERATURE_PER_PROVIDER`：每个查询变体和外部来源默认最多 20 篇。
- `LITERATURE_MAX_SEARCH_VARIANTS`：每个主题最多使用 6 个查询变体，提高召回率。
- `LITERATURE_DOWNLOAD_CONCURRENCY`：每轮并发下载和校验数，默认 6；可按服务器带宽和 CPU 调整。
- `LITERATURE_DOWNLOAD_RETRIES`：同一个 URL 遇到超时、HTTP 429 或 5xx 临时错误时的额外重试次数，默认 2；HTTP 403/405 不会无效重试。
- `LITERATURE_DOWNLOAD_RETRY_BACKOFF_MS`：临时错误指数退避的初始毫秒数，默认 500。
- `LITERATURE_DOWNLOAD_REQUEST_INTERVAL_MS`：同一域名两次请求之间的最小间隔，默认 250 毫秒，用于降低触发限流的概率。
- 下载器会解析学术落地页中的 `citation_pdf_url`、`application/pdf` link 和明确的 `.pdf` 链接，并继续校验 PDF 文件签名；HTML 登录页或反爬页面不会被误收为 PDF。
- `scope_requirements`：由每次任务的检索专家 LLM 生成的主题范围组；服务不内置材料、工艺或其他领域规则。LLM 不可用时仅使用通用查询规范化和词法相关性，并在报告中标记严格范围过滤未启用。
- `ACADEMIC_CONTACT_EMAIL`：用于 OpenAlex/Crossref 的联系邮箱，当前固定为 `panghuer001@163.com`，不通过 Vault 管理。
- `OPENALEX_API_KEY`：可选的 OpenAlex API Key；配置后可使用更高的 OpenAlex 配额，密钥本身不要写入 ConfigMap。
- 任务通知邮箱：由提交任务时的 `email` 字段提供，仅在任务完成或失败时发送结果通知；不是 Vault 配置项。
- Semantic Scholar API Key：未配置。系统仍会尝试公开接口，若受限流影响，会在检索报告中记录错误并继续处理其他来源。
- 未配置 Semantic Scholar API Key 时，下载阶段只对检索结果中已有 Semantic Scholar paper ID 的文献查询 OA PDF，不再对每个 DOI 逐篇发匿名请求，避免大任务因 429 限流显著变慢。

## 是否需要 LLM

LLM 检索专家是可选增强能力，不改变下载和校验的确定性流程。配置 `PROVIDER=deepseek`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_API_KEY` 后，系统会启用检索计划和相关性重排；也支持 `openai` 或 `custom` 的 OpenAI-compatible 接口。`DEEPSEEK_API_KEY` 由现有 `agent-secret`/Vault 机制注入，`agent-config` 只保存非敏感配置。未配置密钥时仍可正常检索、下载和生成报告，只是使用规则回退。

额外配置：

- `LITERATURE_LLM_ENABLED`：是否启用 LLM 增强，默认 `true`。
- `LITERATURE_LLM_TIMEOUT`：单次 LLM 请求超时秒数，默认 30。
- `LITERATURE_LLM_MAX_CANDIDATES`：单批最多提交给相关性重排的候选数，默认 40。
### Task-level LLM scope rules

The service does not contain a built-in material, process, disease, or other
domain rule. When the retrieval expert LLM is available, it must return
`scope_requirements`: named groups of literal terms and a `required` flag.
The searcher applies those groups to title, abstract, and venue for the current
task only. The same plan is sent to the relevance ranker and is preserved in
the search report.

When the LLM is unavailable or returns an invalid plan, the service uses only
domain-neutral query normalization and lexical relevance scoring. Strict scope
filtering is disabled and the report states that precision is reduced; no
fallback InP or other subject-specific rule is applied.

The LLM plan is not sent verbatim to every database. The searcher translates
each semantic variant per provider: OpenAlex, Crossref, and Semantic Scholar
receive plain-text anchor terms, while arXiv receives `all:"..."` clauses.
Unsupported Boolean operators and wildcards are removed before the request.
When a provider returns a rate-limit response, the task pauses that provider
for one round before retrying it, instead of issuing another burst of doomed
requests. The search report records the actual provider queries alongside the
original LLM variants.

Academic API requests are throttled per host and retry transient `429/5xx`
responses with exponential backoff. `Retry-After` is honored up to
`ACADEMIC_API_RATE_LIMIT_MAX_WAIT_SECONDS` (default 60). After a provider is
rate-limited, the search task pauses that provider for one round and then tries
again, while continuing with other providers. Configure
`ACADEMIC_API_REQUEST_INTERVAL_MS` to increase the gap between requests and
`ACADEMIC_API_RETRY_BACKOFF_SECONDS` to change the initial exponential delay.
API keys for providers that offer them, especially `SEMANTIC_SCHOLAR_API_KEY`
and `OPENALEX_API_KEY`, remain the most reliable way to raise quotas. Keys are
optional; the service continues with anonymous/polite access when absent.

## Separated search and download workflow

`POST /literature-download` now performs search only. The request accepts
`search_rounds` (1-10).
Each round requests the next provider page, then results are deduplicated across
rounds. The first round may use the retrieval-expert LLM; later rounds reuse its
plan and use deterministic ranking to control cost.

Search completion is persisted as `ready:download` and produces:

- `search_report.md` with local hits, provider records, source URLs, DOI/arXiv IDs, query rounds, and LLM status;
- `doi_list.md` with a compact DOI/identifier table;
- `need_to_download.md` with metadata and legal/open download routes.

PDF collection is optional and starts with `POST /literature-download/{task_id}/download`.
The request body is required and must contain the notification address for this
download stage, for example `{"email":"user@example.com"}`. This address is
persisted on the task before the worker starts, so download completion or
failure notifications use the address entered on the “下载文献” tab.
It runs one collection and verification pass, then creates the EvidenceGate-style
download report and verified PDF ZIP. The UI exposes this as a separate “下载文献”
tab where the user enters the search task ID.
