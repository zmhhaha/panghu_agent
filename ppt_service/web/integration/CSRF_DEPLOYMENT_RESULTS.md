# 规划兼容 / CSRF 静态启用与真实浏览器回归（2026-10-22）

用户可操作 URL：**http://127.0.0.1:18505**。

本记录是后续实际启用结果，补充 `PLANNING_COMPATIBILITY.md` 中构建时尚未部署的历史状态。没有启用规划 / 制稿执行器，没有将离线规划 DTO 写入 live DB。

## 静态包实际启用

执行前只读确认 owned API 正在运行，label `app=ppt-service-webdev`，镜像仍为 `8148cd5182e7`，IP `172.18.0.3`。读取当前入口及引用资源 SHA-256，然后将整个旧 dist 复制到独立版本目录：

`/root/ppt-service-webdev-20261021/source/ppt_service/web/versions/before-planning-csrf-C66NMsHg-20261022`

验证备份入口哈希等于旧入口后，新增新哈希资源，保留旧哈希资源，最后使用文件替换原子更新 index。只更新已存在只读静态挂载背后的 verified 目录，没有重启 API、重建镜像或改动挂载设置 / PG / materials / 网络 / 认证。

| 实际 HTTP 资源 | SHA-256 |
| --- | --- |
| 新 index | f136015b22c75b4e3f18a540f72a67c7fda46aeeb35a0e8853ed5448c0075e41 |
| index-C66NMsHg.js | dae3182e070ee2b0d2a2d6584df13f8a4bfa67ab77b43f483fbf4679e00c888f |
| index-C0RgSHL_.css | 119e63d6513ad2170415b348083b66f11a4ee3f3d52409fbc9a8eab2c8999e0d |
| 保留的 index-Cb8shtpo.js | 765a30838a5e1371b7a97116986ea903ce611d08aa29fe05962ffe7baf4557c9 |

旧入口 SHA-256 为 `67651824d93cc5259ece8cb4abad20c91a210550bf10082e70a485bed6c8d720`，在独立备份中保持一致。浏览器实际新资源哈希与部署后文件哈希相同；旧 JS 实际 HTTP 200。

## 真实浏览器：10 项检查通过

浏览器使用既有 preview5 镜像中的 Chromium 临时容器，真实 route 转发到同一开发 API，保留浏览器 Origin；没有 mock HTTP 响应。本轮没有故障注入。只在内存比较 /me 真实 csrf_token 和实际浏览器写请求头，记录匹配布尔值，不输出 token、Cookie 或请求头。

1. 实际加载新包哈希、/me 返回真实 token，开发账户 / 正式 SSO 未接通 / 制稿未接通 / PowerPoint 待验收提示仍存在。
2. 已打开标签页所需旧 JS 保留，实际 GET 200。
3. 旧项目 `3e70a772-268b-4151-b6b9-ac2a740904ad` 的 ready 材料及公开摘要保持可见。
4. 新建唯一 fixture，实际 POST project 201，CSRF 匹配 true。
5. 设置保存实际 PATCH 200，CSRF 匹配 true。
6. 明确同意后 acknowledgement PATCH 200、multipart upload 201，均 CSRF 匹配 true；实际快照 queued→ready，DOM 摘要逐字等于 API 文本。
7. reload 后保留所选项目、已保存设置、ready 文件与摘要；localStorage 只有允许的 UI 选择 / 游标 key。
8. 无真实方案时保持空态，实际 POST plans 503，CSRF 匹配 true；没有方向单选、生成按钮或假成功。
9. 本轮 5 个实际写请求的 CSRF header 全部与最近 /me token 匹配 true，未输出令牌值。
10. page_errors=[] / unexpected_console_errors=[]；唯一 console 是预期规划 HTTP 503，无转发异常。

本轮 fixture：项目 `4f3a7c9c-cedb-427e-8abd-ebc1b8c3054a`，标题 `CSRF-DEPLOY-DOM-1792648385744590864`，材料 `csrf-deploy-material.md`。没有删除任何新旧资料。

这证明开发身份下前端真实 token 传递与真实持久化 / 解析行为，不代表正式 JWT / SSO / 反向代理 CSRF 全链验收。真实规划确认、正式生成、视觉验收和 PowerPoint 人工验收仍未通过本轮验证；没有为测试种入假规划。

## 真实后端 DTO 离线补充测试

额外读取父任务指定输出：

`/root/ppt-service-webdev-20261021/planning-compatibility-20261022/output/stage2-api-plan-unconfirmed.json`

仅下载到 `src/test/fixtures/backend-stage2-api-plan-unconfirmed.json`，SHA-256：`abdcaac3da5648596edfae82773c7a0fe3b5a6fc60833e717b64fee547968bfd`。它来自后端独立测试 namespace，不是 live 真实用户方案或历史接受基线。

新增 focused 测试 `src/test/backend-planning-contract.test.ts`：**1 test passed，1.58 秒**。核对三个真实 ID、六页完整页纲、note / production_parameters，确认 DTO 的 confirmed_at / confirmation_selection 均为 null、推荐不构成选中。没有修改产品源码，因此不重复原 49 项全量测试或重新构建；本次实际部署的仍是此前已经通过 49 项与构建的 C66NMsHg 静态包。

## 服务及临时资源

最终只读确认：API 与 materials coordinator 均 running、restart_count=0、OOM=false，启动时间分别仍为 2026-10-22T04:02:27Z 与 04:07:42Z。IP 没变，无需调整父任务 SSH managed tunnel `b123a049ccfb404e`。

临时浏览器容器 `ppt-csrf-deploy-dom-20261022` 使用 --rm 同步执行并已退出；最终 docker ls 过滤结果只有空表头。没有新增 managed job。没有模型请求、收费执行、服务重启或认证绕过修改。

复现浏览器（仅制稿 503 的专用开发站；每次新增一个公开 fixture）：

```bash
scp integration/csrf_deployment_browser.py root@192.168.137.101:/tmp/ppt-csrf-deployment-browser-20261022.py
ssh root@192.168.137.101 docker run --rm --name ppt-csrf-deploy-dom-20261022 --network ppt-service-webdev-20261021 --entrypoint python -v /tmp/ppt-csrf-deployment-browser-20261022.py:/tmp/csrf_deployment_browser.py:ro ppt-service-worker:arm64-c350bc31-preview5 /tmp/csrf_deployment_browser.py
```
