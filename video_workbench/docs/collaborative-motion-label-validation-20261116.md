# 协作作品动作编排标签修复：2026-11-16

源码 `eb9a97685d77bec860a992c4b0f7a5da017727c0` 已推送 `codex/video-capability-experiment-redesign`，未合主线。用户反馈最近三条作品缺少动作编排信息；实际源码均通过原生 JavaScript 的绝对时钟驱动动画，旧识别器只追踪直接函数调用，遗漏了工厂返回对象的方法接口。

修复增加有界词法调用与时钟参数传递分析，支持返回接口别名、Canvas 第二参数时钟、Three.js 属性更新和 SVG 聚合属性值。方法按所属作用域解析；同名方法、未调用函数、常量调用、未知接收对象、重赋值及局部遮蔽不借用其他函数证明。无法识别的语法继续保留未知。已渲染源码、媒体和原渲染操作的绑定要求不变，标签不代表视觉质量验收。

72 项 Python 动作证据及工具投影检查通过；三份真实作品源码的只读回归均通过。发布操作另有 10 项离线校验，只从推送后的干净 Git 检出正常构建执行器，使用 `latest` 标签并固定镜像摘要部署：

`arm-cluster-master:5000/video-animation-creation:latest@sha256:39140ac0f92848d143f08a98826349b6252386762286f8b09f8efe61825b0c90`

镜像离线导入、运行容器实际摘要、7 个相关源码哈希、Deployment 就绪及配置保持检查通过。Lab、Worker、HyperFrames renderer 未更换。

通过正常认证接口读取效果库及实际产物，以下三条的效果与最佳候选投影均返回 `native-js`／“原生 JavaScript”：

| 作品 | 画面创作 | 原视频 SHA256 |
| --- | --- | --- |
| Three.js：微型天文台的一次苏醒 | Three.js | `f329bffbd3f2790992da5f1343a23fec57efb87bbe8fe8fdac2085924f840d82` |
| HTML/SVG：负形中的信号接力 | HTML/SVG | `0c578e556873ebacd8997ae80b448101ecb9886f7e2e4ad2d3b274ed5992e910` |
| 雾退见山石：同一主体的水墨材质与空间层次 | Canvas 2D | `9b616266068b2013f50e20975910fc68fc56d7084eee12cfdbd428e25ce134b0` |

三条实际媒体及源码哈希均与原始保存结果一致。10 个效果及最佳候选身份不变，其余 7 条标签保持；当前共 9 条原生 JavaScript、1 条 Anime.js。未调用模型、重新渲染、改写历史、发布 WikiSkill 或改变水墨作品的质量结论。

本地证据：`D:/motion-label-object-interfaces-20261116`。服务器构建与部署收据：`/root/video-releases/collaborative-effect-20261116/motion-label-factory-release-eb9a976`。P.6 关键画面预检与独立的 WikiSkill 整库编译超时仍未解决。
