> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/lotus-video-retry-20261119/readonly-diagnostics-summary.md`；原文件 SHA-256：`289bb13af7ffcc86850e66f683cdf0a8cf2f609013f4d809d59852370828e4b0`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# Authorized retry: read-only native diagnostics

Project `588a5b7ef481471b950f392f85223bed`\
Experiment `9419391d763f4ccdaf008b1bf3862a12`

The parent submitted one user-authorized retry. This diagnostic agent submitted no business jobs, made no model calls, changed no source/configuration or time limits, and did not stop any worker.

## Round 1 author succeeded

- Job `a266e36d-9274-4ad6-acfd-2f0bc81fe600`, attempt `37108d0c-78da-40e3-87c7-4e908ede92ae`, fence 420.
- Completed; service stop proof true; allocation retired.
- Service stop observed at `1795088144.8633952`, 501.62 seconds after creation and 98.38 seconds before the job deadline.
- Five model requests completed with HTTP 200 and terminal Responses completion events. Tool calls: `read_inputs`, `skill_read`, `skill_read`, `write`; final response ended without another tool call.
- Model image-input audit: eight unique reference-image hashes; 32 total image parts across the repeated conversation requests. This proves image inputs were transported, not that continuous motion or audio were observed.
- The resulting render operation completed and advanced to review.

## Round 1 review succeeded

- Job `a8432a99-1bf8-4e1d-a999-8d890203a262`, attempt `1f84bdc0-b56a-477d-a5ec-657ebefd164e`, fence 421.
- Completed; service stop proof true; allocation retired.
- Three model requests completed with HTTP 200: `read_inputs`, `write`, final completion.
- Model image-input audit: 16 unique reference/output-image hashes, 32 total image parts. Continuous playback, motion fidelity and audio perception are not established by this audit.

## Round 2 was stopped at the original experiment budget

- Job `1d00ad43-96e0-4e9a-a53f-ceaf1ee00496`, attempt `7be079ff-801c-47c2-99b6-11fd905126a4`, fence 422.
- Deadline inherited from the experiment: `1795088542.4720168`.
- Job terminal state `cancelled`, `error_code=cancelled`; service stop proof true; allocation retired. Service stop observed at `1795088545.496272`, approximately three seconds after the deadline.
- No round-2 artifact was returned. Allocation fallback result was `capsule_stopped_without_result`; this is not an observed `model_error`.
- Three completed upstream requests were followed by one still-admitted request in the saved metadata snapshot.
- The parent observed experiment `completed`, `error=null`, `stop_reason=budget_exhausted`, preserving the reviewed first-round result. Final product/hash/playback checks are the parent's separate validation.

## Diagnostic capture and limits

- `kubectl logs -f` could not create an fsnotify watcher because the node reported too many open files. This is a limitation of the log-follow channel; no causal link to the business task's prior model error was established.
- Used bounded, read-only polling of the exact worker log directories instead. The last observer retained its file descriptor through directory deletion. Only fixed `pi_model_failure`, `execution_failure`, and numeric output-accounting records were eligible for persistence.
- No fixed native error records were captured for these workers. This is not a claim of complete native-log coverage.
- All log observers ended. The stop audit found zero remaining matching observer processes and signaled zero workers.
- Evidence includes only safe metadata/classifications and receipt hashes; raw log lines, provider responses, prompts, tokens, and artifact contents are not retained in the diagnostic summaries.

Relevant files: `author-readonly-summary.json`, `author-final-custody.json`, `review-final-custody.json`, `round2-author-final-custody.json`, stage-specific `*-model-progress-safe.json`, `discovered-operations.json`, `job-*-node-log-receipt.json`, `readonly-observer-stop-receipt.json`.

Date: 2026-11-19.
