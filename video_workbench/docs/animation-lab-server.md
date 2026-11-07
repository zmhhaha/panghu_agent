# 动画开发者工作台的服务器部署

2026-11-07，分支 `codex/video-animation-lab`。独立实例、数据迁移和已有登录入口下的路径转发已部署完成。最终gzip版通过真实登录下的SVG、Canvas、Three.js、跨镜播放、报告读取、技术标注保存/刷新/撤回，以及同digest Pod重启后的持久化核对；真实集群安全边界和受信后端完整HTTP导出也通过。

随后已发布反馈迭代升级，当前运行源码为 `205b1ea4011d6c4b9a47417e57b666de6ac7b632`、镜像digest为 `sha256:36538111fac4003594b6c2313d8cf65fad6b05aeebc53cf8764b6d8317dbc2a5`。本说明以下保留首次服务器部署的原验收记录；后续版本、数据备份和实际交接验收见 [动画反馈到 GitHub 的开发迭代](animation-feedback-iteration.md)。

## 访问和用途

入口为 `https://video-service.panghuer.top/dev/animations/`，沿用已有Casdoor登录。只允许明确配置的开发者身份进入，普通视频制作界面不增加实验入口。十五个固定样例、A/B同步播放、动作检查点、标注、JSONL导出和已保存评测继续使用原工作台能力。

这次增加独立服务器实例，不把固定效果直接推广到业务视频模板。技术验收、开发者偏好和生产采用分别记录；服务器部署不代表这些效果已由用户认可，也不完成旧生产全面视觉闭环任务。

## 运行结构

公共请求经过现有VPS转接和OAuth登录代理。代理使用原生多路径upstream，为`/dev/animations/`增加独立动画工作台Service，原业务upstream仍为`http://video-workbench.video-workbench.svc.cluster.local:8089`。不增加额外网关。域名、Cloudflare和Casdoor回调沿用现有配置。

本次服务器部署显式配置固定HTTPS公共Origin、`/dev/animations/`前缀、监听地址和精确开发者allowlist。通用的显式代理模式也支持空前缀的根路径部署，但本次资源生成器和发布入口固定使用上述子路径；默认本地模式不会因此开放网络。应用只接受由受信登录入口清洗并设置的稳定用户标识；NetworkPolicy只允许指定代理连接。它不会从客户端自报的转发Host或协议推导信任。页面、媒体、历史与导出也检查开发者身份，不能只保护写操作。

写请求保留固定Origin、随机CSRF令牌、自定义标头、JSON类型和大小约束。缺失或重复身份头、错误Host/Origin和不允许的用户明确拒绝。健康探针仅返回不含实验数据的状态。本地默认模式继续使用回环监听，不能通过省略安全配置意外变成远程开放服务。

路径前缀适配发生在HTTP/UI边界，涵盖静态资源、API、预览、组合资源、报告、原帧、局部任务和导出。编译后的composition HTML和冻结样例身份保持原始字节，避免旧标注被错误迁移。

首次远程Canvas验收曾因约19.5MB的CJK字体在预览中重复下载，超过播放器20秒就绪期限；保留了`canvas-first-load-timeout.png`，不将首轮结果记为成功。第二版增加共享内容寻址资产与UI预热，但公网冷下载仍超过120秒，保留`font-warmup-120s-timeout.png`。最终版本增加gzip传输与600秒有界资源期限，已实际打开并播放四类代表性样例；原播放器20秒就绪期限保持不变。首次访问需等待固定资产下载和校验，常用资源随后由浏览器私有缓存复用。

共享资源方案在服务器代理模式把旧composition的三类固定资产请求以307跳转至已鉴权的摘要URL，本机模式仍直接返回原资源；目录响应副本附带预热清单，不改变冻结目录身份。UI完整读取资源并核对解压后原始长度及SHA-256，再加载原播放器；切换案例时取消旧预热并替换iframe，隔离旧消息。播放器、编译composition与资产原始字节、十五例身份和历史均保持不变。

仅共享固定资产的200响应可使用启动时预压的gzip传输；请求协商允许gzip时设置`Content-Encoding: gzip`及`Vary: Accept-Encoding`，显式`gzip;q=0`优先于通配接受值，未支持时返回原始字节。路径摘要和预热校验始终绑定解压后的原始内容，不把压缩结果当作新的案例或运行资产身份。API、报告、composition和其他资料不因这项传输优化改变缓存或身份合同。

最终服务实际核对三类资产的原始和gzip响应、显式拒绝、通配、重复拒绝及非法quality值降级，所有解压后SHA均与原资产一致。字体原始19,484,784字节，gzip传输15,518,082字节；首次远程下载仍有实际成本，后续复用浏览器私有缓存。

VPS仅为`/dev/animations/assets/<64位小写SHA-256>/`下的GSAP、CJK字体、Three.js三个精确文件名增加浏览器私有缓存例外，且要求上游200与精确`private, max-age=31536000, immutable`响应头同时满足。其他页面、API、媒体、标注、导出、登录跳转及拒绝响应保持`no-store`，共享代理缓存仍关闭。首次资产获取也检查登录与开发者身份。VPS已通过`nginx -t`并reload，其他站点摘要与原上传限制保持；原站点备份位于`/root/video-animation-lab-cache-20261107/`。例外规则的源配置与回退说明见外层仓库`vps/VIDEO.md`。

## 镜像、存储与迁移

工作台使用独立轻量镜像，只包含需要的Python依赖、工作台代码、已验摘要的GSAP/CJK字体/Three.js资源及许可证。构建白名单排除`rendered/`、数据库、临时证据、缓存和凭据。动画由访问者浏览器执行，服务器实例不安装或运行Agent、模型、TTS、编码器或采样浏览器。

镜像以`latest`发布，部署同时固定实际digest，并在labels/annotations保存源码和发布身份。采用单副本、`Recreate`更新、非root、只读根文件系统、drop ALL、禁止提权和禁用ServiceAccount令牌；设置资源限额及不调用模型的健康检查。

首次部署的最终ARM64工作台镜像已构建、推送并滚动就绪，两个PVC及已发布路径保持。当时实际验收版本为：

| 项目 | 实际记录 |
| --- | --- |
| 镜像标签 | `arm-cluster-master:5000/video-animation-lab:latest` |
| 镜像digest | `sha256:9719df446c4eb0223a22f5fd4fda0c905873078ee3e483bd526f094eab4fb176` |
| 构建源码 | `016f3d11861f116190fb54c47dacfc9a3b9803e7` |
| 构建上下文SHA-256 | `b602d6b54ce3160aeedb7ffa6d420dd95cdf9459326e6b0de03bbe558ceb6e13` |
| 镜像大小/架构 | `176,926,689`字节，`arm64` |

最终镜像与滚动回执保存在`server-deploy/transportfix/`。首版及120秒预热版的构建、滚动与失败证据继续保留，不能用当前镜像记录覆盖其历史结果。

标注SQLite保存在独立可写持久卷目录，冻结评测资料以只读目录挂载。不会读取业务项目数据库，不向此实例配置业务Agent或模型凭据。现有本地SQLite迁移使用一致性backup API，核对摘要、表/记录数量和冻结身份；原本地数据库与历史不删除。冻结报告和资源按记录身份及文件摘要迁移，不能用新编译产物冒充旧评测。

一致性快照已迁移到服务器，原本地数据保留。服务器核对20个冻结报告、1,796个文件与迁移清单一致；数据库120,418,304字节，SHA-256为`3b2352d7c26b9a640e16f090d6a21e430af8da9040cd99f9b78cabc3fe6954d7`。迁移基线为49条样例快照、9条标注、7条撤回、20条评测快照、3条分诊事件，各表计数均一致。服务器技术验收新增记录会单独注明。

部署前保存受影响入口配置、原目标及原镜像digest的非敏感回退资料。验收失败时可撤回新路径或恢复入口原转发；实验工具的问题通过其独立资源处理。

## 构建与资源生成入口

离线白名单打包器仅生成新的TAR，不下载、构建、推送或部署。以下命令从应用仓库根目录执行，输出路径必须尚不存在：

```sh
python -m video_workbench.deploy.build_animation_lab \
  --assets-dir video_workbench/rendered/feedback-20261106/animation-lab/assets \
  --output video_workbench/rendered/feedback-20261106/animation-lab/server-deploy/lab-context.tar
```

镜像定义为 `video_workbench/Dockerfile.animation-lab`，专用依赖为 `requirements-animation-lab.txt`。构建时必须传入公开的`SOURCE_REVISION`，使用`latest`标签；取得实际推送digest后才生成部署资源。发布验证不能用标签猜测运行的镜像内容。

资源生成器只输出JSON，不直接修改集群；下面使用已核对的发布标识、镜像digest和开发者稳定subject（不是显示名或自报邮箱）：

```sh
python -m video_workbench.deploy.animation_lab \
  --image arm-cluster-master:5000/video-animation-lab:latest \
  --image-digest "$LAB_IMAGE_DIGEST" \
  --release-id "$LAB_RELEASE_ID" \
  --developer-user "$LAB_DEVELOPER_USER" \
  --storage-class ceph-rbd \
  --output video_workbench/rendered/feedback-20261106/animation-lab/server-deploy/deployment.json
```

上述多行示例为Linux shell写法；PowerShell可写为一行并传入已核对的实际参数。新增输出文件采用排他写入，复测或重新发布应使用新文件名，不覆盖已有回执。

默认命名空间为`video-workbench`，Service和Deployment为`video-animation-lab`，内部端口`8766`。独立PVC为`video-animation-lab-data`和`video-animation-lab-evaluations`，默认各`2Gi`，分别挂载`/data`和只读`/evaluations`；渲染资产在镜像内的`/opt/animation-lab-assets`。默认入口身份来自`oauth`命名空间中标签`app=oauth2-proxy-video-workbench`的代理。实际应用前需核对生成资源与现有集群，迁移成功后再开放限定路径。

开发者准入由重复的`--developer-user`明确列出。修改允许身份应通过同一资源生成/发布流程，不能靠浏览器自报标头、共享业务账号或关闭身份检查临时放行。标注的新增可信身份记录在`client.forwarded_user`；旧`reviewer`和历史事件保持原格式，避免改写以前的来源。

## 回退

首次部署的入口配置备份位于服务器私有目录 `/root/video-process-20261105/feedback-20261106/animation-lab-server/75e50604ac8a28b0b58ece9daff92904761b9b9d/`，包含原OAuth Deployment、其NetworkPolicy和原业务Deployment。该目录不通过工作台提供下载。

撤回开发者入口时，应先读取当前配置，使用带现值校验的增量修改，仅移除本次新增的`--upstream=http://video-animation-lab.video-workbench.svc.cluster.local:8766/dev/animations/`、对应发布注解及8766端口的专用出站规则，保留原业务upstream及其他配置。核对OAuth代理滚动完成和业务入口可达后，必要时将`video-animation-lab`缩容为0；两个PVC和原本地数据均保留，不随回退删除。不要在有其他并行改动后直接覆盖整份旧代理配置。

未来升级时保留前一个实际digest、发布资源和一致性数据备份。镜像回退使用已知的`latest@sha256:...`精确内容，不能靠重新拉取可变`latest`恢复旧版本。

## 实际验收进度

源码检查与真实服务器/浏览器检查分别记录。最终代理边界与引擎回归76项Python通过；部署及效果集成83项在短临时目录通过；最终gzip前端回归17项通过，此前完整前端回归192项通过；VPS回归8项通过。这些分组有不同覆盖范围和运行版本，不相加为全量计数。早先Windows临时目录权限/长路径造成的fixture失败保留原结果，相关测试以短目录复跑，不能把运行环境错误改写为最初已通过。

真实已登录浏览器已读取15个固定样例与20个冻结评测的目录，实测以下代表案例。时刻为界面和播放器实际读数，用于说明播放控制已生效，不代表人工审美批准或逐帧质量全部检查。

| 代表能力 | 已完成的真实操作 |
| --- | --- |
| SVG复杂架构 | 播放推进到12.715秒，同步定位9.500秒 |
| Canvas请求队列 | 播放推进到10.368秒，定位2.125秒，归零后下一帧0.042秒 |
| Three.js分层系统 | 播放推进到17.815秒，定位9.360秒，末帧17.958秒，再归零 |
| 跨镜保留对象 | 在9.000秒边界定位后播放至共用时钟18.625秒；较短/较长版本分别停在17.958/18.583秒，核对9.600秒承接检查点 |

对应截图为`server-{canvas-midpoint,three-focus,svg-midpoint,continuity-boundary}.png`。冻结报告`architecture-expand-effects-r1`已用真实登录打开，其12幅DOM图片均完成加载且原始尺寸1280×720，保留`report-browser.txt`；它仍显示原始技术检查和未调用视觉模型状态，没有重写成模型或审美批准。

技术标注通过浏览器保存，并在换到最终`016f3d1`版本的新Pod后刷新读到，记录见`annotation-before-restart.txt`。随后保持同一`latest@sha256:9719df44…`版本，将Pod从`video-animation-lab-7cfd645f84-ngngp`替换为`video-animation-lab-6b7556bf58-f97n2`；新Pod就绪后，真实浏览器刷新仍能读到测试标注，Canvas两版均就绪，见`annotation-after-restart.txt`。标注ID为`8cb69824-1921-4f70-b034-8d92d333196e`，对应`canvas-queue`的candidate及冻结身份`7005051280cd42ff7a43c87ba2060b3110ffe31156c69fff88f47f0166e55187`，服务端完整导出也核对其快照身份匹配。

完整JSONL的验收采用受信后端实际HTTP读取全量响应并核对SHA-256及行数；登录浏览器核对导出链接的同源与`/dev/animations/`前缀。没有在公网另行下载约120MB全量文件，不把后端全量读取记为浏览器下载。同digest重启后、撤回技术标注前，实际HTTP返回200、120,187,856字节、82行，包含49条case_snapshot、10条annotation、20条evaluation_snapshot及3条evaluation_decision；SHA-256为`ff7e0fcf67b1da7da0d5a965175f608150336c6e42f11eee14fe1fdd2cf000ef`。此摘要对应撤回前的该次响应，不作为随后状态变更后的最新导出摘要。

上述标注文字明确说明仅为部署技术测试，不作为画面效果采用依据；最后通过真实浏览器只撤回该技术标注，页面显示成功状态与“已撤回 · 仅保留历史”，见`annotation-retraction.txt`。原始记录和已有历史保留，没有写入A/B偏好或审美批准。

最终`016f3d1`/`9719df44…`镜像在重启后的Pod完成14组后端检查，正式`security-acceptance.json`为passed：身份缺失/错误/重复/合并、错误Origin、缺少CSRF均拒绝；允许身份可读取15例和20个冻结评测；共享资产拒绝未认证或错误摘要；三类资产协商与完整导出通过。实际UID/GID10001、根只读、data可写/evaluations只读、两PVC Bound、无业务/模型凭据、无ServiceAccount令牌；NetworkPolicy只允许OAuth代理入站并拒绝出站。非受信Pod即使伪造身份也直访超时，控制连接可达，临时探测Pod已删除。公网匿名伪造身份访问固定资产仍302到`auth.panghuer.top`且`no-store`。后端身份矩阵使用直接Pod请求构造标头，未使用第二个真实Casdoor账号；实际现有登录的浏览器操作另有回执。先前`b4aa229`版安全回执保留为`security-acceptance-warmfix.json`。

部署验收覆盖清单：

- 服务器配置、开发者allowlist、Host/Origin、CSRF、伪造/重复身份头及路径边界的针对性测试。
- 十五个固定样例和原冻结历史的精确身份保持，打包白名单与部署资源检查。
- 实际集群健康、未登录及非开发者拒绝、可信入口清洗身份头、网络策略与业务入口仍可达。
- 登录浏览器核对目录及代表性SVG、跨镜、Canvas、Three.js播放，执行同步定位和回退。
- 明确标为技术测试的标注完成保存、刷新保留、完整JSONL导出和撤回，不添加审美偏好。
- Pod重启后核对原记录、绑定身份和导出持续存在。

真实失败保留原回执，不改写成成功。最终浏览器回执`browser-acceptance.json`和正式集群回执均为passed；本轮未调用Agent、模型或TTS，未改业务项目数据、DNS或Casdoor回调，没有自动合入主线、apply或archive。

## 记录位置

OpenSpec变更为 `deploy-video-animation-developer-lab`，10项均按实际回执完成，revision为`74e69ac947c69e75f2b636c61bc8c320918e58cb`。CAS更新后再次validate及get读回通过，0项剩余、无校验问题；已区分受信后端全量HTTP导出与浏览器链接检查，并记录共享资产、gzip、600秒资源期限和原player20秒合同。此前本地样例库已完成的记录单独保留；部署代码继续在开发分支审阅，没有自动合入主线、apply或archive。

所有非敏感部署与验收证据统一放到既有目录下的 `video_workbench/rendered/feedback-20261106/animation-lab/server-deploy/`，最终摘要为`closure-summary.json`，OpenSpec完成及读回回执为`openspec-server-lab-completion-receipt.json`和`openspec-server-lab-readback.json`。标注数据库、完整导出、迁移备份和冻结运行资料属于长期实验数据，按既有整体清理计划保留，不能作为临时截图随手删除。
