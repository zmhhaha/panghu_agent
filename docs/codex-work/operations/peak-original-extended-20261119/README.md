> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/peak-original-extended-20261119/README.md`；原文件 SHA-256：`19eca1742546936f82f06dc7e4416c14cb9c685fe48277c0b6ef0e13652b50b2`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# 原版峰林隔离对照导出

2026-11-19。仅准备，未启动容器。调查数据见 `read-only-survey.json`，原请求/镜像见 `original-request-survey.json`。

## 已准备的 probe

远端执行器：`/root/video-releases/peak-original-extended-20261119/runner.py`。

```sh
python3 -B /root/video-releases/peak-original-extended-20261119/runner.py run /root/video-releases/peak-original-extended-20261119/probe-20261119T094603Z-bd73a4d2
```

源码保持原版 `330a60ff88a97581ff61b9db2d35d3a09d8141ec2706b02cc41d7d9c95f0b3b3`，从真实 scene.html、scene.js 和 engine canonical 独立重算；原始文件和全部资源再逐文件 SHA-256 核对。原受信 host `df725389...` 不变。派生 host 及 request 绑定新的 host 哈希、identity 和 request 哈希，全部 patch 记入独立 plan。probe host 经 Node 语法检查通过。

probe 保持 1280×720，抓绝对时间 0 秒的一帧；明确输出 `video.offline-original-single-frame-probe.v1`、`probe_completed`、`production_acceptance=false`，不会伪装为完整视频验收。截图/默认/导航超时为180秒，外层上限300秒。HyperFrames 和 engine runtime 原封不动；如发生其 ready/seek 超时，保留实际错误再决定后续。

## 资源

目标宿主 `192.168.137.101`：8核（A55×4、A76×4），实际物理内存15.58GiB，调查时 MemAvailable 约7.2GiB、load约3.67、CPU约26%。Kubernetes top 中内存100%是其 allocatable口径，不能据此认为物理内存完全耗尽。

独立容器限定4 CPU、3GiB内存、256MiB shm、512MiB tmpfs、256进程；无网络、只读根目录、10001非root用户、去除capabilities。启动前要求至少5GiB MemAvailable，为3GiB容器之外留出空间；不足时明确报资源不足，不自动改画面。

## full

probe结果可用于估算144帧加原有9次额外检查及reload的成本。估算只作参考，第一帧编译与后续帧开销不一定相同。若预估超过已授权2小时上限，应报告时间/资源问题，不能改原scene降低效果。

```sh
python3 -B /root/video-releases/peak-original-extended-20261119/runner.py prepare full
python3 -B /root/video-releases/peak-original-extended-20261119/runner.py run <上一步返回的run_directory>
```

full 的派生 host 仅增加超时，保留原144帧、反向定位、重复/静置/重新加载定位、编码、ffprobe及解码检查，外层最多7200秒。结果留在独立 run目录，不进入生产ledger、历史实验或准入记录。现有成功版也不受影响。

runner记录容器结束或超时前后的真实ExitCode/OOMKilled；核对容器owner、run label、image ID、container ID后再停止/移除，确认不存在后写outcome。receipt必须匹配派生host、新request、新identity与镜像；probe核对帧SHA，full核对144帧、所有seek证明及输出媒体SHA。下载/展示由root执行。

## 实际 probe 结果

root唯一启动的probe已完成：0秒原分辨率单帧56031字节，capture耗时30.587秒，含容器整个运行33.957秒。实际容器退出码0、OOMKilled=false，并经owner核验移除确认不存在。原受信host仍保持不变。这只证明单帧可完成，不代表连续144帧或画面质量已验收。

probe帧、完整outcome/receipt/plan/start/log和原/派生host共8个文件已下载至 `D:/codex-work/archives/video-service/20261119/peak-original-extended/probe/`，每个文件均以远端SHA-256核对本地。`download-evidence.json`记录每项远端路径、本地路径、字节数、哈希及核验结果。帧SHA-256为 `18543631606407983dd2b427335923bce4883c5c076e4c654c0a03c427f82f5d`。

full已准备、尚未由本agent启动：`/root/video-releases/peak-original-extended-20261119/full-20261119T094814Z-d30d4d90`；plan SHA-256为 `50d8dcd3cc5c47344caa685d1ee9d9358b94e371ca47c8166dacf9ac9b5add40`。按153次capture粗估约78分钟，但首帧编译与其他状态成本不同，因此仍以实际运行和7200秒上限为准。

## 实际 full 结果

root唯一启动的CPU原后端full已正常完成，耗时1201.125秒（20分1秒），实际144帧及9张额外定位核验图；原帧率24fps、1280×720、6秒。退出码0、OOMKilled=false，按owner核验清理并确认容器不存在。原source、原受信host、未改的engine/HyperFrames runtime和新request绑定均核对通过；全部absolute/reverse/repeat/idle/reload定位检查通过。

27个文件经一次打包只读传输后逐项验证远端/本地SHA，保存到 `D:/codex-work/archives/video-service/20261119/peak-original-extended/full/`，保留原相对路径。包括plan/start/outcome、原/派生host、runtime、request、scene源码、receipt、视频、0/36/72/108/143帧和全部seek核验图。

本地再次ffprobe与完整ffmpeg解码均成功。视频为 `full/work/output.mp4`，SHA-256 `e4a1d05190f134fbeb83d72dec50670e0b4630ca6029e38121627d89270f98e8`。传输证据为 `full/download-evidence.json`，技术验证为 `full/validation.json`。这表示原版源码在增加资源与时限后可完整导出，不代表艺术质量或模仿准确度已被认可。
