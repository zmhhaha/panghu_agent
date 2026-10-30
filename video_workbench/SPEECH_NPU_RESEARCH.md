# RK3588 NPU 语音合成调研

调研日期：2026-10-30。范围为服务器只读检查、公开项目文档和源码核对；没有安装运行时、下载或运行模型、升级驱动或部署服务。以下适配能力不等于本机性能和中文质量验收。

## 硬件现场

| 节点 | 主板 | SoC | 内存 | 检查时可用内存 |
| --- | --- | --- | --- | --- |
| arm-cluster-master（192.168.137.101） | Orange Pi 5 Plus | Rockchip RK3588 | 约 16 GB | 8,028 MiB |
| orangepi5-max-server1（192.168.137.211） | Orange Pi 5 Max | Rockchip RK3588 | 约 16 GB | 6,797 MiB |

两台均为 `6.1.115-vendor-rk35xx`，暴露 `/sys/kernel/debug/rknpu/`，驱动报告 `RKNPU driver: v0.9.8`。采样时 Core0、Core1、Core2 均为 0%；这只说明检查时的负载。常见路径和动态库缓存未找到 RKNN/RKLLM 库，不能据此断言系统所有目录或容器均未安装。

目标是 Rockchip RKNN/RKLLM，不能用 ARM64 CPU、NVIDIA CUDA、华为 Ascend 或其他 NPU 支持代替。两台机器的内存/NPU 也不会自动合并；后续可以按镜头分配任务。

## 选型判断

| 模型/路径 | RK3588 证据 | 中文与成熟度 | 建议 |
| --- | --- | --- | --- |
| Matcha + Vocos，经 rkvoice-stream | 有声学 RKNN encoder/estimator 和 RKNN vocoder 实现；ODE、ISTFT 等仍在 CPU | `matcha-icefall-zh-en`，16kHz、单说话人；有中文/英文及混合前端 | 优先作为低成本 NPU 基线验证 |
| Kokoro ConvOnly，经 rkvoice-stream | 有 RK3588 配置与平台模型包说明，CPU/ONNX 与 RKNN 混合，包含明确 CPU 回退 | 实际调用 `misaki.zh.ZHG2P`，有中文可选依赖；模型包下载与板上效果尚未验证 | 与 Matcha 同稿比较，作为中文旁白候选 |
| Kokoro，经 marty1885/kokoro-server | 有 RK3588 decoder 实现；encoder 与 harmonic generator 在 CPU | C++ 中文音色路由明确警告并退回英文前端；Python 文本入口同样为英文 | 可参考移植技术，不能直接当作中文服务 |
| Piper，经 marty1885/paroli | ONNX encoder 在 CPU、RKNN decoder 在 NPU，有转换工具 | 需要匹配的中文 checkpoint、前端和拆分模型；并非把任意 Piper ONNX 直接交给 NPU | 备选，中文音色/模型转换仍待核实 |
| Qwen3-TTS | 有社区 RK3588 CPU/NPU 混合移植；另有 rkvoice-stream 实现，但其 Talker 文件写为 RK3576 | 小型实验移植，不等于原模型直接支持 RK3588；速度和输出须逐方案判断 | 暂不作为首个生产后端 |
| CosyVoice3 | 有俄语社区 RK3588 混合移植，以及独立 RK3576 项目 | 尚不能证明可直接部署为本服务的高质量中文旁白 | 保留研究方向 |
| Fish Speech / IndexTTS | 本次公开检索未找到可推荐的成熟 RK3588 RKNN 适配 | 不能以支持 GPU/CPU 推导 NPU 可用 | 目前不作为板载 NPU 路线 |
| Rockchip 官方 MMS-TTS | 官方明确支持 RK3588，encoder、decoder 转 RKNN，中间动态处理在 CPU | 官方示例是 `facebook/mms-tts-eng` 英语，固定输入长度并有裁剪 | 可验证基础 NPU 环境，不能替代中文选型 |

LocalAI 属于服务/后端封装，是否调用本机 NPU 取决于实际后端；edge-tts 调用在线服务，不使用本机 NPU。Kokoro-FastAPI 的 ARM64 CPU 镜像也不代表已启用 RK3588 NPU。

## 关键源码证据

### 中文首选验证路径：rkvoice-stream

固定提交：[`f2c2d730403e1d4c732add799f0414da34695043`](https://github.com/suharvest/rkvoice-stream/tree/f2c2d730403e1d4c732add799f0414da34695043)。调研时 GitHub 为 46 Star；社区适配仓库的规模与基础模型仓库需分别看待。

- [Matcha 后端](https://github.com/suharvest/rkvoice-stream/blob/f2c2d730403e1d4c732add799f0414da34695043/rkvoice_stream/backends/tts/matcha.py#L376)：声学模型优先走拆分 RKNN，也支持其他加载路径；失败可能回退 ONNX Runtime CPU。Vocos 声码器用 RKNN，ISTFT 在 CPU。
- [中文模型说明](https://github.com/suharvest/rkvoice-stream/blob/f2c2d730403e1d4c732add799f0414da34695043/docs/matcha-zh-en-tts.md)：模型为 `matcha-icefall-zh-en`，中文/英文/混合，16kHz、单说话人。部分命令以 RK3576 为背景，不能直接照抄到 RK3588。
- [Kokoro ConvOnly 文档](https://github.com/suharvest/rkvoice-stream/blob/f2c2d730403e1d4c732add799f0414da34695043/docs/kokoro-convonly.md)和[中文前端](https://github.com/suharvest/rkvoice-stream/blob/f2c2d730403e1d4c732add799f0414da34695043/rkvoice_stream/backends/tts/kokoro_long32_frontend.py#L292)：有实际 `misaki.zh.ZHG2P` 调用，使用平台专属 bundle，部分时长情况显式回退 CPU generator。
- 该文档指定 HF `harvestsu/seeed-local-voice-rk-artifacts`、路径 `rk3588/kokoro-convonly-v1_0/`、revision `3f8d58c8446ec4b18891624ad4ae4ce75e0f3d3e`。本次只读目录请求遇到 SSL EOF，尚未独立确认固定包可下载，不据此承诺即装即用。
- [旧 Kokoro 路线的性能分析](https://github.com/suharvest/rkvoice-stream/blob/f2c2d730403e1d4c732add799f0414da34695043/docs/kokoro-rknn-analysis.md)记录了更多算子上 NPU 仍可能更慢或误差更大；不能把旧 `kokoro_rknn` 的数据直接用在新 `kokoro_convonly` 上。
- [依赖定义](https://github.com/suharvest/rkvoice-stream/blob/f2c2d730403e1d4c732add799f0414da34695043/pyproject.toml#L35)指定 RKNN Toolkit Lite2 2.3.2、ORT 1.19.2，并提供中文 extra；native tail 还需要 SDK headers 和 `librknnrt`。已有驱动 0.9.8 不能单独证明兼容，应先核对并隔离验证，不能先在业务节点盲目升级驱动。
- [Qwen3 后端](https://github.com/suharvest/rkvoice-stream/blob/f2c2d730403e1d4c732add799f0414da34695043/rkvoice_stream/backends/tts/qwen3_tts.py#L239)不是空类，但 Talker 路径硬编码为 RK3576，RK3588 性能栏为空；RK1828 后端则需要额外 PCIe 协处理器。

仓库代码声明 Apache-2.0，具体模型权重许可证需按选定权重另核。README 中首包时延/实时因子为作者数据，不是用户机器实测，也不说明长篇中文视频旁白的自然度。

### 其他适配与官方边界

- [`marty1885/kokoro-server`](https://github.com/marty1885/kokoro-server/tree/952be751c3c4ee325a8049fa931eae037ef55f3b)（9 Star）：[中文音色分支](https://github.com/marty1885/kokoro-server/blob/952be751c3c4ee325a8049fa931eae037ef55f3b/src/g2p.cpp#L174)明确回退英文；[推理代码](https://github.com/marty1885/kokoro-server/blob/952be751c3c4ee325a8049fa931eae037ef55f3b/infer.py)保留 CPU encoder/harmonic generator；[图改写说明](https://github.com/marty1885/kokoro-server/blob/952be751c3c4ee325a8049fa931eae037ef55f3b/ON_RKNN_HACKING.md)展示固定窗口、算子替换及 FP16 取舍。
- [`marty1885/paroli`](https://github.com/marty1885/paroli/tree/0bf1f9f80acb69c47b0dc3a50a6ed2c302b253ef)（136 Star）：[代码按 decoder 扩展名选择 RKNN](https://github.com/marty1885/paroli/blob/0bf1f9f80acb69c47b0dc3a50a6ed2c302b253ef/piper/piper.cpp#L296)，[转换脚本](https://github.com/marty1885/paroli/blob/0bf1f9f80acb69c47b0dc3a50a6ed2c302b253ef/tools/decoder2rknn.py)指定 RK3588 和固定输入。README 最后的示例把 `.rknn` 放在 encoder 参数，与说明及代码不一致，不能直接照抄。
- [`MasterVVK/qwen3-tts-rknn-russian`](https://github.com/MasterVVK/qwen3-tts-rknn-russian)（10 Star）与 [`MasterVVK/cosyvoice3-rknn-russian`](https://github.com/MasterVVK/cosyvoice3-rknn-russian)（5 Star）：属于社区混合移植，不等于官方完整中文支持；[`keelzhang/cosyvoice3-rk3576`](https://github.com/keelzhang/cosyvoice3-rk3576)目标是另一芯片。
- Qwen 移植作者公布最快流水线 RTF 为 5.5，RKLLM Talker 路径约 6.2；[默认模型选择](https://github.com/MasterVVK/qwen3-tts-rknn-russian/blob/master/qwen3_tts_pipeline.py#L1074)优先 GGUF Talker，[vocoder 选择注释](https://github.com/MasterVVK/qwen3-tts-rknn-russian/blob/master/qwen3_tts_pipeline.py#L1096)明确因 RKNN INT8 噪声优先 ONNX FP32。CosyVoice 移植作者报告短句/长句 RTF 约 6.6/13.6，并记录量化后的截断、重复或高频伪影；[默认 LLM 选择](https://github.com/MasterVVK/cosyvoice3-rknn-russian/blob/main/cosyvoice3_rknn_pipeline.py#L1274)还存在注释与实际 RKLLM 默认值不一致。数字仅对应作者的特定移植和测试，不代表原模型在其他硬件上的性能，也不代表本机测试。RTF 5.5 即作者测试下生成 1 分钟音频约需 5.5 分钟。
- [RKNN3 官方平台列表](https://github.com/airockchip/rknn3-toolkit#supported-platforms)中的 RK1820/RK1828/RK3572 与本机板载 RK3588 NPU 不同。RK3588 作为外接协处理器主机的案例，以及需额外 AX650N 的项目，都不能算现有硬件即可使用。
- [Rockchip MMS-TTS](https://github.com/airockchip/rknn_model_zoo/blob/837db8e992815ff8647eadb709f07067f4bc3a71/examples/mms_tts/README.md)与[模型拆分说明](https://github.com/airockchip/rknn_model_zoo/blob/837db8e992815ff8647eadb709f07067f4bc3a71/examples/mms_tts/export_onnx.md)：官方英语例程，固定输入长 200，中间动态处理/随机采样留 CPU。
- [sherpa-onnx RKNN 编译实现列表](https://github.com/k2-fsa/sherpa-onnx/blob/935e5ded3d949a8b557ac44ef4dad03a7d2038e7/sherpa-onnx/csrc/CMakeLists.txt#L207)面向 ASR/KWS/VAD；同提交中的 [VITS/Piper/Melo](https://github.com/k2-fsa/sherpa-onnx/blob/935e5ded3d949a8b557ac44ef4dad03a7d2038e7/sherpa-onnx/csrc/offline-tts-vits-model.cc)、[Kokoro](https://github.com/k2-fsa/sherpa-onnx/blob/935e5ded3d949a8b557ac44ef4dad03a7d2038e7/sherpa-onnx/csrc/offline-tts-kokoro-model.cc)、[Matcha](https://github.com/k2-fsa/sherpa-onnx/blob/935e5ded3d949a8b557ac44ef4dad03a7d2038e7/sherpa-onnx/csrc/offline-tts-matcha-model.cc)仍走 ORT。不能拿该项目的 ASR RKNN 支持证明 TTS 已用 NPU。

## 下一步验证方式

建议先在 worker 节点做单并发、受限资源的小样，再确定生产提供者：

1. 核对驱动、`librknnrt`、Lite2 和模型构建版本；确认目标是 RK3588。核验下载包及权重许可证，不为尝试模型先升级业务节点驱动。
2. 同一份约 150～300 字的中文旁白，比较 Matcha+Vocos、Kokoro ConvOnly 和 CPU 基线，包含数字、停顿、长句及 DSH/Cordis/SDK/ACP/PTY/LSP 等术语。
3. 记录冷启动、完整合成耗时、音频实测时长、峰值内存、CPU/NPU 负载和实际后端日志。RTF 定义为合成耗时/音频时长，越低越快；首包延迟不能代替整片吞吐。确认没有把 CPU fallback 当成 NPU 成功。
4. 试听自然度、读音、漏字、噪声、断句和长片段拼接，再决定音色与服务化方式。不根据模型大小、Star 或低延迟直接推断配音质量。
5. 通过独立内部语音服务与工作台 HTTP 适配层对接，保留替换远端 API 的能力；媒体解码/规范化、字幕对齐、MP4 继续由工作台媒体链路承担。

当前仍未确定正式 TTS 提供者，本调研不完成 OpenSpec 任务 1.1，也不代表全部音频制作功能已实现。DNS、Casdoor、线上工作台均未更改。
