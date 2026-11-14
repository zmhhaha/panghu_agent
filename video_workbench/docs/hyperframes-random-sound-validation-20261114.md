# HyperFrames 随机状态与带声音效：专项技术验证

2026-11-14。承接 [运行验证](hyperframes-project-runtime-20261114.md)，补充 H.1／H.2 的随机状态和非空音轨证据。本批是单独冻结的技术验证，不复用此前整片 2/2 预算，也不表示新作品、人工认可、Skill 发布或正式整片接通。

## 冻结输入与实际消耗

服务器既有 OPS 下 `hyperframes-random-sound-r2/frozen-input.json` SHA256 为 `9e26e5902897c50f7cda4f8f401068d46d660040611caa88d689ba8b408f3bb2`。上限为三次短编码、六次无编码定位；实际仅一次编码、两次定位 fixture，模型、TTS、依赖下载、构建和部署均为零。依赖镜像保持为已部署 renderer `sha256:59ee5c356f4c742e515385db4e2b315c9393641bea7d76136f095b27214e79ca`，core 为 0.8.149。

首次 r1 只读准入预检失败：现有准入引用原 ledger，不能用空测试 ledger 证明。该次没有启动浏览器或编码。r2 改为只读核验原准入，新的 SVG 技术操作使用独立 ledger 和明确的默认 HF 身份，不复制历史行或写入业务存储。原失败和输入保留。

## 随机状态

两个明确的 Canvas 协议夹具使用真实核心。固定种子每次从同一状态重建，在正向、反向、非顺序、重复、空闲和重新加载定位后均与原帧字节一致；未固定种子的版本被真实逆向帧检查以 `method_reverse_seek_mismatch` 拒绝，没有编码或接受媒体。夹具属于验证代码，不是 Agent 美术作品，也不向创作者提供固定视觉模板。

该组耗时约 6.88 秒，限制为 2CPU／1GiB，测试容器确认不存在。它记录耗时与资源限制，没有宣称测得峰值 CPU／RSS 或长片性能。

## 原 Agent 作品实际带声编码

复用原 SVG 作品，原 `source` 摘要保持 `08d54e523cfa5bd54ff9ee4b273758276aae7d0a646f4ce043e11bb339cf5c1c`，视觉文件逐字节不改。仅通过已有 `sound_events` 合同加入两个技术音效：0.5–0.9 秒的 440Hz 正弦波，2.5–2.9 秒的 660Hz 三角波。真实 HF `renderSeek(exact)` 驱动、非顺序等定位、编码和原始音效封装均完成，显式保留 `legacy_bridge=true`。

实际输出 4 秒、24fps／96 帧，87,720 字节，耗时约 9.63 秒；原作品和原媒体不变，渲染容器已移除。MP4 SHA256 为 `3e15f8bf5463e58738b149702ca79eefd89c843899f570a8e8682bdea28378d3`。48kHz 单声道原 WAV 摘要为 `d9fefb1ef399a067723867b5055c3949abcdc38f94d9ec0944493720496d2b4f`。

独立后置探测最初因服务器宿主没有 `ffprobe` 停止，原错误保留。没有重跑编码或定位，直接将现成 MP4、WAV 和回执校验摘要后取回 D 盘，用既有 FFmpeg／FFprobe 7.1 完成独立分析：

- MP4 的音视频均从零开始、有效时长均为四秒；实际完整解码通过。
- Python 按音效合同独立生成 192,000 个原始 PCM 样本，与 WAV 逐样本完全相同。
- AAC 解码为 192,512 样本，包含 512 个填充样本；有效区与原 PCM 的最大误差为 108LSB，RMS 误差约 4.12LSB，不将 AAC 误记为无损音频。
- 两段音效频率分别验证为 440／660Hz；阈值测得起点约 0.50044／2.50065 秒，结束约 0.89854／2.89854 秒，与已有短包络和 AAC 容差一致。三个远离边界的静音窗 RMS 均为零。

上述证明非空音轨和时序的技术正确性，没有代替实际人耳试听或表现力认可。此前三引擎重渲染的音轨实际为空，其 `audio_pcm_unchanged=true` 仅表示双方摘要均为 null；本批新增真实非空音轨证据，未改写旧回执。

## 证据位置与剩余范围

服务器 OPS 下 `hyperframes-random-sound-r2/reconciliation-completed.json` 绑定原输入、实际核心、媒体、独立分析和容器清理。原 `execute-failed.json` 与 r1 预检失败均保留。D 盘既有研究 scratch 中 `hyperframes-random-sound-r2-evidence/` 保存媒体及原始摘录；其 `independent-v2/independent-audio-check.json` 已按摘要归档到服务器 `independent-local-v2/`。

测试文件新增两项真实核心 opt-in 用例；有界验证执行器位于 `tests/video_workbench/run_hyperframes_random_sound_validation.py`。实际批次冻结执行器 r2 SHA 为 `7ef9483f8d62f16ec19ff35579d0bf64789fbd8a28d51804d41c42a78285a9fe`。后续脚本补充既有镜像媒体工具回退与 AAC 误差报告；本批独立媒体检查使用本地工具，没有把该回退路径记为目标环境已验证。

与既有三引擎、异步、原声和正式单场景证据结合，本批补上 H.1／H.2 已识别的随机状态与必要音效技术缺口。H.3 仍需新 Agent 模块、运输、正式整片业务、预览与实际媒体审阅；本批不改变其未完成状态，也不扩大到统一音效事件多轨混音或美学认可。
