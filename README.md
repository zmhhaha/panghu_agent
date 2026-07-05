# Panghu Agent

Agent 开发平台 — 多 Agent 协作研究助手，支持 CLI 本地使用和 API 在线服务。

```
panghu_agent/
├── crewai/                 # Agent 引擎（核心功能）
├── api/                    # 付费 API 服务（独立模块）
├── k8s/                    # Kubernetes 部署清单
├── scripts/                # 构建 & 部署脚本
├── Dockerfile              # 多架构 Docker 镜像
├── docker-compose.yaml     # 本地编排
└── README.md
```

## 快速开始

### Agent 引擎（本地 CLI）

```bash
cd crewai
pip install -r requirements.txt
cp .env.example .env   # 编辑填写 API Key
python main.py "你的调研主题"
```

### 付费 API 服务（在线）

```bash
# 1. 安装依赖
cd crewai && pip install -r requirements.txt && cd ..
cd api && pip install -r requirements.txt && cd ..

# 2. 配置
cp api/.env.example api/.env

# 3. 启动
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
```

### Docker（推荐）

```bash
# 本地构建 + 运行
docker build -t panghu-agent:latest .
docker run -d -p 8000:8000 \
  -e PROVIDER=openai \
  -e OPENAI_API_KEY=sk-xxx \
  -e ADMIN_API_KEY=your-admin-secret \
  -v $(pwd)/data:/app/data \
  panghu-agent:latest

# 或用 docker-compose
docker compose up -d

# ARM64 构建
./scripts/build.sh --arm-only

# 多架构构建 + 推送
REGISTRY=harbor.your.com/project/ ./scripts/build.sh --push
```

### Kubernetes（ARM 服务器）

```bash
# 1. 编辑密钥
vim k8s/secret.yaml   # 填入真实 API Key

# 2. 编辑 Ingress 域名
vim k8s/ingress.yaml  # 替换 agent.your-domain.com

# 3. 一键部署
kubectl apply -k k8s/

# 4. 查看状态
kubectl -n panghu-agent get pods,svc,ingress

# 5. 查看 admin API Key（首次启动自动生成）
kubectl -n panghu-agent logs deploy/panghu-agent | head -10
```

启动后访问 http://localhost:8000/docs 查看 API 文档。

## API 端点概览

### 用户端（需要 API Key）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/me` | 当前用户信息 + 累计消费 |
| GET | `/v1/usage` | 用量记录（分页） |
| POST | `/v1/research` | 执行调研，按 Token 后付费 |

### 管理端（需要 Admin Key）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/admin/users` | 创建用户，返回 API Key |
| GET | `/admin/users` | 用户列表 |
| GET | `/admin/users/{id}` | 用户详情 + 统计 |
| POST | `/admin/users/{id}/keys` | 生成新 API Key |
| POST | `/admin/users/{id}/keys/{kid}/revoke` | 吊销 API Key |
| GET | `/admin/pricing` | 定价配置 |
| POST | `/admin/pricing` | 新增/更新定价 |

### 计费模式

- **后付费**：按 Token 累计，不预扣余额
- **定价管理**：通过 `/admin/pricing` 配置，支持按模型模糊匹配
- **默认定价**：首次启动自动初始化（比官方价上浮 50%-100%）

## curl 示例

```bash
# 创建用户
curl -X POST http://localhost:8000/admin/users \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: your-admin-key" \
  -d '{"username": "test", "email": "test@example.com"}'

# 执行调研
curl -X POST http://localhost:8000/v1/research \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk-your-api-key" \
  -d '{"topic": "量子计算在金融领域的应用前景"}'
```
