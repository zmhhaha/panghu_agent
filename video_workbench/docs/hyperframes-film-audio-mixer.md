# 整片原声轨离线混合

`hyperframes_film_audio.mix_film_audio()` 实际调用已有 FFmpeg，把经过独立校验的 `hyperframes_film_project` 冻结包混合为一条完整交付音轨。它返回 WAV 字节及带内容摘要的回执，不执行场景、调用模型、拼接视频、认可作品或部署服务，也不形成运行时准入。

调用者提供原包、来自可信边界的确切 manifest SHA，以及已存在的绝对 scratch 目录。函数先重新验证包与原 WAV，再使用唯一临时子目录；不接受调用方输出文件路径，不覆盖已有文件，成功或失败均清理自己的临时文件。需要持久保存时由调用者使用新文件的排他创建方式写入返回字节。

```python
result = mix_film_audio(
    frozen_files,
    expected_manifest_sha256=trusted_manifest_sha256,
    scratch_dir=scratch_directory,
    ffmpeg=existing_ffmpeg_binary,
)
with new_output_path.open('xb') as stream:
    stream.write(result.data)
```

排程只消费冻结 `narration_tracks` 的有理数 `start_time`／`end_time`。每个绝对边界分别取 `floor(time × 48000)`，总输出严格为 `floor(frame_count × 48000 / 24)` 个采样帧，不累加舍入后的镜头时长。延迟、补零和交付边界均使用整数样本滤镜，不使用浮点秒数或 `atempo`；`async=0` 禁止时钟补偿。交付网格的起止量化误差小于一个 48 kHz 样本，并以有理数写入回执。无法在交付网格占至少一个样本的极短原声会明确拒绝，不静默丢弃。

原 WAV 字节、采样数、源速率、声道和版本保持原样；仅最终交付轨转换到 48 kHz／双声道／16 bit PCM。单声道按原幅度复制到左右声道，双声道保持，不自动响度归一化。转换后的补零／裁定只用于最终采样网格，不修改源文件或项目时钟。显式静音镜头不创建假录音；全静音影片可得到完整静音交付轨，回执明确 `contains_narration=false`。本模块尚未混入 SFX，不新增音效协议或重写已有合成器。

成功前检查完整 WAV 格式、实际样本总数、临时输入与 FFmpeg 二进制是否保持。非零退出、超时、损坏输出或输入变化均不返回成功回执。回执绑定输入包／每条 WAV、原采样与交付边界、滤镜图、实际 FFmpeg 二进制及版本输出 SHA、交付 WAV SHA；二进制摘要采用流式读取。它不是 FFmpeg 依赖闭包证明或 runtime admission。

返回的 dataclass 中 `receipt` 是可变普通字典。后续存储或消费边界须重新核验 `receipt.sha256`、可信包身份、输出 WAV SHA／格式／采样数；不能因对象类型或一个自报 status 就视作独立证明。

2026-11-14 本地已有 FFmpeg 7.1 实测覆盖 24 kHz／44.1 kHz／48 kHz、非整采样起点、单双声道及不同 PCM 位宽、首尾样本保持、混合静音区间、全静音及重复输出一致。14 项检查通过，含伪造元数据拒绝与非零／超时／错样本数／输入变化失败清理；没有下载依赖、使用服务器或调用模型。真实音轨 fixture 回执另存现有 D 盘 research scratch，不把音频实测当作整片画面或 HyperFrames 运行通过。

独立实测归档为 `hyperframes-film-audio-fixture-20261114-r1/completed.json`，SHA256 `70098b91e5969fe4142afb82cccb02cac98e15f20238813cef9953786a098d9e`。四组均保留原包、实际交付 WAV、混合回执及真实 ffprobe 采样数复核；FFmpeg 二进制 SHA 为 `2ce797a0f88d7f067180338fb227f7b1928ea727bd9a4d7a1d022f7c52af71a3`。
