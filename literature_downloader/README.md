# Literature Downloader

独立的三阶段文献下载工具：

1. 检索本地库、OpenAlex、Crossref、arXiv 和 Semantic Scholar。
2. 按 `arXiv -> DOI/Unpaywall -> Semantic Scholar -> 元数据 PDF URL` 下载 PDF，支持多轮重试。
3. 校验 PDF 文件签名、大小和文本可读性，并生成 EvidenceGate-new 风格 Markdown 报告。

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

Literature Downloader 沿用 `scientific-agent` 的 Vault、OAuth2 Proxy 和 Cloudflare Tunnel 配置方式。先在 Vault 写入 API 凭证，再依次应用 ExternalSecret、OAuth2 Proxy 和 TunnelRoute：

```bash
kubectl apply -f ../vault/inventory/literature-downloader-externalsecret.yaml
bash ../oauth/k8s/deploy-agent-proxy.sh literature-downloader
kubectl apply -f ../cloudflare-tunnel/operator/tunnel-routes.yaml
```

默认访问地址为 `https://literature-downloader.panghuer.top`。OAuth2 Proxy 将请求转发到 `ui.literature-downloader.svc.cluster.local:7860`，并继续使用集群现有的 Casdoor/OIDC Secret。

## 主要 API

- `POST /literature-download`：创建检索任务。
- `GET /literature-download/{task_id}`：查询状态和文献列表。
- `POST /literature-download/{task_id}/approve`：确认检索清单并开始下载。
- `POST /literature-download/{task_id}/retry`：重试上一轮失败文献。
- `POST /literature-download/{task_id}/finish`：结束收集并生成最终报告。
- `GET /literature-download/{task_id}/report/download`：下载 Markdown 报告。
- `GET /literature-download/{task_id}/files/download`：下载通过校验的 PDF ZIP。

## 数据目录

默认写入 `literature_downloader/data/`：

- `literature.db`：任务、文献、下载尝试和报告索引。
- `pdfs/<task_id>/`：下载的 PDF 文件。
- `reports/<task_id>/`：检索、每轮收集/校验和最终报告。

可通过 `LITERATURE_DATA_DIR`、`LITERATURE_DB_PATH`、`LITERATURE_PDF_DIR` 和 `LITERATURE_REPORTS_DIR` 修改路径。

## 配置

- `LITERATURE_MAX_ROUNDS`：默认最大重试轮数 3。
- `LITERATURE_SEARCH_LIMIT`：默认返回最多 30 篇。
- `LITERATURE_PER_PROVIDER`：每个外部来源默认最多 10 篇。
- `ACADEMIC_CONTACT_EMAIL`：用于 OpenAlex/Crossref/Unpaywall 的联系邮箱。
- `SEMANTIC_SCHOLAR_API_KEY`：可选的 Semantic Scholar API Key。
