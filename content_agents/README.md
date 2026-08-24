# Panghu Content Agents

This directory contains three platform-independent content bots:

- `github_trending_agent`: collect active and popular open-source repositories.
- `international_news_agent`: collect international-news leads from RSS/Atom feeds.
- `meme_collector_agent`: collect trending phrases and their public source context.

The bots produce a common `ContentItem`. Hublog is only an optional channel adapter; JSON and RSS output can run without Hublog.

## Offline smoke test

Run from the `panghu_agent` directory. `--sample` does not use the network or any token:

```bash
export BOT_DRAFT_ONLY=true
export AGENT_CHANNELS=json,rss
export CONTENT_DATA_DIR=/tmp/panghu-content-test

python -m content_agents.github_trending_agent.main --sample
python -m content_agents.international_news_agent.main --sample
python -m content_agents.meme_collector_agent.main --sample
```

Each bot writes JSONL records under `<CONTENT_DATA_DIR>/<bot-name>`:

```text
content-items.jsonl          # normalized content and source references
published-content.jsonl      # JSON channel output
channel-publications.jsonl   # per-channel status and external IDs
bot-runs.jsonl               # run statistics
```

The RSS adapter writes `feed.xml` and `rss-items.json` at the data directory root.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `CONTENT_DATA_DIR` | `/data/content-agents` | Persistent JSONL/RSS directory |
| `AGENT_CHANNELS` | `json` | Comma-separated: `json`, `rss`, `hublog` |
| `BOT_DRAFT_ONLY` | `true` | Keep public channels in draft mode |
| `BOT_MAX_ITEMS` | `5` | Maximum candidates per run |
| `BOT_LOOKBACK_HOURS` | `24` | GitHub query window |
| `HUBLOG_BASE_URL` | cluster-internal Hublog service | Hublog API base URL |
| `HUBLOG_SERVICE_TOKENS` | empty | Raw-token JSON envelope; each bot selects its own entry |
| `HUBLOG_SERVICE_TOKEN` | empty | Compatibility fallback for local runs |
| `GITHUB_TOKEN` | empty | Optional GitHub API token |
| `NEWS_FEEDS` | BBC World and Al Jazeera | `name|url||name|url` |
| `MEME_FEEDS` | Google Trends China | `name|url||name|url` |
| `LLM_BASE_URL` | empty | Optional OpenAI-compatible endpoint |
| `LLM_API_KEY` | empty | LLM credential, from a Secret only |
| `LLM_REQUIRED` | `false` | Fail the run if the optional LLM is not configured |

Publication policy:

- JSON always keeps the generated item as a review ledger.
- `blocked` items are never sent to a channel.
- With `BOT_DRAFT_ONLY=true`, RSS and Hublog are recorded as `draft` and are not public.
- With draft mode disabled, only `approved` items go to RSS or Hublog. News and meme items are `needs_review` by default.

## Hublog tokens

Never commit raw tokens. Hublog stores only SHA-256 hashes. The content-agent namespace receives a separate raw-token JSON envelope under the same key name, and each bot selects its own entry by its fixed `bot_name`.

Run `panghu_chat/hublog/scripts/generate-service-tokens.py` and store its two JSON outputs separately:

- hash-only JSON in `secret/hublog/auth` for the Hublog API;
- raw-token JSON in `secret/content-agents/auth` for the content-agent Pods.

The raw envelope has this shape (use real generated values only in Vault):

```json
{"github-trending":{"token":"..."},"international-news":{"token":"..."},"meme-collector":{"token":"..."}}
```

`deploy.sh` applies `vault/inventory/content-agents-hublog-externalsecret.yaml`, which syncs one `HUBLOG_SERVICE_TOKENS` key into `content-agents/content-agent-hublog`. CronJobs import that Secret with `envFrom`; no per-bot `SERVICE_TOKEN_*` mapping is needed.

Check only field names, never values:

```bash
kubectl -n content-agents wait --for=condition=Ready \
  externalsecret/content-agent-hublog --timeout=120s
kubectl -n content-agents get secret content-agent-hublog \
  -o jsonpath='{.data}' | jq 'keys'
```

## Build and deploy

The Docker build context is the `panghu_agent` directory:

```bash
cd panghu_agent/content_agents
bash build.sh                 # build only
bash build.sh --push          # build and push
bash deploy.sh                # build, push, and apply all manifests
bash deploy.sh --skip-build   # apply using existing images
```

Override the registry or tag with `REGISTRY=... IMAGE_TAG=...`. The deployment creates the `content-agents` Namespace, a CephFS PVC, the ConfigMap, three CronJobs, and the ExternalSecret. It keeps draft mode enabled by default.

Manual run and logs:

```bash
kubectl -n content-agents create job --from=cronjob/github-trending-agent github-trending-manual
kubectl -n content-agents logs -f job/github-trending-manual
```

## Decoupling contract

The core package never stores a Hublog post ID or assumes a Hublog database. A channel adapter maps `ContentItem` to the target API and records its external ID. Hublog uses `Idempotency-Key: <bot-name>:<content-hash>`, so retries do not create duplicate posts.
