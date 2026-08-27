# Programmer Jobs Agent

`programmer-jobs-agent` publishes one daily Chinese-language summary of recent
technical hiring. It does not access Boss 直聘, nor does it use accounts,
cookies, proxy rotation, or an access-control bypass.

## Sources

The default `PROGRAMMER_JOB_FEEDS` combines these accessible public sources:

| Source | Format | Role in the report |
| --- | --- | --- |
| RemoteJobsCN | RSS | Chinese remote and Web3 technical opportunities |
| Remotive | JSON API | Structured remote engineering roles and skill tags |
| Remote OK | JSON API | Broader remote technical-role sample |
| AI Dev Jobs | JSON API | AI/ML-oriented developer roles |

Only technical titles, tags, or descriptions are retained. The configured
`PROGRAMMER_JOB_LOOKBACK_DAYS` window defaults to seven days. Each source is
independent: a temporary source failure is logged while the remaining sources
continue. If no valid technical roles remain, the run does not publish.

## Output

The agent collects at most 80 normalized roles, then sends all of them to
`content-llm-service` in one request. The generated Hublog report contains:

- hiring directions with their common skills;
- cross-source high-frequency technical skills;
- experience, education, and compensation signals only when source fields
  support them;
- source API/RSS links for verification.

The ledger uses `programmer-jobs-daily:<Asia/Shanghai date>` as the source
identity, so retries on the same day cannot create a second report.

## Configuration

The non-secret configuration is in `content_agents/k8s/configmap.yaml`:

```yaml
PROGRAMMER_JOB_FEEDS: "RemoteJobsCN|https://remotejobscn.com/rss.xml||Remotive|https://remotive.com/api/remote-jobs||Remote OK|https://remoteok.com/api||AI Dev Jobs|https://aidevboard.com/api/v1/jobs?tags=python,ai&posted_within_days=7"
PROGRAMMER_JOB_LOOKBACK_DAYS: "7"
```

Keep the source names unchanged unless the corresponding source adapter is
also updated. API keys are not required for these default read-only feeds.

## Deployment

Deploy the updated shared LLM service first because the report uses its
`/v1/jobs/programmer-summary` endpoint:

```bash
cd ~/armbianbegin/panghu_agent/content-llm-service
bash deploy.sh
```

Then build and deploy the agent:

```bash
cd ~/armbianbegin/panghu_agent/content_agents
bash deploy.sh
kubectl -n content-agents create job --from=cronjob/programmer-jobs-agent programmer-jobs-manual
kubectl -n content-agents wait --for=condition=complete job/programmer-jobs-manual --timeout=300s
kubectl -n content-agents logs job/programmer-jobs-manual
```

The production CronJob runs daily at 09:00 `Asia/Shanghai`. Completed jobs are
removed after one hour.
