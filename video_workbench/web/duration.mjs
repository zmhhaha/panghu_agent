/** Keep script estimates, measured adopted audio and rendered video distinct. */
const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;
const padding = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0;
const rounded = value => Math.round(value * 1000) / 1000;
const seconds = value => rounded(value).toLocaleString('zh-CN', { maximumFractionDigits: 3 });

export function currentSceneAudio(scene) {
  if (!scene?.head_audio_version_id || scene.audio_stale) return null;
  return scene.audio_versions?.find(audio => audio.id === scene.head_audio_version_id
    && !audio.stale && positive(audio.duration_seconds)) ?? null;
}

export function plannedSceneSeconds(scene, { endHoldSeconds = 0 } = {}) {
  const audio = currentSceneAudio(scene);
  if (!audio) return null;
  const recorded = audio.duration_seconds + padding(scene.leading_seconds)
    + Math.max(padding(scene.trailing_seconds), padding(endHoldSeconds));
  const dynamic = (scene.motion?.mode ?? scene.production_mode) === 'motion';
  const duration = dynamic ? recorded : Math.max(positive(scene.duration_seconds) ? scene.duration_seconds : 0, recorded);
  return Math.ceil(duration * 24) / 24;
}

/** Historical single-scene, superseded or unmeasured renders cannot become a project total. */
function matchingRender(project, renders) {
  const scenes = project.scenes ?? [];
  return [...renders].filter(render => {
    if (!scenes.length || !['completed', 'succeeded'].includes(render.status) || render.silent
        || render.content_state !== 'current' || !positive(render.actual_duration_seconds)
        || (render.continuity_version_id && render.continuity_current !== true)
        || !Array.isArray(render.scenes) || render.scenes.length !== scenes.length) return false;
    return scenes.every((scene, index) => {
      const frozen = render.scenes[index];
      const visualId = (scene.motion?.mode ?? scene.production_mode) === 'motion'
        ? scene.motion?.head_version_id : scene.head_version_id;
      return currentSceneAudio(scene) && visualId && frozen.scene_id === scene.id
        && frozen.visual_version_id === visualId && frozen.audio_version_id === scene.head_audio_version_id;
    });
  }).sort((a, b) => Number(b.created_at ?? 0) - Number(a.created_at ?? 0))[0] ?? null;
}

export function projectDurationSummary(project, renders = [], { endHoldSeconds = 0 } = {}) {
  const scenes = project.scenes ?? [];
  const audio = scenes.map(currentSceneAudio);
  const audioCount = audio.filter(Boolean).length;
  const audioComplete = scenes.length > 0 && audioCount === scenes.length;
  const estimatedSeconds = rounded(scenes.reduce((sum, scene) => sum
    + (positive(scene.duration_seconds) ? scene.duration_seconds : 0), 0));
  const audioSeconds = rounded(audio.reduce((sum, item) => sum + (item?.duration_seconds ?? 0), 0));
  const plannedSeconds = audioComplete ? rounded(scenes.reduce((sum, scene, index) => sum
    + plannedSceneSeconds(scene, { endHoldSeconds: index === scenes.length - 1 ? endHoldSeconds : 0 }), 0)) : null;
  const rendered = matchingRender(project, renders);
  return { sceneCount: scenes.length, estimatedSeconds, audioCount, audioSeconds, audioComplete,
    plannedSeconds, rendered, overEstimateSeconds: audioComplete ? Math.max(0, rounded(audioSeconds - estimatedSeconds)) : 0 };
}

export function durationSummaryLines(summary) {
  const lines = ['目标时长尚未单独记录，请以原稿要求核对。'];
  lines.push(`方案预计 ${seconds(summary.estimatedSeconds)} 秒；有效已采用配音 ${seconds(summary.audioSeconds)} 秒（${summary.audioCount}/${summary.sceneCount} 镜${summary.audioComplete ? '' : '，尚非全片总时长'}）。`);
  if (summary.plannedSeconds !== null) lines.push(`按录音预计成片 ${seconds(summary.plannedSeconds)} 秒（含留白；渲染后以实际视频为准）。`);
  if (summary.rendered) lines.push(`最新匹配当前采用版本的整片${summary.rendered.purpose === 'draft' ? '草稿' : '视频'}：实际 ${seconds(summary.rendered.actual_duration_seconds)} 秒。`);
  else lines.push('当前采用版本尚无已完成的整片实际时长。');
  if (summary.overEstimateSeconds > 0) lines.push(`录音比方案预计长 ${seconds(summary.overEstimateSeconds)} 秒。需要压缩时长时，可精简旁白，或在「语速与读音」调整后重新生成配音；不会自动加速或截断。`);
  return lines;
}
