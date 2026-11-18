# 图片学习库

图片学习与需求创作分别列表。开发者提供实际参考图和学习目标，Agent 直接读取参考像素，生成绘制源码，经过真实 PNG 渲染后对照评审；先改善复现，再在本次预算允许时完成一次由独立评审出题的换内容验证。首页按项目展示，内部尝试和迁移作品保留在项目详情中。

入口为 `/dev/animations/picture-library.html`，与效果库、知识库使用一致的顶部导航和右侧主要操作。图片学习不等于上传一张成品素材，实际产物包括绘制源码、渲染图片、回执和输入记录。正式设计见 [reference-learning-libraries-design.md](reference-learning-libraries-design.md)。

## 网页使用

1. 点击“新建图片学习”，上传 PNG 或 JPEG 参考图片，说明重点学习的造型、笔触、材质、光影或构图技法。
2. 按需指定输出画幅、可观察标准、本次预算，以及是否加载已发布 WikiSkill。输出不强制采用参考图片的长宽比。
3. 启动后，在项目详情对照参考与真实渲染结果。执行中只有查看和停止；新意见用于下一次实验，不修改当前冻结需求。
4. 结束后可继续优化、从零重做，或对技术失败进行重试。继续优化可以选定明确且已实际生成图片的复现作品；从零重做不沿用旧源码、布局和专用方法；技术重试保留原始需求、预算和知识版本。只有源码、尚未出图的版本仍可下载源码，但不能采用或作为继续优化的基线。
5. 切换查看复现作品与换内容验证作品，下载图片、源码和回执。内部最佳和开发者采用的默认作品分别显示，新结果不会覆盖已采用作品。
6. 展开“实验历史与冻结需求”查看本次提交内容。进一步展开“查看实际 Agent 输入”，只读查询已保存的角色输入；这不等于导出全部系统提示词。

项目不因一次结束而失去继续研究的能力。开发者可以继续发起新的有界实验；这不受正片自动打磨轮数的限制。

## 输入和证据边界

上传的是实际图片字节，不是由另一角色概括图片内容后只向创作者提供文字。原始图的摘要、尺寸和所属身份保存为参考记录。素材到达模型的传输证据与模型是否理解、画面是否优质分别记录。

一幅图片不能证明动画流畅、整段播放、音质或音画配合已经验证。换内容验证尚未执行或未通过时，详情保留相应范围，不能用复现完成代替迁移通过。采用默认作品也不自动给予艺术验收或发布知识。

发布仍由开发者在知识库主动发起，不能因图片出现新产物而自动发版。静态技法进入 WikiSkill 时应注明画面验证范围和动态未验证范围；发布资料须内化为技法，不依赖外部原图地址才能使用。

## 外部 Agent API

结构化合同为 [picture-api.openapi.json](../animation_lab_web/picture-api.openapi.json)，公网根路径：

```text
https://video-service.panghuer.top/dev/animations/api/picture-lab
```

沿用站点的 OAuth 登录和开发者权限。调用方使用用户安全提供的会话文件；不从浏览器导出凭据。GET `/catalog` 返回 `csrf_token`；POST 除 `application/json` 外，还须提供公网同源 `Origin` 和 `X-Animation-Lab-Token`。当前合同不声称存在独立长期机器令牌。

先 POST `/references` 保存实际图片，PNG/JPEG 解码后最大 8 MiB：

```json
{
  "request_id": "0cfd4015-0f62-4d58-878d-3724bb5b6630",
  "file_name": "reference.png",
  "mime_type": "image/png",
  "content_base64": "此处填写实际文件字节的标准Base64"
}
```

再引用返回的 `id`，POST `/projects`：

```json
{
  "request_id": "9bdd946e-103e-423c-922e-e8d2723b6ee0",
  "title": "山石皴擦与留白",
  "goal": "学习浓淡干湿笔触如何构成山体体积，保留前后景层次，不复制水印与字幕。",
  "reference_ids": ["替换为实际返回的参考编号"],
  "criteria": ["笔触参与造型，不呈现重复的带状墨条", "近浓远淡，留白支撑空间层次"],
  "width": 1280,
  "height": 720,
  "use_wikiskill": true,
  "max_rounds": 3,
  "max_seconds": 900
}
```

以 GET `/projects/{project_id}` 轮询结果；不要为了刷新进度重发创作。源码、PNG、回执使用详情中返回的产物地址读取。后续实验 POST `/projects/{project_id}/experiments`：

```json
{
  "request_id": "aa721cfe-8173-46ca-8e64-9e833069495c",
  "mode": "continue",
  "feedback": "保留目前的构图，重点改善山体转折处的皴擦，避免统一方向的长条笔触。",
  "use_wikiskill": false,
  "max_rounds": 3,
  "max_seconds": 900
}
```

`continue` 可传 `base_candidate_id` 选定同项目已出图的复现作品。`fresh` 只保留目标、参考和本次精简问题。`retry` 只传 `{"request_id":"新的请求编号","mode":"retry"}`，沿用原始预算，不能夹带新的意见、基线、知识选择或预算字段。需要改变要求或预算时使用 `continue` 或 `fresh`。输入在开始时固定，不能修改进行中的角色输入。

幂等 `request_id` 与内容共同绑定。提交结果未知时，先只读核对列表或项目；需要重送时使用相同编号和原 JSON。网页保存尚未确认的原提交，只提供明确的“重送原请求”，不会自动换编号再开实验。

停止 POST `/projects/{project_id}/stop`，正文 `{}`；采用默认复现作品 POST `/projects/{project_id}/adopt`，正文 `{"candidate_id":"实际作品编号"}`。这两项均不生成新版本或发布 WikiSkill。实际 Agent 输入通过 GET `/projects/{project_id}/experiments/{experiment_id}/input` 查询。
