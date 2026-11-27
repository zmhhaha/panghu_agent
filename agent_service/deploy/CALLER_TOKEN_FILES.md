# Additive caller token files

Control services accept repeatable explicit CLI arguments:

```
--caller-token-file video-picture-review-v2=/run/agent-caller-tokens/review/token
```

The public catalog must already declare that caller token reference. The original
private JSON is loaded through its unchanged startup path, then only missing
caller references may be added from independently mounted files. The operator
does not fetch, decode, copy or rewrite an existing Kubernetes Secret. Existing
caller, Worker, model and database credentials are not replaced.

No environment variable, public catalog field, job or model output selects these
files. Duplicate references, duplicate paths/inodes, undeclared references,
attempted replacement of an original token, and values equal to any original
token are rejected. Files must be absolute ordinary single-link files without
symlinks/reparse points, 32–512 printable ASCII token characters with at most one
final LF. POSIX files require root/current-user ownership, no world access,
no execute/special bits or group write; group read requires root/current group.
Mount a new immutable Secret using a read-only subPath with mode 0440 and the
existing service fsGroup. No token or private path is printed on failure.

`check` reports only added public token reference names and the resulting caller
count. Verify real readiness using the new credential to GET `/v1/capabilities`,
confirm its single intended profile and actual admitted binding, and compare the
old public callers/config prefix and old Secret references. Checking a token does
not create an Agent job or prove any model/artistic behavior.

This loader/CLI change belongs to the control image. Reusing a separately admitted
Worker requires explicit equality of its actual execution paths/bundle/tool
bytes and pins. Do not claim that the entire later Agent source tree equals an
earlier Worker revision after adding these control-only files.
