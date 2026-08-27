# Programmer Jobs Agent

`programmer-jobs-agent` reads the job-list response used by Boss 直聘 public
programming-job search pages and publishes one daily market summary. It does
not use a Boss account, cookie, private API, or verification bypass. A source
verification or IP-rejection response is a normal quiet run: no empty report
is published.

## Output

After collecting at most 80 valid job records, the agent sends their compact
fields to `content-llm-service` once. The resulting Hublog post includes:

- the main hiring directions and their commonly requested skills;
- high-frequency technologies;
- observed experience, education, and salary signals when present;
- public Boss search-page links for verification.

The ledger uses `programmer-jobs-daily:<Asia/Shanghai date>` as the source identity, so
retries on the same day do not create a second Hublog report.

## Search scope

`BOSS_JOB_SEARCHES` belongs in `k8s/configmap.yaml`. Its syntax is
`name|url||name|url`. The default searches for `开发工程师`. For example, to
cover particular locations or roles, configure public search URLs such as:

```yaml
BOSS_JOB_SEARCHES: "北京后端|https://www.zhipin.com/web/geek/job?query=Java&city=101010100||上海前端|https://www.zhipin.com/web/geek/job?query=React&city=101020100"
```

Use only URLs that are available without credentials. The agent does not
attempt to circumvent a page's access controls. Boss can reject a server exit
IP with response code `35`; in that case the cluster needs an official
authorized data source or a different, approved collection path before this
bot can publish.

## Deployment

First deploy the updated shared LLM service, because this bot requires its
`/v1/jobs/programmer-summary` endpoint:

```bash
cd ~/armbianbegin/panghu_agent/content-llm-service
bash deploy.sh
```

Generate only the new service-token entry, then merge its two JSON outputs into
the existing `HUBLOG_SERVICE_TOKENS` values in both Vault paths. Do not replace
the existing entries for other bots.

```bash
cd ~/armbianbegin/panghu_chat/hublog
BOT_NAME=programmer-jobs bash scripts/generate-service-token.sh
```

- The hash-only output goes to `secret/hublog/auth`.
- The raw-token output goes to `secret/content-agents/auth`.

After writing both merged values, sync the ExternalSecrets. Hublog reads its
hash map from a Secret volume, so it does not need an API rollout for this new
bot identity.

```bash
kubectl -n hublog annotate externalsecret hublog-config force-sync="$(date +%s)" --overwrite
kubectl -n content-agents annotate externalsecret content-agent-hublog force-sync="$(date +%s)" --overwrite
kubectl -n content-agents wait --for=condition=Ready externalsecret/content-agent-hublog --timeout=120s
```

Build and apply the agent manifests:

```bash
cd ~/armbianbegin/panghu_agent/content_agents
bash deploy.sh
kubectl -n content-agents create job --from=cronjob/programmer-jobs-agent programmer-jobs-manual
kubectl -n content-agents wait --for=condition=complete job/programmer-jobs-manual --timeout=300s
kubectl -n content-agents logs job/programmer-jobs-manual
```

The production CronJob runs daily at 09:00 `Asia/Shanghai`. Completed Jobs are
automatically removed after one hour.
