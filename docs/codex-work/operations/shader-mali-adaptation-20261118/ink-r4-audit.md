> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/shader-mali-adaptation-20261118/ink-r4-audit.md`；原文件 SHA-256：`229105a6dfb2519bccc2073958e08c3feb1387131a75904701e44624e286c507`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# 水墨 r4 独立复核

2026-11-18。只读审查r4既有源码、捕获宿主、候选guard、回执和PNG；仅对最终容器名及已观察GPU PID补一次只读停止查询。没有启动新渲染，没有改动r4原始证据。

## 结论

**r4的实验性Mali/HyperFrames单帧技术验证通过，前次审查指出的证据字段缺口已补齐。艺术目标未达成，不能判整体更好。SH.4完整艺术目标与SH.6正式两链准入均不能据此标完成。**

## 技术核实

- source、request、host、guard四项实际文件SHA均与执行回执一致。source SHA为 `248065fbe7cd1e6e3d6802e6cd16c57e1e2b381a93532ee6ed9804645439ec95`。r4是新的艺术迭代身份，没有声称与r2/r3艺术源码相同。
- libmaliBefore/libmaliAfter均为 `07cc993b13d6591161b0a12a270c75e06f25d56437016c6fbff8ea36e47c6614`；捕获宿主确实在运行前后读取 `/mali/libmali.so` 并核对，补齐了r3依赖外部先前probe的版本来源缺口。
- CDP记录driverVendor=ARM、driverVersion=v1.g24p0，真实renderer为Mali-G610 / OpenGL ES3.2，ANGLE git hash ca3d0ccbebb5。nativeGL另记录WebGL2/GLSL3.00、完整1024×640画布及同尺寸viewport/box，body仅Canvas和Script。
- 严格独立候选guard身份保持 `animation-engine-runtime-mali-g24p0-candidate-1`；原生方法捕获的renderer/vendor要求Mali/ARM，software=false、context_lost=false、gl_error=0。没有修改正式软件guard来冒充生产准入。
- 继续加载校验SHA的真实HyperFrames core0.8.149。新增reloadedSeek回执为core_seek_count1、actual_seconds0、requested_seconds0，补齐reload返回值记录。
- 完整readPixels PNG实际SHA与回执一致。主图、重复seek和reload SHA均为 `e8853993200b264c7479963afe459fdd1079baf76190850dc62968a79772c437`；同源shader强度0消融SHA为 `bee5dbf27fb8e9043fe2fb44d9af1e9540b16e6563fdacdbc868e22418ab99b9`。
- stdout摘要与完整receipt均frames_captured4，统计口径已一致。
- 既有固定renderer digest为 `sha256:45504cc19d0ce1a0dd2234a47ac953afcb957bb1b92bbeaf63356610f641f13b`；设备仍仅 `/dev/mali0`，外部采样GPU进程389150的FD13指向该设备。
- 回执ExitCode0、OOMKilled=false、约7.50秒，随后移除并确认容器不存在。单帧范围内未发现新的实质证据缺口。

## 独立视觉判断

直接查看r4/still.png和without-shader.png：前景原有垂直封口已消失；shader带来的边缘柔化、局部扰动更可见。主体山峰仍由粗暗长边夹浅色长条构成，具有条带/帘幕感，尚未形成自然山石体积与皴擦。消融差异存在不等于艺术更好，也不证明接近用户参考。

本轮应按预算停止并保留实际结果，艺术目标记未达成。正式两条HyperFrames生产链、连续动画、队列/回退或生产部署均未在此审查中完成，不应勾选SH.6；不能把单帧技术通过当作完整SH.4视觉目标完成。

## 最终停止核查

唯一补充只读查询时间 `2026-11-18T15:37:59.110659Z`：精确容器 `ink-shader-keyframe-20261118-r4` 不存在；`/proc/389150` 不存在，其原Mali FD已随进程释放。原始输出保存在同目录 `ink-r4-stop-verification.json`。这不是GPU全局空闲声明，未扫描其他业务进程。
