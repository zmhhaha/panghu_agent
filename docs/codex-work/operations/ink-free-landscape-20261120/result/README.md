> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-free-landscape-20261120/result/README.md`；原文件 SHA-256：`707bd6198f545e8e9685bab700e04194c7b15a072c024107613af63d5fdc03a2`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

# 雨霁江渚

1600×1000 原创代码山水画。以“雨后空山、静江孤舟”为文字意境，不输入、描摹或复用任何参考图片。

- `rain-cleared-river.png`：实际渲染的完整图片。
- `painting.html`：无需联网的单文件预览，直接用浏览器打开。
- `scene.js`：可编辑的构图、原始笔路、种子和笔墨参数。
- `ink-landscape.mjs`：固定版本的开源绘画库；MIT 许可见相邻 LICENSE 文件。
- `manifest.json` / `rain-cleared-river-receipt.json`：源码与输出摘要、库版本及实际函数执行记录。

笔墨使用真实 ink-landscape 的 createBrushDocument / renderCanvas；纸色、柔润山体、江雾与合成由原生 Canvas 2D 绘制。PNG 使用 Skia Canvas 2D 渲染；浏览器字体与渲染器可能带来细微差异。

在 scene.js 修改后，可通过普通本地静态服务器运行模块页面，或重新打包为 painting.html。现有 painting.html 已把代码与库放入同一文件，没有远程脚本、图片或字体依赖。
