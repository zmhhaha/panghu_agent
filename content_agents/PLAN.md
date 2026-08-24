# Panghu Content Agents Plan

## Goal and boundary

Implement three independent content producers under `panghu_agent/content_agents`: GitHub trends, international news, and meme collection. Collection, normalization, deduplication, risk review, and audit records belong to the bots. Presentation platforms do not.

Hublog is an optional `ChannelAdapter`. The same normalized item can be exported to JSON, RSS/Atom, Hublog, Portal, email, or another future channel. A Hublog outage must not stop collection or review.

## Common content contract

Every bot emits a `ContentItem` with:

- bot identity and version (`bot_name`, `bot_version`, `prompt_version`)
- title, body, summary, language, tags, and topics
- source references (name, URL, external ID, publication time, excerpt)
- risk level, review status, review notes
- UUID, timestamps, and a deterministic `content_hash`

Hublog `post_id` is a channel external ID only. It is not part of the bot's core data model.

## Processing flow

```text
source -> normalize -> deduplicate -> generate -> rule review
                                      |
                                      v
                             ContentItem + JSONL ledger
                                      |
                       +--------------+--------------+
                       |              |              |
                     JSON           RSS           Hublog
                   (review)       (optional)     (optional)
```

Every run gets a `run_id` and records candidate, generated, duplicate, publication, and error counts. Store only the minimum source excerpt needed for verification; do not mirror third-party archives.

## Bot responsibilities

### GitHub trending

Use GitHub Search API with a configurable lookback window and optional `GITHUB_TOKEN`. Report repository purpose, activity, stars/forks, language, license, and the original link. Missing license metadata raises the item to medium risk. Schedule: 08:00 Asia/Shanghai.

### International news

Use configurable RSS/Atom feeds, defaulting to China News International. Separate confirmed information, source wording, and speculation. Default to high risk and human review. Schedule: 08:30 and 20:30 Asia/Shanghai.

### Meme collection

Use configurable trend sources, defaulting to the Baidu Hot Search public board. The collector parses the board's embedded JSON and explains the phrase, context, and propagation without copying original images, video, music, or large user-generated excerpts. Default to medium risk and human review. Schedule: 18:30 Asia/Shanghai.

## Review and publication

`BOT_DRAFT_ONLY=true` is the safe local default:

- JSON remains the review ledger.
- Blocked content goes to no channel.
- RSS and Hublog are recorded as drafts and are not public.
- When draft mode is disabled, only approved content is sent to public channels.

The production ConfigMap currently enables `CONTENT_AUTO_APPROVE=true` and
`BOT_DRAFT_ONLY=false`, so non-blocked content from all three bots is published.
Blocklist matches remain blocked.

Future work can add a review API that promotes `needs_review` to `approved`, followed by an idempotent publication worker.

## Hublog authentication

Bots use separate Hublog Service Tokens, never personal SSO cookies. Raw tokens exist only in the bot Secret. Hublog stores SHA-256 hashes and provisions users with `sso_subject=service:<bot-name>`. Calls use `POST /api/v1/posts`, a Bearer token, and an idempotency key based on the bot name and content hash.

## Kubernetes deployment

- Namespace: `content-agents`
- Three independent CronJobs and one CephFS PVC
- One image and token field per bot
- ConfigMap for non-secret settings; Vault/ExternalSecret for tokens
- `build.sh` builds/pushes images; `deploy.sh` applies Namespace, PVC, ConfigMap, CronJobs, and ExternalSecret

The content-agent Vault record stores a raw-token envelope at `secret/content-agents/auth`:

```text
HUBLOG_SERVICE_TOKENS
```

Hublog keeps a separate hash-only `HUBLOG_SERVICE_TOKENS` value at
`secret/hublog/auth`. The bot runtime selects the entry matching its own
`bot_name`, so CronJobs do not need per-bot token environment mappings.

## Follow-up phases

1. Add source time-window filtering, quality metrics, and stronger historical deduplication.
2. Use the existing optional OpenAI-compatible client for structured summaries, with a deterministic fallback.
3. Add a review API, per-topic pause controls, and channel withdrawal.
4. Move the JSONL ledger to a dedicated PostgreSQL schema; add Elasticsearch only when search is needed.
5. Implement new adapters for Portal, email, and other displays without changing bot core logic.
