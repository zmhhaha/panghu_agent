# 实体覆盖与对象内部体面

Canvas 场景宿主增加两个可选能力：独立对象覆盖，以及局部体面/皴向辅助。世界、
计划和学习方法 v1 的严格字段保持原义，绘画参数在 `scene.js` 中按世界/对象 ID
保存。它不授予 SVG/Three.js 新执行能力，也不重建来源没有提供的标定三维结构。

## 覆盖与颜料分别表达

```javascript
window.__sceneSpaceArtwork = {
  paintObject(ctx, object, world, tools) {
    // 对象局部绘画；既有三参回调也可继续使用。
  },
  paintCoverage(maskCtx, object, world, tools) {
    const data = authoredObjects[world.id][object.id];
    if (!data.solid) return null;
    maskCtx.beginPath();
    data.outline.forEach(([x, y], i) =>
      i ? maskCtx.lineTo(x, y) : maskCtx.moveTo(x, y));
    maskCtx.closePath();
    maskCtx.fillStyle = '#000'; // 只使用 alpha。
    maskCtx.fill();
    return {substrate: [238, 233, 220]};
  }
};
```

若颜料预乘颜色与 alpha 为 `P`、`Ap`，覆盖为 `M`，承接底色为 `S`，缓存预乘颜色
为 `M × (P + (1 − Ap) × S)`，alpha 为 `M`。浅墨和纸白亮面也能遮住后面的山。
蒙版沿实体轮廓绘制，不能拿矩形 bounds 代替；局部雾边可渐隐，天空、飞鸟和有意
透明的雾可返回 null。禁止为此在最终画布擦洞或加必选白晕。

父/层 opacity 仍表示有意透明，宿主各应用一次。淡山优先使用淡墨值域，避免把整个
实体图层无意淡化。色稿与覆盖在每个页面实例各初始化一次，覆盖临时 Canvas 复用后
释放像素，逐帧复用完成的缓存。回调必须同步，不能调整宿主缓存尺寸。

一个色稿内部若含多个互相遮挡的山峰，外层蒙版不会自动恢复它们之间的前后关系。
应将这些部分拆为独立对象，或在对象内部也依次合成各自不透明底色与局部覆盖。

## 内部体面辅助

第四参的冻结 `tools.surface` 提供：

- `createSurfaceField({bounds,outline,ridges,valleys,grid,baseHeight?,boundaryFade?})`。
  脊/谷为 `{id,points:[[x,y,z],...],width?}`，正谷线高度表示凹深，点位于轮廓内。
- `sampleSurface(field,x,y,{light?})` 返回 coverage、height、normal 三分量、flow 两分量
  和 shade；shade 越大越背向所选光向。
- `paintSurfaceWash(ctx,field,{paper,ink,light,inkRange})` 连续铺染体面值，保留局部变换、
  裁切及 alpha，不创建 Canvas。网格只参与计算，不出现在成图中。
  铺染按物理像素行连续插值，每次最多8192行、524288个渐变节点，超额在绘画前拒绝。

这里的采样网格和 `world.layout` 的构图锚点不同。作者先组织轮廓、分支、脊谷和体面，
再让皴擦随局部方向变化；不是把重复竖线或一个尖锥复制到所有山上。光向及墨色值域
按画意选择，这些局部 2.5D 数据是生成假设。

轮廓最多256点，特征最多32条/256段，网格每边最多128节点/总16384节点，单场构建
工作量最多400万、数值缓冲最多1 MiB。canonical 预算模块在分配前限制每页累计64次
字段分配、16 MiB 数值存储；失败不退款，不依赖 GC，fragment 导入也共用预算，
没有 reset 或提高限额接口。这不等于所有 JS/浏览器进程内存已测量，原容器上限继续生效。

预检计入最大覆盖缓冲、helper 累计存储及工作缓冲。宿主、合成器、surface helper、
预算模块字节随新能力固定并由回执核对。旧操作按原冻结输入查阅；无覆盖回调的源
保持原颜料 alpha 像素行为。绘画参数不加入世界/计划、strategy 或 recipe_ref。

图片/视频抽帧学习、效果作者和正式制作共用指导。实际检查分别覆盖轮廓内外、局部
雾边、父/层透明度、普通透明、旧源、重播和资源拒绝。艺术对照使用同一布局的原诊断、
仅覆盖、覆盖加体面三组，分别报告遮挡、体面和笔墨结论。技术通过不自动采用效果、
发布 WikiSkill，或代表用户艺术认可。

2026-11-23 本地浏览器同布局三组对照显示：独立覆盖消除了远岭穿过主峰亮面的现象；
加入不对称轮廓、脊谷、体面和随形皴向后，主峰转折及内部遮挡更清楚，未见规则网格
接缝。当前示例的铺染仍偏平滑，棱角和细线较硬，整体有雕塑感；实体和体积改善不
等于传统水墨笔墨已达标。原图学习记录保持原状态，这三组是新建的代码诊断对照。

同日 ARM 隔离容器的三组实际 PNG 均通过重复、逆序、重载与停止证明。原诊断图与
上一轮的 PNG 摘要完全相同；仅覆盖和覆盖加体面的变体分别使用新的源摘要。三组
控制器累计实测66.014秒，新增模型调用为0，预留均已结算；原计划的未知模型用量
继续保持未知，不授权长片导出。体面组观测页面创建11个局部场，数值缓冲预留1,892,352
字节，并执行11次铺染和12,500次采样。实际看图与本地结论一致，技术和艺术结论
分别记录在 `evidence/scene-space-coverage-form-container-20261123.json`。

学习 executor 已以干净源码提交 `11281f0` 构建，并使用 `latest` 镜像的 digest
`sha256:8a8b921a4433c60f630cf800f6e41dbb017f4a7d9d656cc412eb205f9a45c4e8`
完成仅镜像更新；健康检查、运行字节与线上 API 第2版能力读回通过。原 renderer
镜像保持，正式制作新场景队列仍关闭。这次单帧诊断不代表正式长视频已完成验收。
发布前后检查89个部署，仅目标 executor 的预期镜像变更；原10张业务表摘要保持，
发布前后包含独立诊断账本的12张表摘要一致，诊断控制容器已移除。
