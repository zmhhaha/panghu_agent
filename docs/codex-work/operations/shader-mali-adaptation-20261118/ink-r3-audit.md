> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/shader-mali-adaptation-20261118/ink-r3-audit.md`；原文件 SHA-256：`148b6c77efe92d4ec285ca207c1a71d01d77324b11aae09d8d1d12d4ae3e7461`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# 水墨 r3 独立复核

2026-11-18。只读审查既有r2/r3源码、捕获宿主、guard、回执和图片，并精确查询r3容器/旧GPU进程状态；没有启动新渲染。原始r3文件未改动。

## 结论

**实验性Mali + HyperFrames单帧执行证据成立；shader存在可测贡献，但正常观看下差别微弱，不能据此宣称显著提高水墨质感。正式SH.6两链准入仍未完成。**

## 已核实

- r2/r3 `source.json`逐字节相同，SHA256均为 `f0e131fe8f02550d1780e205ef6adbe410b56cc91925a1ce5d9d128556deda4e`。冻结艺术源码后更换执行后端的要求成立。
- r3回执中的source/request/host/guard四项文件SHA，及still/without-shader PNG的SHA均与实际文件一致。
- 候选guard身份为 `animation-engine-runtime-mali-g24p0-candidate-1`，从初始化时捕获的原生GL方法取证，要求renderer包含Mali、vendor包含ARM，software=false、context_lost=false、gl_error=0；未把软件guard简单删除。宿主使用该独立文件，不修改正式guard。
- 宿主调用checksum校验的HyperFrames core（0.8.149，SHA `675e02ee21f31540e3a6abaec061f86cea92a0178ae67a390700acb4f801dbbc`），真实绑定player.renderSeek；首次seek回执core_seek_count=1、actual_seconds=0。legacy bridge是在真实core事件后调用Agent seek，不能描述为未使用core。
- 场景HTML只有一个连接且可见的1024×640 `#painting` canvas，无额外DOM叠层。17个Canvas2D上下文均为分离的离屏层；源码将完整底图上传为纹理，用全屏三角形绘到该WebGL2 canvas。宿主对其完整宽高readPixels，不是截取某个局部部件。
- 首帧、重复seek、reload后产物SHA均为 `705b717f2f11498e872ff1588bc5c8f388627feac6ded35a4d53eefc4f5be244`。消融在同一源码/同一页只令uStrength=0，随后仍走HF seek，PNG SHA为 `0f05f2ce009829a45ac23d26fdc6f4ae2f560eb95156f61bda69668efa574a7c`。
- 真实renderer为 `ANGLE (ARM, Mali-G610, OpenGL ES 3.2)`，vendor `Google Inc. (ARM)`，无软件后端、GL错误或context丢失。外部回执采样GPU进程374194的FD13指向 `/dev/mali0`。
- 使用既有固定digest镜像 `sha256:45504cc19d0ce1a0dd2234a47ac953afcb957bb1b92bbeaf63356610f641f13b`，仅Mali设备与独立只读库目录，rootfs只读、非root、无网络、2CPU/1536MiB/150秒边界。回执ExitCode0、OOMKilled=false、约7.14秒、清理后容器不存在。
- 复核时再次精确查询：r3容器 `ink-shader-keyframe-20261118-r3` 不存在，`/proc/374194` 不存在，其原Mali FD不再存活。不是GPU全局空闲声明。
- 复核时共享libmali实际SHA仍为 `07cc993b13d6591161b0a12a270c75e06f25d56437016c6fbff8ea36e47c6614`，三个版本链接仍指向libmali.so。其闭源来源/许可由本轮硬件报告保存；这次查询不冒称是r3运行前后的历史校验。

## 视觉判断

直接查看 `r3/still.png` 和 `r3/without-shader.png`：整体构图、山峰形态、明暗与纹理组织几乎相同。shader主要改变细微纸纹/墨纤维，未改变底图匀密纵向纹、形态单一或前景右端垂直切口。这些问题由Canvas底图生成规则主导。

对实际PNG独立重算：平均绝对RGB差 `[0.7251, 0.7042, 0.6750]`（0–255）；任一RGB通道差≥8的像素为11,715/655,360，约1.79%。最大RGB差分别75/71/68仅说明局部差异，不证明改进。放大差分图不是正常观感，不能作为质量提升的替代证据。

## 证据完整性缺口，留给后续身份补齐

1. r3执行回执只保存库挂载路径，未自行保存当次libmali前后SHA、实际GL_VERSION/CDP driver版本。g24归属依赖此前独立probe与共享目录连续性；后续记录应自包含，勿回写r3历史。
2. reload确实调用seek且PNG一致，但receipt保存的是install时 `reloadedRuntime`（core_seek_count0），丢弃reload后的seek返回值。后续应保存 `reloadedSeek`，避免读者误解。
3. stdout摘要frames_captured=1，完整receipt=4；一个是主要成品，一个是技术捕获次数，字段口径应明确。
4. source.engine=three是现有准入分类；实际源码采用Canvas2D底图+原生WebGL2 shader，并未调用Three.js库。不能表述为Three.js完成了本次绘画。

以上不推翻已验证的实验性Mali/HF单帧执行，但不构成连续动画、任意源码多画布合成、两条生产链或艺术质量完成的证明。
