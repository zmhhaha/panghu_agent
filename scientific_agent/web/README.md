# 科研工作台前端

React / TypeScript 独立前端。所有生产数据来自同源 BFF，不含演示项目或模型连接配置。

```sh
npm ci
npm run build
npm test
```

`dist/` 由 `scientific_agent.workbench_server` 提供；生产容器构建时安装依赖并生成静态文件。
开发时 `npm run dev`，Vite 仅将 `/api` 转发到本机 `8000` 端口。身份应通过现有 SSO/BFF 注入，
不可把服务凭据或模型 Key 写入前端环境变量。

启动读取 `/api/v1/research/me`，仅在内存保存返回的 CSRF token。写入同时发送同源凭证、
`X-CSRF-Token`、`Idempotency-Key` 与已读取的项目修订。任务状态从持久项目恢复；请求超时
不会自动重发任务 POST。旧版报告通过现有只读 `/scientific-reports` 等接口查看。

Windows 如系统临时目录的短路径 ACL 使 esbuild 清理临时文件失败，可在当前构建进程中
将 `TEMP`、`TMP` 指向工作目录内的临时目录；无需改动系统配置。
