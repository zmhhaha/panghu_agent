# 当前 Kubernetes 解析器期限与停止验收（R2）

先完成 `CURRENT_PARSER_IMAGE.md` 的58项精确镜像解析边界，再执行本项。必须等生产 DB/PVC 一致快照的短暂停写窗口结束；不与该窗口、部署或其他生产故障注入并行。

代码提交推送后，在集群实际 Linux master 服务器从 Git 取得精确干净 checkout，执行：

```bash
sudo python3 ppt_service/tests/acceptance/current_parser_lifecycle.py \
  --source "$PWD" --output /root/ppt-current-parser-lifecycle-20261104-r1
```

入口固定本次只读核对的 controller `345338fa...` 与解析镜像 `4492a878...`；变化时拒绝。它通过正常 `kubectl exec -i` 在已有 controller 中运行 Git 来源的测试编排，导入**该镜像内**的 `AgentKubernetesSandbox.parse`、`KubernetesSandbox`、`KubernetesBackend`，不修改任何已安装文件/环境/配置，不读取数据库/Agent凭据或模型配置。SDK模块路径/hash、controller Pod UID、精确镜像、run-id和scope UID均保留。

只在生产PVC `state/.acceptance/ppt-r2-life-<随机20位hex>` 创建新的合成资料和私有authority journal，不操作任何 `projects/` 内容、业务行或用户文件。沿用正常的 `ppt-worker` ServiceAccount、UID10000、网络拒绝策略、只读输入/根和任务子路径挂载，由原SDK创建及停止真实parser Pod。

运行顺序为健康解析 → 实际parser进程暂停 → 原30秒期限超时及物理停止 → 同资料健康后续解析。合成DOCX包含40个合法XML部分、360万个节点，未超过实际单项节点/展开/压缩比/文件限制；用于给外部观察器足够的真实解析时间。初始健康解析必须成功，否则不注入故障。

故障只对本随机scope的单个sandbox Pod生效：逐项核对 Pod UID、containerd完整container ID、CRI的Pod UID、host PID、`/proc`启动时间、精确 `python -m ppt_service.control.parsing` 命令、cgroup容器归属和本节点，重新读取Pod和PID绑定后才发送 SIGSTOP。观察到实际 `T` 状态才算注入成功。没有向controller、非测试Pod或其他用户进程发信号。SDK自身的30秒 `wait_pod` 必须返回 `sandbox_timeout`，并由原 `finish_sandbox` 在精确API UID保留的finalizer下观察全部container terminated、保存stop receipt，再删除Pod/策略；仅收到DELETE成功或Pod不见不能通过。

健康后续必须保留相同输入snapshot/来源hash并成功返回正文。所有阶段均核对输入hash不变与scope所有writer已停止。最后只有在每个Pod有terminated stop receipt、Pod/策略删除、同scope清单为空、私有owner marker匹配之后，才能删除本轮唯一 `.acceptance/run-id`；共享 `.acceptance` 父目录保留。controller UID与镜像必须不变，不重启服务。

证据保存 source.json、controller.log、events.json、fault.json、fault-pod.json、controller-result.json（含原authority journal和stop receipts）、status.json。证据目录保留；故障/停止不确定时不宣称通过、不对真实项目补写状态，也不按猜测清除finalizer。

范围是实际当前生产namespace/PVC/解析SDK的期限/停止/后续健康路径，工作资料完全合成；它不是生产用户业务行的解析回收、完整Agent模型故障矩阵、所有者上传交互、PowerPoint验收或正式发布。
