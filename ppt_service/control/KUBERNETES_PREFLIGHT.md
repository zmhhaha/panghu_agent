# Kubernetes 无模型网络预检

在已部署的 Controller 容器中运行：

```bash
python -m ppt_service.control.kubernetes_preflight
```

从服务器执行同一个入口：

```bash
kubectl -n ppt-service exec deploy/ppt-hermes-controller -- python -m ppt_service.control.kubernetes_preflight --node-ip 192.168.137.101
```

执行准入可以保持 disabled。预检复用 Controller 的 DATA_ROOT、数据库地址、Kubernetes API
端点和实际 Worker 镜像 digest；只创建独立临时 scope、三类固定探针 Pod 和对应网络策略。
Pod 不挂载供应商 Secret、模型临时 key 或 SA token，不调用 Hermes 和模型接口。
--node-ip 可以重复，填写实际控制平面和节点地址；每个 Pod 还会自动测试自己的真实 hostIP。

Worker 必须能向自己 scope 的 dummy proxy 8080 完成随机挑战回显。Proxy 必须使用固定
62.234.50.20:443 建立正常验证证书的 TLS，SNI 为 custom-llm-proxy.panghuer.top；不发 HTTP。
检查 Worker 和 sandbox 对上游、互联网、数据库、Kubernetes Service、控制平面、Pod 实际
所在宿主机、metadata 以及 TCP/UDP DNS 的拒绝结果。Proxy 也必须拒绝这些非上游目标。
UDP DNS 使用真实查询和响应，不能用 UDP connect() 充当网络连通证据。

任何不应允许的连接成功都会失败，包含 CNI 对本节点流量的例外。预检不会自动扩大网络策略，
也不会把节点例外当作通过。否定结果描述的是此次实际连接观察，不能证明网络策略在所有
未来节点、端口、地址和时刻的行为；更换 CNI、节点或策略后应重新执行。

每个 Pod 的探针预算为 30 秒，调度与探针等待预算为 85 秒，随后留出停止窗口。总耗时超过
120 秒即报告失败。API 不可用时以保留资源和 finalizer 为先，不用硬超时强删来制造成功。
镜像拉取、调度、TLS、DNS 或停止证明不可用均返回非零状态，需要解决实际原因后重试。

stdout 输出不含凭据或远端响应的简短 JSON：passed、各角色失败探针名称、scope_uid、
writers_stopped、cleanup_complete 和 stopped_pod_uids。只有所有结果成功、获得实际停止
证据并完成清理，命令才返回 0。退出 1 时不得据此开启付费执行准入。

私有材料保留在：

```text
$DATA_ROOT/kubernetes-preflight/<scope_uid>/report.json
$DATA_ROOT/kubernetes-preflight/<scope_uid>/stop-evidence.json
$DATA_ROOT/kubernetes-preflight/<scope_uid>/<role>/output/probe-result.json
$DATA_ROOT/kubernetes-authority/<scope_uid>.json
```

stop-evidence.json 保存每个确切 Pod API UID、执行镜像 digest 和由 Kubernetes API 观察到的
terminated/containerID/finishedAt/resourceVersion。只有完成 stop/prove 后才读取结果和移除
finalizer。停止未证实时 cleanup_complete=false，保留本 scope 的 journal、Pod、策略和
finalizer；禁止手动删除 finalizer 来替代真实停止证据。

这些报告仅为网络预检和停止证据，不是 PPT 生成、视觉审核或正式发布的验收凭据。
