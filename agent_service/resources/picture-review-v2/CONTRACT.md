# Classified static review v2

This is profile `video.picture-learning-review.v2`, profile_revision `1`. It uses the existing `video.picture-learning-input.v1` file and immutable image/knowledge transport, with a required frozen `constraints.review_policy`. Do not emit the v1 result or `issues`, source, method, transfer_task, an observation_request, or a passed/approved field.

Policy is exactly `{schema:"video.picture-review-policy.v1",criteria:[{id,requirement,text}],evidence_scope:"static_only"}`. Each criterion has a unique ID and requirement exactly `required` or `optional`; the server owns these labels. Required includes the actual objective and style, visible structure, proportion, coverage and declared light interpretation. Do not downgrade a mandatory artistic goal by calling it a refinement.

Write one UTF-8 JSON object to `output.json` with exactly these outer fields:

```json
{"schema":"video.picture-review-result.v2","input_sha256":"COPY EXACT FROZEN INPUT SHA","mode":"review","review":{"score":83,"summary":"Concrete static-image opinion.","assessments":[{"criterion_id":"COPY REQUIRED CRITERION ID","status":"satisfied","evidence_frame_ids":["COPY ACTUAL OUTPUT FRAME ID"],"reason":"Visible evidence for this criterion."}],"findings":[],"limitations":["No continuous video or audio was observed."],"stop_recommended":false},"evidence_scope":"static_only"}
```

The review has exactly score, summary, assessments, findings, limitations, stop_recommended. Score is a finite number 0..100, never a boolean. Summary/reason/observation/recommendation/limitation must be nonempty. Lists contain at most32 entries; summary at most8000 characters, other text at most4000. All supplied image references identify actual frozen images; use frame_id when supplied, otherwise file_name. Every assessment/finding cites at least one actual output frame. Unknown, repeated or cross-task frame IDs are invalid.

Assessments contain exactly one item for every required criterion, no optional criterion and no duplicate: `{criterion_id,status,evidence_frame_ids,reason}`. Status is `satisfied`, `violation`, or `unresolved`. A required condition not observable from the supplied evidence is unresolved; omission is not success.

Each finding is exactly `{id,criterion_id,severity,certainty,evidence_frame_ids,observation,recommendation}`. IDs are unique. Severity is `blocking` or `advisory`; certainty is `observed` or `uncertain`. Required violation needs a matching blocking/observed finding. Required unresolved needs a matching blocking/uncertain finding. Required satisfied cannot have a contradictory finding. Every optional finding must be advisory. Do not put a required concern under an optional criterion to avoid blocking.

An observed defect can have an unknown cause: describe the visible defect, keep an unverified diagnosis uncertain in the explanation, and do not claim a mesh/camera/source changed solely because two compressed images have different bytes. Uncertainty about a required condition still blocks. Optional suggestions can remain uncertain without being falsely elevated to facts. Limitations preserve unobserved scope separately and cannot certify a required check.

The server alone computes the quality decision: frozen score threshold, all required assessments satisfied, no blocking findings and stop_recommended=false, in addition to independent trusted technical, transport, stop and budget checks. A score is not human approval. In v2 stop_recommended=true blocks output and does not authorize automatic drawing or another task. This does not redefine historical v1 stop/issue semantics.

Only actual supplied static pixels are observed. Do not claim continuous motion, sound, whole-video viewing, transfer, source execution, broad applicability, publication or user acceptance. New captured output/baseline files use PNG. Every actual image is at most2MiB; the coordinator may optimize PNG losslessly, but cannot rescale or convert to JPEG to fit. Do not replace, regenerate or fetch inputs. Knowledge is optional frozen context accessed only through the existing bounded tools.
