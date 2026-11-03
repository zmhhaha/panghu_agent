# 源资料上传的系统磁盘余量保护

上传默认保留 128 MiB 的文件系统可用空间。`Settings.upload_min_free_bytes` / `PPT_UPLOAD_MIN_FREE_BYTES` 可配置为非负整数；非法值使配置加载失败。配置为 0 仍检查待写字节的实际空间，不会关闭容量测量。

Linux 在创建此次上传 UUID 目录前调用 `statvfs`，并在每次实际写入前通过打开文件描述符的 `fstatvfs` 检查 `f_bavail * f_frsize >= 保留空间 + 本次待写字节`。写入无缓冲且处理短写，避免小块请求未落盘时低估已用空间。低余量或目录创建/文件创建/写入/关闭返回 ENOSPC，固定返回 HTTP 507 `storage_capacity_low`；无法测量容量则停止上传并返回 HTTP 503 `storage_capacity_unavailable`，不会默默绕过检查。此次 UUID 目录中的部分上传按原有清理路径移除；若清理本身遇到文件系统故障，记录固定内部错误，保留原错误响应。

这是一项源资料上传的服务器保护，不属于用户用量、费用、资格或配额。读取、下载和删除不受新余量检查限制。生成、渲染、数据库、multipart 临时缓冲所在分区及其他服务的磁盘写入不在本项保护范围；并发外部写入仍可能耗尽空间，因此还会处理实际 ENOSPC。

独立验证见 `tests/ops/test_storage_capacity.py` 和前端 `src/test/storage-capacity.test.ts`。使用构造的容量读数和 ENOSPC 验证边界、清理与旧文件可读，不填满真实磁盘；在服务器隔离测试环境运行。
