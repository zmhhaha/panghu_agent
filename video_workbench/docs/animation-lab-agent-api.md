# 动画效果库：外部 Agent API

外部 Agent 可以直接使用现有 REST JSON API，完成列出效果、提交制作、查询状态、下载候选视频和源码、保存反馈及取消任务。

- API 基址：`https://video-service.panghuer.top/dev/animations/api/method-lab`
- [在线静态指南](https://video-service.panghuer.top/dev/animations/agent-api.html)
- [OpenAPI 3.1 合同](https://video-service.panghuer.top/dev/animations/agent-api.openapi.json)
- [下载 Python 标准库客户端](https://video-service.panghuer.top/dev/animations/agent-api-client.py)

这些入口沿用动画效果库的 OAuth 登录及开发者权限。合同只列出 Agent 接入支持的字段与操作；服务响应可以包含其他字段，调用方应忽略未知字段。

## 授权与写请求

当前凭据是用户安全提供的 OAuth 会话 Cookie 文件，部署 Cookie 名为 `__Host-video_workbench`。客户端使用 Netscape 格式的 `cookies.txt`；文件应保留原 Cookie 的域、路径、Secure 和过期信息。由用户通过获准的安全方式提供文件，接入程序不读取或导出浏览器凭据。会话过期后需要用户重新授权。独立的长期机器凭据尚未实现，此 API 不支持 Bearer 授权。

每次 POST 都需要以下三项，缺一不可：

1. 有效的登录 Cookie，且会话用户具有现有开发者权限。
2. `Origin: https://video-service.panghuer.top`。
3. `X-Animation-Lab-Token: <catalog.csrf_token>`，并设置 `Content-Type: application/json`。

先 GET `/catalog` 获取当前 `csrf_token`。它是写请求防伪 token，不是独立登录凭据；不应写进请求 JSON、日志或源码。服务重启后应重新读取目录。不要自行设置身份转发头；身份由原 OAuth 入口确定。

## 最短调用流程

以下为 POSIX shell 示例；Windows 可用 `curl.exe` 并使用本机 shell 的换行语法。示例 Cookie 与 token 均为文件名或占位符，不包含真实凭据。

```sh
curl --fail-with-body --cookie cookies.txt \
  https://video-service.panghuer.top/dev/animations/api/method-lab/catalog \
  --output catalog.json
```

从返回 JSON 读取 `availability.enabled`、已有 `commissions` 及 `csrf_token`。`enabled: false` 表示当前执行器不可用；目录仍可读取。若返回登录重定向或 HTML，请先重新授权，不要把登录页当作成功的目录。

创建 `request.json`：

```json
{
  "request_id": "17c8a3ea-52b4-42d0-b875-f55ab241e2db",
  "title": "聚焦异常数据点",
  "goal": "制作一段数据点从整体分布中被选出并放大解释的动画。保留总体与局部的对应关系，文字清楚且不遮挡数据。",
  "duration_seconds": 18,
  "budget": {"max_rounds": 2, "max_seconds": 600},
  "criteria": [
    {"id": "focus", "description": "选中的数据点与总体分布之间的对应关系清楚。"},
    {"id": "labels", "description": "关键标签完整可读，且不遮挡主要图形。"}
  ]
}
```

`request_id` 是调用方提供的幂等键；每个新任务生成新 UUID。示例中的固定 UUID 仅用于说明，请勿让不同任务共用它。`goal` 为必填的非空文本，最长 16000 字符；`title` 可选，最长 500 字符。`duration_seconds` 默认 18，范围 1–60 秒，必须落在 24 fps 的整帧边界。`budget.max_rounds` 为 1–8 的整数，`max_seconds` 为 60–3600 的整数。

`criteria` 可省略，由服务规划；显式提供时为 1–16 项，`id` 唯一，每项必须有 `id` 和非空 `description`（最长 4000 字符），可选 `requires_motion`、`requires_audio` 布尔值及 `observation` 文本。运动或音频要求只有实际观察证据才能判定；不能从静帧评审推断已满足。

```sh
curl --fail-with-body --cookie cookies.txt \
  -H 'Origin: https://video-service.panghuer.top' \
  -H 'X-Animation-Lab-Token: <catalog.csrf_token>' \
  -H 'Content-Type: application/json' \
  --data-binary @request.json \
  https://video-service.panghuer.top/dev/animations/api/method-lab/commissions
```

成功返回 `202` 和委托详情。保存返回的 `id`，再查询状态：

```sh
curl --fail-with-body --cookie cookies.txt \
  https://video-service.panghuer.top/dev/animations/api/method-lab/commissions/11111111-1111-4111-8111-111111111111
```

GET 只读，不会启动制作、执行模型、续跑任务或增加轮次。轮询应设定间隔和总等待上限。`202` 只代表提交被接受，不能当作完成或质量认可。

## 状态、结果与下载

| 字段或状态 | 含义 |
| --- | --- |
| `queued` / `running` | 已入队 / 正在执行 |
| `cancelling` | 已请求取消，等待在途操作停止 |
| `completed` / `failed` / `cancelled` | 三种委托终态 |
| `stop_reason` | `goal_met`、`budget_exhausted`、`no_progress`、`developer_stopped`、`failed` 或 `cancelled` |
| `summary` / `error` | 结果说明 / 失败代码；可能为空 |
| `best_candidate_id` | 当前选中的候选，可能为空 |
| `candidates` | 候选列表，包括 `id`、`media_url`、`source_url`、`technical_status` |

`completed` 也可能是预算用完或停止进展；请结合 `stop_reason`、`summary`、`remaining_issues` 和候选结果判断。响应中的 `budget`、`usage`、`remaining` 使用服务内部的 `creator`、`review`、`render`、`seconds` 计数，形状与提交时的预算不同。

从详情取得实际候选 ID 或返回的 URL 后下载。返回 URL 可能是站点根相对地址，应相对于同一公网 origin 解析。下载同样需要登录 Cookie。

```sh
curl --fail-with-body --cookie cookies.txt \
  https://video-service.panghuer.top/dev/animations/api/method-lab/commissions/11111111-1111-4111-8111-111111111111/candidates/candidate-1/media \
  --output candidate.mp4
curl --fail-with-body --cookie cookies.txt \
  https://video-service.panghuer.top/dev/animations/api/method-lab/commissions/11111111-1111-4111-8111-111111111111/candidates/candidate-1/source \
  --output animation-source.json
```

媒体为 `video/mp4`，支持单段字节 Range；源码是 `application/json` 下载。候选 ID 使用返回值，`candidate-1` 仅是占位示例。

## 继续优化、反馈和取消

继续优化仍使用 **POST `/commissions`**。新建 `request_id`，设置 `base_commission_id`、`base_candidate_id`，并在 `feedback` 写明本轮改进目标；同时提供 `goal` 和新预算。例如：

```json
{
  "request_id": "bb8c0924-41c1-4da1-b2b5-c0e038ca7741",
  "title": "改善聚焦后的标签间距",
  "goal": "延续总体到局部的聚焦结构，改善标签间距与画面留白。",
  "duration_seconds": 18,
  "budget": {"max_rounds": 1, "max_seconds": 300},
  "base_commission_id": "11111111-1111-4111-8111-111111111111",
  "base_candidate_id": "candidate-1",
  "feedback": "保留总体与选中点的对应关系，增大右侧标签间距，避免遮挡。"
}
```

省略 `base_candidate_id` 时服务选择基线委托的 `best_candidate_id`；基线必须存在且当前用户可访问。省略 `criteria` 时继承基线标准。此请求创建新委托，原委托保留。

`reference` 可附 `{ "url": "https://example.com/reference.mp4", "start_seconds": 0, "end_seconds": 8 }`。参考 URL 和片段时间只是需求资料，提交链接不会自动读取或观看原片；需要将关键视觉特征、节奏和希望达到的效果写入 `goal` 或 `feedback`。

**POST `/commissions/{id}/feedback`** 仅记录已授权的意见，不会自动制作、选择候选、授予人类认可或发布权限。将下列内容存为 `feedback.json`，用与提交相同的 POST 请求头和 `--data-binary @feedback.json` 发送：

```json
{
  "request_id": "11b15293-1973-46a0-bd82-fba87e2d8c34",
  "kind": "comment",
  "candidate_id": "candidate-1",
  "comment": "右侧标签仍遮挡数据，请增加间距。",
  "start_seconds": 6,
  "end_seconds": 9,
  "region": {"x": 0.6, "y": 0.1, "width": 0.3, "height": 0.4}
}
```

时间范围可省略，但必须成对提供，且 `0 <= start_seconds <= end_seconds <= 候选时长`。区域可省略；其四个值均为 0–1 的归一化坐标，矩形必须位于画面内。也可用 `kind: "preference"`、同委托内两个不同的 `left_candidate_id` / `right_candidate_id`，以及 `preference: "left" | "right" | "equal" | "neither"` 保存比较偏好，`comment` 可选。比较反馈不接受时间或区域字段。

**POST `/commissions/{id}/cancel`** 的 JSON 必须是 `{}`。返回 `200` 和当前详情；可能先进入 `cancelling`，应继续查询到终态。重复取消不会启动新任务。发布与人类验收不属于本合同；Agent 只能依据用户实际授权记录意见，不能代替用户声明认可。

## Python 客户端

仓库中可使用模块；下载的独立文件只需 Python 标准库，不依赖仓库或额外包：

下载入口 URL 为 `agent-api-client.py`，响应文件名为 `animation_lab_client.py`；以下按下载后的文件名调用。

```sh
python -m video_workbench.animation_lab_client --base-url https://video-service.panghuer.top/dev/animations/api/method-lab --cookie-file cookies.txt catalog
python animation_lab_client.py --base-url https://video-service.panghuer.top/dev/animations/api/method-lab --cookie-file cookies.txt submit request.json
python animation_lab_client.py --base-url https://video-service.panghuer.top/dev/animations/api/method-lab --cookie-file cookies.txt status 11111111-1111-4111-8111-111111111111
python animation_lab_client.py --base-url https://video-service.panghuer.top/dev/animations/api/method-lab --cookie-file cookies.txt wait 11111111-1111-4111-8111-111111111111 --timeout 600 --interval 2
python animation_lab_client.py --base-url https://video-service.panghuer.top/dev/animations/api/method-lab --cookie-file cookies.txt feedback 11111111-1111-4111-8111-111111111111 feedback.json
python animation_lab_client.py --base-url https://video-service.panghuer.top/dev/animations/api/method-lab --cookie-file cookies.txt cancel 11111111-1111-4111-8111-111111111111
python animation_lab_client.py --base-url https://video-service.panghuer.top/dev/animations/api/method-lab --cookie-file cookies.txt download 11111111-1111-4111-8111-111111111111 --candidate-id candidate-1 --kind media --output candidate.mp4
```

客户端读取用户提供的 Netscape Cookie 文件，POST 前自行 GET 目录并设置 Origin / CSRF 头。`catalog` 输出隐藏 CSRF token。`submit` 和 `feedback` 的文件必须显式包含 UUID `request_id`；不会替调用方生成新的幂等键。`wait` 要求显式设置总超时与轮询间隔；超时只结束本地等待，不取消服务端任务。客户端不自动重试写请求、不自动发布。成功输出 JSON 到 stdout，错误输出安全的 JSON 到 stderr 并退出 1；到达业务终态后仍需检查 `state`，命令成功不代表动画制作成功。

`download` 可将 `--kind` 改为 `source` 保存源码 JSON；省略 `--candidate-id` 时先读取详情的 `best_candidate_id`。输出文件必须尚不存在。下载完成输出候选 ID、字节数和 SHA-256，不把视频内容打印到终端。

## 错误与幂等处理

API 错误通常返回 `{ "error": "错误代码" }`。OAuth 入口可能先返回登录重定向或 HTML；应按授权失败处理，重新授权后再调用。

| HTTP 状态 | 调用方处理 |
| --- | --- |
| `200` | 查询、反馈、取消或下载成功；检查响应中的业务状态 |
| `202` | 制作请求已接受；保存 `id` 并查询 |
| `400` / `422` | JSON、字段或业务参数无效；修正后作为新意图提交 |
| `401` / `403` / 登录重定向 | 会话、开发者权限、Origin 或 CSRF 检查未通过；不要循环重试 |
| `404` | 委托或候选不存在、不可见或已移入回收站 |
| `409` | 幂等键内容冲突、基线不可用或媒体校验冲突；读取当前状态并解决具体冲突 |
| `413` / `415` | 请求过大（POST 上限 1 MiB）或不是 `application/json` |
| `502` / `503` / `504` | 执行服务不可用或网关失败；提交是否落库可能不明确 |

相同会话用户对同一个 `request_id` 提交相同制作请求，会返回已有委托；更改请求内容会得到 `409 animation_method_commission_idempotency_conflict`。反馈同样使用自己的 `request_id`，内容或关联委托改变会得到 `409 animation_method_feedback_conflict`。

POST 超时或连接断开时，先在 `/catalog` 中按原 `request_id` 查找委托，再 GET 详情。只有在需要人工重放且结果仍不明确时，才使用**原文件和原 `request_id`**重放；不要自动生成新 ID 或自动追加轮次。反馈可从委托详情的 `feedback` 列表按 `request_id` 核对。新优化任务与网络超时重放应使用不同的处理方式：前者新建请求，后者保留原幂等键。
