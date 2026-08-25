# Content LLM Service

统一的 CrewAI LLM 服务，供 `content_agents` 及其他内容生产服务调用。

## API

`POST /v1/meme/judge` 接收 `title`、`summary`、`url`，由 CrewAI Agent 使用网页搜索/抓取 tools 判断是否为可独立传播的短句梗，并返回结构化 JSON。

## 配置

支持现有 `tools.llm_config` 的 `openai`、`deepseek`、`anthropic`、`custom` Provider。API 密钥只放在 `content-llm-secret`，不要写入 Git。

## 部署

```bash
cd panghu_agent/content-llm-service
bash build.sh
kubectl apply -f k8s.yaml
```

Service 地址：`http://content-llm-service.content-agents.svc.cluster.local`

## Provider configuration

The service follows the same convention as `research-agent` and
`scientific-agent`: `PROVIDER` and model endpoint settings are stored in the
ConfigMap, while only provider credentials are stored in Vault. The default
deployment uses DeepSeek:

```yaml
PROVIDER: deepseek
DEEPSEEK_BASE_URL: https://api.deepseek.com
DEEPSEEK_MODEL: deepseek-chat
```

Write only the credential to Vault:

```bash
kubectl -n vault exec vault-0 -- vault kv put secret/content-agents/llm \
  DEEPSEEK_API_KEY='...'
```

密钥配置在 Vault `secret/content-agents/llm`，由
`vault/inventory/content-llm-externalsecret.yaml` 同步为
`content-llm-secret`，不要写入 Git。推荐使用 `bash deploy.sh` 部署。

```bash
kubectl -n vault exec vault-0 -- vault kv put secret/content-agents/llm \
  DEEPSEEK_API_KEY='...'
```
