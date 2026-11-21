> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/lab-capability-selection-20261116/README.md`；原文件 SHA-256：`5c2a79e80fcfa58030a7692b959f702130d768b7937a4ffc8d192ac99db20515`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# 实验室能力选择发布记录

代码5a93ea3从干净Git完整构建；仅executor/lab两服务切换。数据库备份及完整构建日志保存在服务器`/root/video-releases/lab-capability-selection-20261116`。

本目录保留小型核验回执、发布工具、OpenSpec乐观锁写入/读取记录。真实网页只操作表单并恢复空白，没有提交实验或发版。测试分组重叠，不累加。API和模型角色使用边界见应用docs/animation-lab-agent-api.md。
