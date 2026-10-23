# Parse-only 真实浏览器集成结果（2026-10-22）

本记录对应 parse-only 实际上线后的新增集成验收；不替代上一轮 `RESULTS.md` 或前端构建测试记录 `PARSE_FRONTEND.md`。

## 最终结果

**最终完整运行 10 项检查通过**。浏览器实际加载 `index-Cb8shtpo.js` / `index-C0RgSHL_.css`，连接已有开发 API 镜像 `8148cd5182e7`。用户入口 `http://127.0.0.1:18505`，父任务 SSH tunnel managed job `b123a049ccfb404e` 保留。

Chromium 使用既有 preview5 镜像的临时 `--rm` 容器，加入现有开发 internal network；Playwright route 仅将浏览器 origin URL 转发到真实 API `ppt-service-webdev-api-20261021:8000`，保留 Origin，没有伪造 HTTP 响应。测试有且仅有一次明确的网络故障注入：对自动轮询 GET 执行 `route.abort('internetdisconnected')`，用来验证客户端恢复；它不是实际服务故障。

| 检查 | 实际观察 |
| --- | --- |
| 版本与门禁 | 实际加载 parse-only bundle；本地测试账户、正式 SSO 未接通、制稿执行器未接通和 PowerPoint 验收待完成均存在 |
| 旧材料持久化 | 已有项目 `3e70a772-268b-4151-b6b9-ac2a740904ad` 的 ready 状态和公开摘要真实可见，reload 后仍可见 |
| 唯一新项目 | POST projects 201；新项目及后续 GET 都来自实际数据库 |
| 一次网络故障恢复 | 第一次自动轮询明确注入断线，页面显示安全网络错误；下一个约 5 秒轮询 GET 200，显示真实 ready |
| 自动完成且保留输入 | MD 上传 201 返回 queued，DOM 显示等待；随后真实快照 ready，尚未提交的受众输入仍保留，保存按钮仍可用 |
| 纯文本摘要 | DOM textContent 与当前 API text_summary 的前 2000 字符逐字相同，实际 1635 字符；无 script/img 注入节点，window 标记未被赋值，无 progressbar |
| 新摘要刷新恢复 | reload 后同项目、ready 和同一实际摘要保留 |
| 损坏 PDF 失败 | 合法 `%PDF-1.7` 签名的损坏文件上传 201/queued，下一次约 5 秒轮询真实 failed / parse_error=parse_failed / 无摘要；页面显示安全建议，无路径/堆栈/凭据 |
| 制稿保护 | POST plans 503，显示执行器未接通；没有生成按钮或假成功 |
| 浏览器错误 | page_errors=[]、unexpected_console_errors=[]；只有注入断线的 ERR_INTERNET_DISCONNECTED 与预期 HTTP 503 两条 console 记录 |

本轮采样实际捕获 queued→ready 和 queued→failed。解析可能在轮询间隔内完成，本轮没有捕获 parsing/stopping 状态，不把这些状态算作实际端到端覆盖。

## 最终运行 fixture 与时间线

项目 `c4a0f28f-ee2c-412d-8ded-f6d99bca25ec`，标题 `PARSE-DOM-1792643168319903558`。

- MD `221b9547-0b89-4e19-8c58-d1a2f5d9a737` / `parse-dom-material.md`。
- PDF `ecc7bfb3-8b92-4c94-ac8a-7b3cd7b537d4` / `parse-dom-damaged.pdf`。
- MD 最近 queued GET 单调时钟 600049.573，注入一次自动轮询断线后 ready GET 为 600059.657；期间没有手动刷新或更改服务状态。
- PDF queued GET 600061.744，failed GET 600066.827，约 5.083 秒。
- PDF 实际显示文案：“资料解析失败，请检查文件内容或重新导出后上传。”

所有源资料保留，未删除已有或本轮测试项目。素材仅为本地集成测试公开字符串，没有执行外部模型调用。

## 前两次尝试与测试修正（保留失败事实）

第一次没有通过：见 `PARSE_BROWSER_FIRST_ATTEMPT.md`。新 MD 已上传 queued，但测试 route.fetch 遇到 ECONNRESET，测试转发器未结束该浏览器请求，35 秒等待超时。后续独立真实 HTTP 读取得到 ready。只读检查 API / coordinator 均 running、restart_count=0、OOM=false；没有据此声称解析器卡住。测试转发器随后加了异常处理：只记录异常类型，不输出请求头；失败时 abort 浏览器请求，让实际客户端收到网络错误并按原有机制重试。未更改产品重试代码。

第二次 fixture `db8cc578-e9c2-4541-b039-e3155e7664a6` / MD `12f184b5-c447-4943-8909-25c7a550c84c`。真实验证了注入一次断线后的约 5 秒恢复、queued→ready 及未保存输入保留，但测试错误地要求摘要仍含原 `<script>` 标签，因此该轮未全通过。实际解析器会移除 HTML 标签、保留普通文字；随后将测试修正为 DOM 与真实 API 摘要逐字比较，并检查无可执行节点与变量赋值。没有修改解析器或 UI，也没有要求服务还原标签。

最终完整第三次运行通过上述 10 项。没有将重试写成首次通过，也没有把故障注入写成真实服务故障。

## 范围、清理与复现

本轮只新增 / 修改 `web/integration` 测试和报告，未改产品源码、服务、网络、认证、数据库结构或协调器。因没有产品代码变更，未重复上一轮已经通过的 41 项前端测试 / 构建；那是 `PARSE_FRONTEND.md` 中独立的历史证据。

临时容器名 `ppt-parse-dom-smoke-20261022` 使用 `--rm`，每次同步运行均已退出；最终 `docker container ls -a --filter name=ppt-parse-dom-smoke-20261022` 只有空表头，确认无残留。没有新增 managed job；原 `b123a049ccfb404e` 隧道不由本子任务停止。

复现（仅在维持制稿 503 的专用开发站运行；每次创建新 fixture）：

```bash
scp integration/parse_browser_smoke.py root@192.168.137.101:/tmp/ppt-parse-browser-smoke-20261022.py
ssh root@192.168.137.101 docker run --rm --name ppt-parse-dom-smoke-20261022 --network ppt-service-webdev-20261021 --entrypoint python -v /tmp/ppt-parse-browser-smoke-20261022.py:/tmp/parse_browser_smoke.py:ro ppt-service-worker:arm64-c350bc31-preview5 /tmp/parse_browser_smoke.py
```

这是实际 API / DOM 行为验收，不是截图视觉验收，也不是 PowerPoint 人工验收、正式 SSO 或完整制稿验收。
