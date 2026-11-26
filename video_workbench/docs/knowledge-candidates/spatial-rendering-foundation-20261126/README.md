# 共享空间、光墨与镜头：未发布知识候选

这组候选依据BV179WZ6REYX的实际研究、共同山体迭代记录和实施提交
`ae06a9ec6d1100bed857956ec524dbada7ab60d1`的同内容本地验证整理。它不是新生成任务，
不是已采用效果，不是正式WikiSkill版本，也尚未写入线上知识库。当前山水艺术认可
仍为false，完整连续视听与跨题材迁移未验收。

## 候选内容与职责

1. 共同形体、可见表面和独立实体覆盖先成立，浅墨不导致后山透出。
2. 统一光向组织湿笔、皴擦和勾骨；法线指导画法，不把机器灰阶底稿当作笔墨。
3. 材质绑定对象/世界坐标、seed和尺度，三频结果进入有结构的接触，不等于完整PBR。
4. 空气降低颜料对比，云承担方向、留白和空间穿插；不取消实体覆盖。
5. 初始编排选择静镜头/推进/平移或局部动态，缓存已有世界，先检查完整取景范围。
6. 用同内容、同机位、同时间和独立冻结开关比较；保留失败及实际成本，用户认可后
   再决定迁移。新题材或其它样片的分数不能替代原山水认可。

框架合同、覆盖/深度正确性、计量和依赖准入属于框架；WikiSkill提供怎样组织笔触、
材质、镜头及观察的指导。候选不会用文字规则替换底层强制，也不会改为默认多轮学习。

## 文件与既有合同

| 产物 | 作用 |
|---|---|
| `skill/SKILL.md`及本地references | 自包含expression方法，不含外部网址或绝对运行路径 |
| `skill-package.json` | 由现有`build_skill_package`生成的`video.animation-skill-package.v1`，仅声明已测Canvas |
| `research/reference-1.json`、`reference-2.json` | 现有研究记录输入格式；保留BV、源码固定commit、观察范围和后续版本差异 |
| `research/provenance.json` | 区分外部转述/读源码、本地测量、推断、失败和未测项 |
| `payloads/experience-inputs.json` | 用现有KnowledgeStore归档的四份不可变事实输入 |
| `payloads/knowledge-proposals.json` | 现有候选合同，5个provisional页面，确切本地证据ID与revision0 |
| `payloads/method-candidate.json` | 现有`video.animation-method.v2`方法候选输入，绑定技能包与独立研究记录 |
| `records/` | 既有本地Store实际返回的研究/experience/snapshot/proposal/method candidate记录 |
| `payloads/online-archive-plan.json` | 供root审阅的分步归档计划，未执行，无正式发布或采用步骤 |
| `manifest.json`、`validation.json` | 文件、包及候选身份与真实校验回执，不是平台正式发布manifest |

运行Skill的外部URL由现有合同明确拒绝，因此原始来源留在research/经验记录，技能
正文已经内化方法并只链接包内references。没有把参考视频/仓库下载作为运行前提。
这也不意味着抹掉来源；独立研究记录和manifest仍绑定其确切出处与摘要。

## 保存工作流与校验

`prepare_candidate.py`只在指定独立本地目录运行现有两个Store：

- `AnimationMethodStore.create_research`后`create_method`，得到真实`status=candidate`。
- `AnimationKnowledgeStore.record_experience`后冻结snapshot，再`record_proposals`，
  保存provisional提案；没有调用`apply_proposals`。

两套本地SQLite使用owner `local-spatial-research`，位于operation目录的
`knowledge-candidates/spatial-rendering-foundation-20261126/`。这清楚区别于服务器用户
与线上数据库。原始source、媒体和正式库不被修改；不存在伪造的commission、模型
operation或服务端验签。导出的候选JSON可以独立审阅。

在仓库根目录运行：

```powershell
python video_workbench/docs/knowledge-candidates/spatial-rendering-foundation-20261126/prepare_candidate.py
```

脚本读取既有research原件和已验证样片，检查媒体/源码身份，再调用现有包、research、
method、experience、snapshot及proposal validator。幂等重放应返回同一候选；验证
数据库中Wiki revisions、method publications和acceptances均为0。它不联网、不运行
渲染、不调用模型、不apply、不发版。内容改动应准备新的明确source/request身份，
不要覆盖已归档不可变记录来强行让旧请求通过。

这些校验验证格式、关联与本地保存流程；它们不是新的画面成功、用户认可、迁移、
线上入库或生产消费证明。

## 本轮实际证据

已有山水样片1280×720、24fps、240帧、10秒，11个最终条件的原生host重复/逆序/
重载及完整视频解码通过。全部对象每页绘制一次，后续帧用缓存。旧无policy的9张
静图和240帧保持逐字节一致，预算低档图也保持同图；实际coarse计数19,004次noise，
预算20,000，0材质法线导数。full的18,945次三频查询乘三=56,835是明确的算术推导，
不能冒称旧宿主已经测到了该计量字段。

所有样片native批次累计598.4257416秒；独立3项原生权限测试的pytest suite与命令
总耗时另列，不能相加成一个native benchmark。生产模型调用0、model_review未运行，
没有GPU渲染、完整PBR、连续运动手感、声音或艺术通过的结论。

原35版与本轮仍有削木纹笔簇、亮肩阶面、峰顶露纸和近岸颗粒；随后四张大墨面稿
全部未采用。其失败和观察范围与候选一起保留，不因技术通过或其它内容评分删除。

## 后续线上归档与消费

如root决定把这些候选写入真实服务，先审阅`online-archive-plan.json`和内容身份。
实际owner、research返回ID、experience证据ID与snapshot必须由真实服务重新冻结，
不能复制本地SQLite的owner、UUID、snapshot时间或SHA冒充线上回执。若目标页面已
存在，应读取实际revision并调整候选的create/revise及expected_revision，不复用
本地revision0绕过CAS。

现有知识发布链从真实平台不可变事实快照编译完整WikiSkill。本地候选不自动进入
正式发现集合，也不以一次读取或本地保存声称后续成片已应用。未来可将这份候选
作为一条有明确范围的学习材料，由用户明确发版时与其它事实一起取舍；不能把这项
4.2归档当成6.2正式发布、效果采用或自动迁移的授权。
