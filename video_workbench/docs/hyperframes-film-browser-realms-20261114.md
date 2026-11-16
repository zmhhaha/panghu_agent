# 整片 HyperFrames：独立模块 realm 与真实浏览器技术验证

2026-11-14。承接 [整片包合同](hyperframes-film-project-contract-20261114.md) 和 [生命周期宿主](hyperframes-film-lifecycle-host-20261114.md)。本阶段实现可运行的专用浏览器加载器，仍未接正式业务／新 Agent profile／整片媒体导出，不修改旧 creative、engine guard、native 路径、部署或模型配置。

## 实际执行链

```mermaid
flowchart LR
  P[冻结原始工程包] --> V[Python 独立重编译校验]
  V --> B[冻结画幅／根配置／可信宿主字节与 SHA]
  B --> S[仅 loopback 的资源白名单]
  S --> R[可信根页面与真实 HyperFrames core]
  R --> C[同一个全局帧／waitUntil]
  C --> A[每次定位创建新的 opaque iframe]
  A --> M[原始模块 ESM 与独立缓存／全局]
  M --> O[私有 MessageChannel 返回完成与观察]
  O --> F[等待所有模块后捕获整片画面]
```

父页面只运行可信 root、生命周期调度和校验过的 HyperFrames core，拥有全片时钟。每个活跃 scene／transition 在每次 seek 时获得全新的 `sandbox="allow-scripts"` iframe，不授予 allow-same-origin；全局对象、ESM 实例／缓存与前次定位隔开。Agent 仍直接编写 HTML/SVG、Canvas2D 或 Three.js，宿主不生成绘画模板、不改原始源码。

转场是在同帧中独立加载的透明覆盖层，只能使用结构化状态与自己的源码；不能访问父页面或相邻模块 DOM。模块必须据绝对全局帧重建状态。这是可执行的组合约定，不保证旧 v5 源码自动适配；新模块创作角色与正式工作流还待接入。

## 冻结配置与资源准入

`hyperframes_film_browser.py` 先调用现有 `verify_film_project` 完整复核工程包，再冻结 320–1920 × 180–1080 画幅、根入口、realm 协议、project manifest SHA、实际 core SHA，以及六份专用 host 文件 SHA。生成的 root／realm HTML 只搭建执行环境，不含业务动画。

浏览器 bundle 保存所有原始工程字节和完整 WAV。录音标记为不向浏览器提供，后续媒体编码／混音另行处理。`verify_film_browser_bundle` 要求外部可信 bundle SHA，核对文件并逐字节重建整个 bundle；原包／新 root 配置不得随执行时的“最新版”变化。

Node reader 检查外部 bundle SHA、精确清单、资源字节及 SHA、普通目录／祖先／文件无 symlink、实际执行 adapter 文件 SHA，并限定预算。资源 server 仅绑定 127.0.0.1 的临时端口，HTTP 和 Playwright route 同时要求 GET、同一 origin、精确已声明路径、无 query；未声明文件、远程资源和录音不可获取。

子 realm 的 CSP 只准本模块脚本／样式和声明资产；CORS 仅提供 opaque 模块加载所需的有限本地资源。worker、media、嵌套 frame、object、表单与 base 变更被禁止。父根页面不加载 Agent 代码。

这些都是执行输入的身份检查。调用者必须从可信发行版本选取 host 字节，并把外部 bundle SHA 冻结进可信执行输入；让不可信调用者任意选择 host_files 或自报 SHA 不能授予准入。当前真实验证使用既有镜像依赖加只读的新宿主覆盖，不能冒充新宿主已成为 image-owned 生产发行或已获准入。

## 通信、完成与专用 guard

realm driver 在 Agent import 前与父页面建立一次性的私有 MessageChannel。父页面核对 `event.source`、opaque origin 和会话 token；端口只在可信闭包中保存。mount／render／dispose 逐条等待返回的 Promise，回包始终绑定确切 module_id 与 module SHA。

`hyperframes_film_channel.mjs` 对失败回包同样保留身份；正常生命周期错误立即拒绝对应 pending，错误身份／协议拒绝全部 pending 并停止通道。不会把 import、mount、render 或 dispose 的真实错误拖成操作超时。

新的专用 guard 在 import 前捕获 Reflect.apply、数值／字符串／正则原语、原生 Canvas／WebGL／DOMRect／CSS／Animation getter、Set 操作等；context 内部用无原型字典，避免 Agent 修改 Array.prototype.map／Function.prototype.call 伪造观察。通信也使用捕获的 Reflect.apply，context 使用捕获的 structuredClone／keys／freeze，不调用可污染的 toJSON。

guard 记录实际 2d／WebGL2 context、连接／可见尺寸、SwiftShader 和 GL 状态，拒绝不符合声明的能力、危险 DOM、活动 CSS 动画和尚待执行的 RAF。截图后另等 100ms 并逐字节比较，最后在 idle 与清理之后再检查页面错误和被阻断请求。

await 只等模块确实返回的 Promise；若源码启动异步任务后提前返回，不能据此声称任务完成。有限 idle 比较／RAF 观察也不能穷尽所有未来定时器、墙钟或随机行为。这些专用检查和负向回归不等于完整的任意恶意 Agent 沙箱认证；后续仍需独立浏览器观察、运行预算和终止证明。

Playwright 的 `serviceWorkers='block'` 辅助初始化会在 opaque realm 读取不可用的 navigator.serviceWorker，触发 SecurityError。本专用 context 在任何脚本前移除这个 accessor，并结合 opaque origin 和 `worker-src 'none'` 保持禁用；旧 guard 不变。根与子 realm 均记录 service worker 未暴露的实际状态。

## 真实 r4 技术证据及其版本边界

使用实际 renderer 镜像 `sha256:59ee5c356f4c742e515385db4e2b315c9393641bea7d76136f095b27214e79ca` 的既有依赖、Chromium headless shell 和 HyperFrames 0.8.149 core。core SHA 为 `675e02ee21f31540e3a6abaec061f86cea92a0178ae67a390700acb4f801dbbc`。

r4 bundle SHA 为 `b8032f268558e26863f4044d09a39378a4d98d5b2f6337e731acf9f2cda7f49a`。三个静音技术场景分别画 SVG、Canvas 和 Three.js，另有一个独立 SVG 转场，不是 Agent 创作效果或可复用动画模板。

- 对 `[0,23,24,46,47,48,49,50,71,0,48,48,24,71]` 进行了 14 次真正 core 定位。
- 46–49 帧中 Canvas、Three 与转场同时存在，使用同一 global_frame；原生 2d 与 SwiftShader WebGL2 均有记录。
- 首次／反向／重复的相同帧直接比较完整 PNG 字节，不只是打印哈希；全部一致。
- 模块执行检查 opaque origin、父 DOM 不可访问、模块 global import counter 每次为 1；根和子 realm 的 service worker 均未暴露。
- 0 页面错误、0 被阻断请求；关闭后 iframe、live_roots、pending_cleanup_modules 均为 0。容器已自动移除并释放服务器。

运行硬上限为 1GiB 内存、2 CPU、128 PID、180 秒；无网络、只读输入、独立工作目录、无生产卷／ledger／Docker socket，未构建镜像或调用模型。

服务器证据目录为 `creation-product-r1/hyperframes-film-realm-probe-r4/`。本地只读副本位于 `D:/github/armbianbegin/.research-temp/video-agent-development-20261112/hyperframes-film-realm-r4-evidence/`；completed.json SHA 为 `8ef36f3fd4eec1b9ebb66c3b2f9124657fd6fbd5045ad0c7eed7464cf37effdf`。已实际查看 47／48 帧，两镜与覆盖层均可见。

r1 输出权限问题、r3 诊断目录权限问题、r2／r3b 的 Playwright opaque serviceWorker 错误都保留在 `hyperframes-film-realm-probe-r1/` 的分开日志／工作目录；不合并为一次成功证据。

r4 早于随后修复的 RPC 身份错误和 guard 原型污染防护。这份正例证明当时自有技术 fixture 的真实执行，不证明修后新 bundle 或任意 Agent 源码已准入。修后版本按新的 bundle SHA 做了下面的独立矩阵，没有借用 r4 的身份。

## 修后版本：固定十次真实浏览器矩阵

H2 释放窗口后，只执行了一轮、一个 context、最多 10 seek／180 秒的固定矩阵；镜像和 core 与 r4 相同。正例对 `[0,24,48,48,0]` 完成真实定位、三引擎／转场观察、100ms idle 和重复 PNG 完整字节比较。

| case | bundle SHA | 结果与该 case 实际耗时 |
| --- | --- | --- |
| positive | `7963f8683c2d214315ef65b220d255dbc35fad0fd3d5830d0253cffc031a3447` | 五次定位通过，3181ms |
| import | `9857fcc629a1ffd514f3774b2f891b0c97a70122744c57e47503045be76d0e2a` | 原始 fixture_import_failed 返回，117ms |
| mount | `747e5ff85678a3bda78b732a4cfb971715ce0d028a770f8219ea04bbe0fd0e0b` | 原始 fixture_mount_failed 与部分 mount 的 dispose 错误一并保留，100ms |
| render | `5ebd3b56207c61f795d24a698272cad92d8d10a66943866f73a09c74e523cc99` | 原始 fixture_render_failed 返回，115ms |
| dispose | `62f8d4dbf1a6c926e1ca1436fdada5ad25748833ab85e76bd687c1756d6bb168` | fixture_dispose_failed 保留在 AggregateError，98ms |
| prototype_without_context | `d2157aa5a7b34cc9071089cd3a4ac808585ee095cd03c51fef747ac48793b587` | 没有真实 context 时污染 Array.map／some 与 Function.call，仍被 native_context_missing 拒绝，88ms |

所有负例都在 15 秒操作超时前保留原始错误，且没有把失败帧记录为成功。dispose 负例明确留下一个未完成的逻辑清理项、status=failed，同时 iframe／live_roots 已为零；没有伪造 dispose 成功，随后关闭整个 page／context 完成物理隔离回收。最终 0 页面错误、0 被阻断请求、0 remaining_pages，容器自动移除，未追加重跑。

服务器目录为 `creation-product-r1/hyperframes-film-realm-matrix-v1/`。本地输入／runner／证据分别为同一 scratch 下的 `hyperframes-film-realm-matrix-v1-input/`、`hyperframes_film_realm_matrix_v1.mjs`、`hyperframes-film-realm-matrix-v1-evidence/`；completed.json SHA 为 `e483bf8316b7487138fb877a493339c7e8549908f18df53b9af2b935b4ed5177`。

本地最终回归为 94 项 Python（24 项新 bundle／fixture，70 项已有整片包）与 62 项 Node（23 项新资源／RPC／guard，39 项已有宿主）全部通过。实际源码 bytes、host 文件和每份新 bundle 身份保存在 input-manifest，不能将这个有限技术矩阵扩写成完整任意源码准入或真实 Agent 作品验收。

## 下一步与明确未完成项

本地回归覆盖包重编译、尺寸／身份／路径和字节篡改、RPC 原始错误与坏协议停止，以及没有真实 context 时污染 map／call 等仍被拒绝。有限的正反向真实浏览器矩阵已完成；后续整片导出须另冻结预算与具体来源，不能无限重开这轮测试。

全片新 Agent 角色／Skill 创作接入、实际旁白／必要音效、多轨混音与原 PCM 时钟、正式 capture／独立 receipt、新宿主发行准入、业务编排、取消恢复和完整影片交付仍待完成。每次每模块重建 realm 成本也尚未在长片量级测量。本实现没有把旧逐镜 MP4 拼接标记成 H.3 完成。
