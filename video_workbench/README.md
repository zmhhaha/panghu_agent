# SVG / HTML 视频素材工作台

这是可运行的 SVG / HTML 视频工作台。文稿拆镜、单镜头生成和修订通过已有通用 Agent 服务执行；工作台负责项目、候选版本、明确采用、隔离预览与导出。音频制作增量支持中文配音、录音上传、试听采用、时长与字幕校正，以及带旁白的静态画面 MP4，见 [AUDIO_PRODUCTION.md](AUDIO_PRODUCTION.md)。

当前画面使用 SVG / HTML，配音调用独立内部语音服务；成片由静态渲染器与 FFmpeg 编码。MP4 不保留 CSS 动画，背景音乐与混音留待第二阶段。ZIP 导出继续包含已采用版本的 SVG / HTML 源码、manifest、预览页面和顺序审阅播放器。

本次音频增量位于独立工作树 `.video-audio-worktree`，分支 `codex/video-audio-production`；静态素材首版已在主线。以下“仓库根目录”指当前代码工作树根目录。

后续要做的事情见 [NEXT_STEPS.md](NEXT_STEPS.md)。

## 本地启动

在仓库根目录使用 Python 3.12 或更新版本：

```sh
python -m venv .venv-video
# Linux / macOS: source .venv-video/bin/activate
# PowerShell: .venv-video\Scripts\Activate.ps1
python -m pip install -r video_workbench/requirements.txt
python -m video_workbench --local
```

打开 http://127.0.0.1:8089 。`--local` 使用固定开发身份，只允许回环地址；默认端口为 8089。未配置 Agent 服务时，可创建项目和编辑镜头，生成操作会提示尚未配置。

如需演示完整界面流程，可显式启动离线 fixture：

```sh
python -c "import runpy,uvicorn; uvicorn.run(runpy.run_path('tests/video_workbench/serve_fixture.py')['app'], host='127.0.0.1', port=8089, proxy_headers=False)"
```

该入口使用 `tests/video_workbench/serve_fixture.py` 的固定示例，画面带有 OFFLINE FIXTURE 标识，不连接模型，也不代表真实 Agent 路由已经可用。不要和正常入口同时占用 8089。示例数据保存在 `.runtime/video-workbench/browser-fixture.db`。

## 连接已有通用 Agent

在启动工作台的进程环境设置：

| 变量 | 含义 |
| --- | --- |
| `VIDEO_AGENT_URL` | 通用 Agent 服务的完整 HTTP(S) origin，例如 `https://agent.example.com`；不带路径、查询或账号密码。 |
| `VIDEO_AGENT_TOKEN` | 专用于该业务调用方的服务凭证，由部署环境注入；与 `VIDEO_AGENT_TOKEN_FILE` 二选一。 |
| `VIDEO_AGENT_TOKEN_FILE` | 服务凭证文件的绝对路径，支持 Kubernetes 投影 Secret；与 `VIDEO_AGENT_TOKEN` 二选一。 |
| `VIDEO_AGENT_PROFILE_REVISION` | 视觉任务契约修订，默认 `1`。当前实现使用 `1`。 |
| `VIDEO_WORKBENCH_DB` | SQLite 数据库路径；默认 `.runtime/video-workbench/workbench.db`；容器默认 `/data/workbench.db`。 |
| `VIDEO_WORKBENCH_HOSTS` | 允许访问的 Host 名称，逗号分隔，不含协议、路径和端口；默认 `localhost,127.0.0.1,[::1]`。 |

启用连接器需要 `VIDEO_AGENT_URL` 加上一种凭据来源。URL、TOKEN 和 TOKEN_FILE 三项均未配置时，可以启动并手工编辑；只配 URL、只配凭据、同时配置两种凭据来源，或配置内容无效时，程序会在创建数据库前启动失败。连接配置和凭据只在启动时读取，更新环境变量或轮换投影 Secret 后，需要重启工作台。

工作台提交 `video.storyboard.v1` 和 `video.scene.v1`，任务只包含输入摘要，输入以 `video-input.json` / `video_input` 上传。完成结果必须通过真实任务、停止证明、结果 manifest 和文件摘要检查，随后再次验证输入绑定和 SVG / HTML 源码。

本次代码不会自动开启现网视觉路由。现有科研路由已接通，并不代表新增视觉路由已部署。上线还需在通用服务中配置视觉 task contract、`video-workbench-v1` 资源 bundle、工具环境及运行镜像 identity、execution binding、业务 caller scope，并完成对应组合的 acceptance。资源位于 `agent_service/resources/video-workbench-v1/`，校验清单位于其中的 `manifest.json`；工具仅为 read/write。未完成这些配置时，工作台显示视觉任务不可用，不会回退调用科研任务。

本分支在 `agent_service/deploy/platform/vault/agent-service-externalsecret.yaml` 新增了通用服务侧的视觉 caller token 引用。父仓库的规范清单 `vault/inventory/agent-service-externalsecret.yaml` 需要后续合并同步；当前从本分支发布视觉 Agent 时，应向[通用服务部署命令](../agent_service/deploy/README.md)显式传入 `--external-secret-file agent_service/deploy/platform/vault/agent-service-externalsecret.yaml`，避免自动优先选择尚未同步的父仓库清单。本次新增引用不创建工作台自己的业务 Secret；其投影可由下述部署生成器按需生成，从同一 Vault 记录读取 `VIDEO_WORKBENCH_SERVICE_TOKEN`。

## 服务器部署

默认入口采用身份代理模式。服务只读取可信代理提供的 `X-Forwarded-User`，本身不实现登录，也不校验该请求头的签名。身份代理必须完成认证，移除客户端自带的同名请求头，再写入稳定用户标识；工作台端口只能由该代理访问。配置 `VIDEO_WORKBENCH_HOSTS` 为用户实际访问的域名，代理保留该 Host，并使用同一 origin 转发页面和 API。不要把身份代理模式的端口直接开放到公网。

当前使用进程内协调锁和一个后台任务协调器，必须按单进程、单副本运行。同一个数据库不能由多个工作台实例共同调度；请勿增加 Uvicorn workers 或容器副本数。数据包括项目、候选、采用记录和任务提交状态，需持久化数据库及其 WAL 文件。重启后恢复已有任务时，提交状态不明确会按业务标识查询，不会盲目重复提交模型任务。

推荐使用白名单构建入口。可以先生成可审阅的 TAR；`--context` 的目标父目录必须已经存在，下面先创建目录：

```sh
python -c "from pathlib import Path; Path('.runtime/video-workbench').mkdir(parents=True, exist_ok=True)"
python -m video_workbench.build_image --context .runtime/video-workbench/context.tar
```

这一步只打包，不调用 Docker。构建器只读取 26 个明确列出的文件，覆盖工作台、所需的通用服务客户端和校验代码，以及经过摘要验证的视觉资源；不会把同目录下新增的配置、凭据、数据库或运行数据一起发送给 Docker。

准备实际构建和推送时，在 Linux 构建环境设置目标仓库、平台和对应的 Git revision，例如：

```sh
export IMAGE=registry.example.com/video-workbench:latest
export PLATFORM=linux/arm64
export SOURCE_REVISION="$(git rev-parse HEAD)"
python -m video_workbench.build_image
```

仓库地址是示例，需要替换。默认目标平台为 `linux/arm64`。`--no-push` 仅在本地构建，不保留发布回执；`--native` 跳过 buildx，使用 Docker 主机原生构建，`PLATFORM` 必须与该主机一致。这两个构建选项可以组合使用，但不能与 `--context` 同时使用。完整选项见 `python -m video_workbench.build_image --help`。

构建、推送和部署使用 `latest` 标签。只有本次推送成功并校验远端摘要后，构建器才写入 `video_workbench/rendered/workbench-image.txt`，内容为 `仓库:latest@sha256:实际摘要`。镜像 labels 记录源码、构建上下文和发布 revision；发布回执用于后续部署核对镜像内容。本地构建或失败的构建不能作为已经发布的证据。

使用 `python -m video_workbench.deploy.render` 生成内部 Kubernetes 资源，完整命令见[服务器部署说明](deploy/README.md)。将发布回执中 `@` 前的 `仓库:latest` 传给 `--image`，`@` 后的 `sha256:摘要` 传给 `--image-digest`。生成器保存 Deployment 和 Pod 的发布 annotations，默认只生成资源供审阅，不会自动应用到集群。它支持代理与 Agent 的命名空间和 Pod 标签、允许的 Host、存储类，以及可选的业务 `ExternalSecret` 投影；工作台投影对应的 property 为 `VIDEO_WORKBENCH_SERVICE_TOKEN`。

容器以 UID/GID `10001:10001` 运行，数据库及 WAL 文件位于 `/data`；使用已有卷或宿主机目录时，需要确保该 UID 可写。部署生成器配置单副本 `Recreate`、只读根文件系统、持久数据卷和小容量 `/tmp`。容器绑定 `0.0.0.0:8089` 并使用身份代理模式，需要通过受限内部网络交给登录代理访问。

`/healthz` 只表示应用进程能够响应；`/readyz` 检查 Agent 连接器已配置且本地数据库可读，未配置 Agent 或数据库检查失败时返回 503。两个端点均不要求用户身份，仍受允许 Host 校验约束；部署探针显式发送第一个允许的 Host。探针不会连接通用服务或调用模型，就绪状态不代表视觉路由、真实生成质量或耗时已经验证。ARM 服务器上的镜像构建和隔离容器验证已通过；正式部署和真实文稿试做仍待完成。

## 使用流程与边界

1. 创建项目并粘贴文稿，选择画面风格。
2. 请求自动拆镜，或手工创建和编辑镜头。
3. 为镜头生成 SVG / HTML 候选，在隔离预览中查看。
4. 根据说明修订候选；历史版本会保留，修订不会自动替换已采用版本。
5. 明确采用满意版本；每个镜头都有采用版本后，导出 ZIP 素材包。

每次生成输入上限 1 MiB，结果上限 2 MiB，单镜头源码上限 128 KiB；自动拆镜最多 12 镜，每镜时长 1..120 秒。静态源码通过保守白名单验证：允许本地 SVG 几何图形、文字、渐变，以及 HTML/CSS 布局；禁止 JavaScript、事件处理器、外部资源、远程字体、嵌入网页和 CSS at-rule。预览使用隔离 sandbox 和禁止网络的 CSP。顺序播放器用于审阅素材，不是视频编码器。

## 验证

依赖版本与本地验证环境一致。安装 pytest 后，可运行业务、视觉 profile 和注册回归，以及不需要第三方库的前端状态测试：

```sh
python -m pytest tests/video_workbench tests/agent_service -q --basetemp=.pytest-video-workbench
node --test tests/video_workbench/ui.test.mjs
```

已完成完整 Python 回归与失败修复后的定向复测、10 项前端测试、离线浏览器流程，以及 ARM 镜像构建和禁网容器验证。详细结果和镜像内容身份见 [VALIDATION.md](VALIDATION.md)。测试均未请求真实模型，真实生成质量、耗时与费用仍需联调确认。
