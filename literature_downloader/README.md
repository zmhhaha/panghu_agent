# Literature Downloader

独立的三阶段文献下载工具。用户提交一次任务后，后台会自动按顺序完成检索、PDF 收集（按最大重试轮数重试）、PDF 校验和最终报告生成：

1. 检索本地库、OpenAlex、Crossref、arXiv 和 Semantic Scholar。
2. 按 `arXiv -> DOI/Unpaywall -> Semantic Scholar -> 元数据 PDF URL` 下载 PDF，支持多轮重试。
3. 校验 PDF 文件签名、大小和文本可读性，并生成 EvidenceGate-new 风格 Markdown 报告。

检索阶段会保存独立的 `search_report.md` 和 `need_to_download.md`，其中列出每篇文献的来源数据库、DOI/arXiv ID、元数据 URL 和公开 PDF URL。收集阶段的每轮报告会记录实际尝试过的下载 URL、下载策略、成功/失败原因和本地保存路径；校验阶段会记录来源链、文件检查结果和文本可读性。最终下载报告会汇总这三阶段内容。服务器本地路径只表示下载产物的保存位置，不是文献来源。

检索阶段包含一个可选的文献检索专家 Agent：它使用统一 LLM 配置生成中英文术语、查询变体以及纳入/排除标准，再由程序调用学术 API 并对候选文献进行批量相关性重排。LLM 不能生成或修改 DOI、作者、元数据 URL 和 PDF URL；这些字段始终以 API 返回结果为准。LLM 未配置、调用失败或返回格式不合法时，自动回退到规则检索和规则相关性门槛。仅因为命中“研究进展”等泛化词、但没有命中主题术语的本地或外部文献不会进入待下载清单，避免不同任务之间出现主题串扰。

UI 只需要填写研究主题、最大重试轮数和通知邮箱，然后点击“开始检索”。任务运行期间可以点击“刷新状态”主动查询进度；完成或失败时会向通知邮箱发送一次结果邮件。完成后，当前任务区域会显示最终报告和已校验 PDF 的下载按钮。历史报告页仅用于按主题或任务 ID 查询历史任务的状态；需要恢复历史任务时，将查询到的任务 ID 填回“新任务”页并点击“刷新状态”，不再提供单独的加载任务或历史文件下载按钮。

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
bash literature_downloader/deploy.sh
```

脚本只构建并推送 Literature Downloader 专属 API 镜像；UI 使用 panghu_agent 根目录的通用 `agent-ui` 镜像和通用 Gradio Deployment。脚本会创建 `literature-downloader` 命名空间和 5 Gi Ceph RBD PVC，并等待两个 Deployment 就绪。UI 通过集群内 Service 和 OAuth2 Proxy 暴露，不再使用专属 UI 镜像或 NodePort。

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

- `POST /literature-download`：创建检索任务（提交 `email` 后，任务完成或失败会发送通知；检索、下载、校验和重试由后台自动执行）。
- `GET /literature-download/{task_id}`：查询状态和文献列表。
- `GET /literature-reports?q=...`：按关键词、任务 ID 或状态查询历史任务。
- `POST /literature-download/{task_id}/approve`、`/retry`、`/finish`：旧版本任务的兼容接口；新任务不需要调用这些接口。
- `GET /literature-download/{task_id}/report/download`：下载 Markdown 报告。
- `GET /literature-download/{task_id}/files/download`：下载通过校验的 PDF ZIP。
- `GET /literature-download/{task_id}/reports`：列出检索、收集、校验和最终报告。
- `GET /literature-download/{task_id}/reports/{report_id}/download`：下载指定阶段报告。

## 数据目录

默认写入 `literature_downloader/data/`：

- `literature.db`：任务、文献、下载尝试和报告索引。
- `pdfs/<task_id>/`：下载的 PDF 文件。
- `reports/<task_id>/`：检索、待下载清单、每轮收集/校验和包含三阶段明细的最终报告。

可通过 `LITERATURE_DATA_DIR`、`LITERATURE_DB_PATH`、`LITERATURE_PDF_DIR` 和 `LITERATURE_REPORTS_DIR` 修改路径。

## 配置

- `LITERATURE_MAX_ROUNDS`：默认最大重试轮数 3。
- `LITERATURE_SEARCH_LIMIT`：默认返回最多 100 篇候选文献；服务会优先尝试带公开 PDF 地址、arXiv 或开放获取标记的记录。
- `LITERATURE_PER_PROVIDER`：每个查询变体和外部来源默认最多 20 篇。
- `LITERATURE_MAX_SEARCH_VARIANTS`：每个主题最多使用 6 个查询变体，提高召回率。
- `LITERATURE_DOWNLOAD_CONCURRENCY`：每轮并发下载和校验数，默认 6；可按服务器带宽和 CPU 调整。下载优先级为 arXiv、检索结果公开 PDF URL、DOI/Unpaywall、Semantic Scholar OA 和最后的详情页 URL。
- `ACADEMIC_CONTACT_EMAIL`：用于 OpenAlex/Crossref 的联系邮箱，当前固定为 `panghuer001@163.com`，不通过 Vault 管理。
- 任务通知邮箱：由提交任务时的 `email` 字段提供，仅在任务完成或失败时发送结果通知；不是 Vault 配置项。
- Semantic Scholar API Key：未配置。系统仍会尝试公开接口，若受限流影响，会在检索报告中记录错误并继续处理其他来源。

## 是否需要 LLM

LLM 检索专家是可选增强能力，不改变下载和校验的确定性流程。配置 `PROVIDER=deepseek`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_API_KEY` 后，系统会启用检索计划和相关性重排；也支持 `openai` 或 `custom` 的 OpenAI-compatible 接口。`DEEPSEEK_API_KEY` 由现有 `agent-secret`/Vault 机制注入，`agent-config` 只保存非敏感配置。未配置密钥时仍可正常检索、下载和生成报告，只是使用规则回退。

额外配置：

- `LITERATURE_LLM_ENABLED`：是否启用 LLM 增强，默认 `true`。
- `LITERATURE_LLM_TIMEOUT`：单次 LLM 请求超时秒数，默认 30。
- `LITERATURE_LLM_MAX_CANDIDATES`：单批最多提交给相关性重排的候选数，默认 40。
