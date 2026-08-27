# Panghu Content Agents

This directory contains platform-independent content bots:

- `github_trending_agent`: collect active and popular open-source repositories.
- `international_news_agent`: collect international-news leads from RSS/Atom feeds.
- `finance_news_agent`: collect finance and market briefs, including 财联社电报.
- `programmer_jobs_agent`: summarize daily programming-job demand from public technical-job RSS and APIs.
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
python -m content_agents.finance_news_agent.main --sample
python -m content_agents.programmer_jobs_agent.main --sample
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
The shared ledger de-duplicates by bot, source name, and source `external_id`
before falling back to the rendered content hash, so a revised feed summary
does not create a duplicate Hublog post.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `CONTENT_DATA_DIR` | `/data/content-agents` | Persistent JSONL/RSS directory |
| `AGENT_CHANNELS` | `json` | Comma-separated: `json`, `rss`, `hublog` |
| `BOT_DRAFT_ONLY` | `true` | Keep public channels in draft mode |
| `CONTENT_AUTO_APPROVE` | `false` | Mark non-blocked generated content as `approved`; blocklist hits remain blocked |
| `BOT_MAX_ITEMS` | `5` | Maximum candidates per run |
| `BOT_LOOKBACK_HOURS` | `24` | GitHub query window |
| `HUBLOG_BASE_URL` | cluster-internal Hublog service | Hublog API base URL |
| `HUBLOG_SERVICE_TOKENS` | empty | Raw-token JSON envelope; each bot selects its own entry |
| `HUBLOG_SERVICE_TOKEN` | empty | Compatibility fallback for local runs |
| `GITHUB_TOKEN` | empty | Optional GitHub API token |
| `NEWS_FEEDS` | China News International | `name|url||name|url` |
| `FINANCE_NEWS_FEEDS` | 财联社电报 | `name|url||name|url`; 财联社电报 API is parsed as JSON |
| `PROGRAMMER_JOB_FEEDS` | RemoteJobsCN/Remotive/Remote OK/AI Dev Jobs | `name|url||name|url`; public RSS/API sources only |
| `PROGRAMMER_JOB_LOOKBACK_DAYS` | `7` | Keep recent job records within this window before the daily LLM summary |
| `MEME_FEEDS` | Bilibili Hot Ranking | `name|url||name|url`; Bilibili ranking API is parsed as JSON |
| `MEME_MIN_SCORE` | `6` | Minimum short-phrase meme score; ordinary news and sensitive events are discarded |
| `MEME_MAX_TITLE_LENGTH` | `12` | Maximum title length for a reusable meme phrase |
| `MEME_AGENT_ENABLED` | `true` | Enable shared LLM-based meme judging |
| `MEME_AGENT_SERVICE_URL` | cluster-local `content-llm-service` | Shared LLM service endpoint |

Meme Collector does not own an LLM configuration. It sends each Bilibili
candidate to the shared `content-llm-service`, which uses CrewAI and selects
the provider from its own `PROVIDER` ConfigMap value. The service owns model
and endpoint settings and reads credentials from its ExternalSecret. It
returns `is_meme`, a short `phrase`, `context`, `joke`, and `confidence`.

Publication policy:

- JSON always keeps the generated item as a review ledger.
- `blocked` items are never sent to a channel.
- With `BOT_DRAFT_ONLY=true`, RSS and Hublog are recorded as `draft` and are not public.
- With draft mode disabled, only `approved` items go to RSS or Hublog.
- Meme candidates must pass the event-joke filter; the bot prefers puns, nicknames, reversals, idiom remixes, and colloquial phrases from Bilibili. It intentionally drops ordinary news instead of reposting it.
- Production currently sets `CONTENT_AUTO_APPROVE=true`, so all non-blocked GitHub, news, and meme items are approved automatically. Blocklist matches remain blocked.

## Hublog tokens

Never commit raw tokens. Hublog stores only SHA-256 hashes. The content-agent namespace receives a separate raw-token JSON envelope under the same key name, and each bot selects its own entry by its fixed `bot_name`.

Run `panghu_chat/hublog/scripts/generate-service-tokens.py` and store its two JSON outputs separately. This includes the `finance-news` bot token:

- hash-only JSON in `secret/hublog/auth` for the Hublog API;
- raw-token JSON in `secret/content-agents/auth` for the content-agent Pods.

The raw envelope has this shape (use real generated values only in Vault):

```json
{"github-trending":{"token":"..."},"international-news":{"token":"..."},"finance-news":{"token":"..."},"programmer-jobs":{"token":"..."},"meme-collector":{"token":"..."}}
```

When adding a bot to a running cluster, generate only its new token
and merge that one-key JSON object into both existing Vault envelopes. Do not
regenerate or replace the existing bot entries:

```bash
cd ~/armbianbegin/panghu_chat/hublog
python3 scripts/generate-service-tokens.py \
  --bot programmer-jobs --expires-at 2027-08-26T00:00:00Z
```

The first JSON output belongs in `secret/hublog/auth` and contains only a
token hash; the second belongs in `secret/content-agents/auth` and contains
the raw service token. Merge each with its corresponding existing
`HUBLOG_SERVICE_TOKENS` value, then force-sync `content-agent-hublog` before
starting the new CronJob. Never put the raw token in a ConfigMap or Git.

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

Override the registry or tag with `REGISTRY=... IMAGE_TAG=...`. The deployment creates the `content-agents` Namespace, a CephFS PVC, the ConfigMap, five CronJobs, and the ExternalSecret. It keeps draft mode enabled by default.

Manual run and logs:

```bash
kubectl -n content-agents create job --from=cronjob/github-trending-agent github-trending-manual
kubectl -n content-agents logs -f job/github-trending-manual
```

Finance-agent smoke test after deployment:

```bash
kubectl -n content-agents create job --from=cronjob/finance-news-agent finance-news-manual
kubectl -n content-agents wait --for=condition=complete job/finance-news-manual --timeout=180s
kubectl -n content-agents logs job/finance-news-manual
```

Programmer-jobs smoke test after the token and shared LLM service are ready:

```bash
kubectl -n content-agents create job --from=cronjob/programmer-jobs-agent programmer-jobs-manual
kubectl -n content-agents wait --for=condition=complete job/programmer-jobs-manual --timeout=300s
kubectl -n content-agents logs job/programmer-jobs-manual
```

The job requests exactly one batch LLM summary per run, after collecting up to
80 job records from RemoteJobsCN, Remotive, Remote OK, and AI Dev Jobs. It
publishes one daily report only when at least one public source returns valid
technical jobs and the LLM returns a valid summary. A source/LLM failure
results in a quiet run instead of an empty post.

## Decoupling contract

The core package never stores a Hublog post ID or assumes a Hublog database. A channel adapter maps `ContentItem` to the target API and records its external ID. Hublog uses `Idempotency-Key: <bot-name>:<content-hash>`, so retries do not create duplicate posts.
