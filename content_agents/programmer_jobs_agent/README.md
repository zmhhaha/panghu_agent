# Programmer Jobs Agent

`programmer-jobs-agent` publishes one daily Chinese-language summary of recent
technical hiring and one weekly trend report. It does not access Boss 直聘,
nor does it use accounts, cookies, proxy rotation, or an access-control bypass.

## Sources

The default `PROGRAMMER_JOB_FEEDS` combines these accessible public sources:

| Source | Format | Role in the report |
| --- | --- | --- |
| RemoteJobsCN | RSS | Chinese remote and Web3 technical opportunities |
| Remotive | JSON API | Structured remote engineering roles and skill tags |
| Remote OK | JSON API | Broader remote technical-role sample |
| AI Dev Jobs | JSON API | AI/ML-oriented developer roles |
| JDWatch Daily | Public HTML daily report | Public aggregated technical-job daily report |

Only technical titles, tags, or descriptions are retained. The configured
`PROGRAMMER_JOB_LOOKBACK_DAYS` window defaults to seven days. Each source is
independent: a temporary source failure is logged while the remaining sources
continue. If no valid technical roles remain, the run does not publish.

JDWatch is accessed once per daily run through its public blog index and the
latest daily report. The agent then visits up to
`PROGRAMMER_JOB_MAX_PER_SOURCE` public job-detail pages in report order, with a
random 3-10 second pause between detail requests. The detail page's public
JSON-LD is used for the title, company, location, skills, description, and
posting date; the report entry is retained as a fallback when a detail page
has no usable structured data. A 403 or 429 stops the remaining detail fetches
for that run. The agent does not access login pages, CAPTCHA challenges, or
paid content, and identifies itself with a transparent User-Agent.

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

The weekly task reads the latest seven daily reports from this bot through
Hublog `GET /api/v1/me/posts`, sends those reports to
`/v1/jobs/programmer-weekly-summary` in one LLM request, and publishes one
`programmer-jobs-weekly:<ISO year-week>` report. Raw job records are not stored
by this agent.

## Configuration

The non-secret configuration is in `content_agents/k8s/configmap.yaml`:

```yaml
PROGRAMMER_JOB_FEEDS: "RemoteJobsCN|https://remotejobscn.com/rss.xml||Remotive|https://remotive.com/api/remote-jobs||Remote OK|https://remoteok.com/api||AI Dev Jobs|https://aidevboard.com/api/v1/jobs?tags=python,ai&posted_within_days=7||JDWatch Daily|https://www.jdwatch.work/blog"
PROGRAMMER_JOB_LOOKBACK_DAYS: "7"
PROGRAMMER_JOB_MAX_PER_SOURCE: "20"
```

Keep the source names unchanged unless the corresponding source adapter is
also updated. API keys are not required for these default read-only feeds.

## Deployment

Deploy the updated shared LLM service first because the reports use its
`/v1/jobs/programmer-summary` and `/v1/jobs/programmer-weekly-summary` endpoints:

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

The production daily CronJob runs at 09:00 `Asia/Shanghai`. The weekly CronJob
runs on Sunday at 10:00 `Asia/Shanghai`. Completed jobs are removed after one
hour.
