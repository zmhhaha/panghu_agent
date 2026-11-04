# 当前解析镜像边界验收（R2）

`current_parser_image.py` 只在 Linux 服务器执行。先提交、推送，再在服务器从 Git 获取精确干净源码：

```bash
python3 ppt_service/tests/acceptance/current_parser_image.py \
  --source "$PWD" \
  --image arm-cluster-master:5000/ppt-agent-validator@sha256:4492a878db0d575f20542379a61986500a1c5263b5ecc5460026a87937549bc9 \
  --output /root/ppt-current-parser-image-20261104-r1
```

上例镜像是 2026-11-04 从线上 `ppt-hermes-controller` 当前 `PPT_WORKER_IMAGE` 读取的解析/校验镜像。每次验收应重新核对实际值；`latest` 可能已经指向另一个候选，不能代替线上 digest。入口不会拉镜像或装依赖，不传源码包，不连接数据库或模型。

入口通过 Git 在服务器创建只读源码快照，移除 `.git` 后挂载为 `/harness`。容器设置无网络、UID/GID10000、只读根、drop ALL、no-new-privileges、128进程/1GiB内存/2CPU及独立临时目录。容器只生成合成输入与证据，运行镜像自带的 `python -m ppt_service.control.parsing`，其 `PYTHONPATH` 固定为 `/opt/ppt-service`，不将 Git checkout 上的应用模块替换进镜像。模块真实路径、SHA256、Python/PyMuPDF/Pillow 版本、实际安全上下文与未修改的五个生产常量均记录；入口还比较镜像解析器及 failure-contract 文件与 Git 的 SHA256。

共58个实际 CLI 场景：五种支持格式；150/151页PDF；500000/500001字单文件；1500000/1500001字项目总量；25MiB/25MiB+1源文件；图像边长/2000万像素；ZIP的2048/2049项、16MiB单项、64MiB/64MiB+1真实展开总量；8MiB XML、10万个节点、深度64；宏/ActiveX/加密/外部引用、压缩比、重复部件、危险路径、软硬链接；扫描/附件/表单/加密/损坏PDF、损坏Office/PNG、非UTF8/NUL、来源hash错配和超过10个来源。

64MiB展开总量通过真实约3:1压缩、每项≤16MiB、外层文件≤25MiB的DOCX触发，未降低生产阈值，也未先被100:1压缩比门挡住。失败场景检查进程退出2、精确四字段安全错误报告及snapshot绑定，不允许返回源内容/路径；通过场景核对来源ID/路径/hash和正文/图片/页数。每个场景均核对原始输入hash不变。

`evidence/status.json` 仅在58场景全部通过、代码与镜像匹配、源码未变且本轮容器全部移除时通过。保留 `case-results.json`、逐例原输入/报告/日志、镜像与源码manifest、installed-parser.json和容器停止/清理证据。失败目录不覆盖。全部数据为合成资料；没有读取或修改真实项目。

这些结果证明精确生产镜像中的解析入口和真实硬边界，不能单独证明生产Kubernetes协调器的30秒期限、物理停止、真实Casdoor上传UI或生产asset状态转换。每例CLI有30秒宿主监督上限，属于本验收runner自身保护，不冒称线上Kubernetes超时演练；后者由R4/R5的实际Pod协调矩阵单独记录。
