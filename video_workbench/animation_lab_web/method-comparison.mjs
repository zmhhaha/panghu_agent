const FPS = 24;
const SPEEDS = [0.5, 1, 1.5, 2];
const text = value => typeof value === 'string' ? value : '';

function node(doc, tag, className = '', label) {
  const value = doc.createElement(tag);
  if (className) value.className = className;
  if (label !== undefined) value.textContent = String(label);
  return value;
}

/** Compare original seconds, without claiming semantic alignment or frame-lock.
 * getSelection() is a detached restoration snapshot, including time/speed/audio.
 * onTimeChange(clockSeconds, snapshot) exposes the shared original-time clock.
 */
export function createMethodComparison(doc, {
  candidates = [], bestId = null, baselineId = null, selection = null, root,
  assetUrl, onSelectionChange = () => {}, onTimeChange = () => {},
  now = () => performance.now(),
  scheduleFrame = fn => requestAnimationFrame(fn), cancelFrame = id => cancelAnimationFrame(id),
} = {}) {
  const versions = [], seen = new Set();
  for (const value of Array.isArray(candidates) ? candidates : []) {
    if (typeof value?.id !== 'string' || !value.id || seen.has(value.id)) continue;
    versions.push(value); seen.add(value.id);
  }
  const validId = value => typeof value === 'string' && seen.has(value);
  const restored = selection && !Array.isArray(selection) ? selection : {};
  let left = validId(restored.left ?? selection?.[0]) ? restored.left ?? selection[0]
    : validId(baselineId) ? baselineId : versions[0]?.id ?? '';
  const wantedRight = restored.right ?? selection?.[1];
  let right = wantedRight === '' || wantedRight === null ? '' : validId(wantedRight) && wantedRight !== left ? wantedRight
    : wantedRight === undefined && validId(bestId) && bestId !== left ? bestId : '';
  let clock = Number.isFinite(restored.time) ? Math.max(0, restored.time) : 0;
  let speed = SPEEDS.includes(Number(restored.speed)) ? Number(restored.speed) : 1;
  let audio = ['left', 'right', 'none'].includes(restored.audio) ? restored.audio : 'left';
  if (!right && audio === 'right') audio = 'left';
  let playing = false, pendingPlay = false, frame = null, epoch = 0, destroyed = false;
  let anchorTime = 0, anchorWall = 0, restorePlaying = restored.playing === true, pendingSeek = null;
  let media = [], mediaListeners = [];
  const listeners = [];
  const element = node(doc, 'section', 'method-comparison');
  const selectors = node(doc, 'div', 'comparison-selectors form-row');
  const grid = node(doc, 'div', 'comparison-grid');
  const transport = node(doc, 'div', 'comparison-transport');
  const actions = node(doc, 'div', 'comparison-actions detail-actions');
  const notice = node(doc, 'p', 'small muted', '按两版原始秒数同步；不同剪辑的内容不一定在同一秒对应。短片结束后停留在末帧。');
  const status = node(doc, 'p', 'small muted'); status.setAttribute('role', 'status'); status.dataset.role = 'comparison-status';
  const wire = (target, type, handler, temporary = false) => {
    target.addEventListener(type, handler);
    (temporary ? mediaListeners : listeners).push([target, type, handler]);
  };
  const safeUrl = value => {
    if (typeof assetUrl !== 'function') return null;
    try { const result = assetUrl(value, root); return typeof result === 'string' && result ? result : null; }
    catch { return null; }
  };
  const snapshot = () => ({left, right, audio, time: clock, speed, playing: playing || pendingPlay || restorePlaying});
  const changed = () => { if (!destroyed) onSelectionChange(snapshot()); };
  function selectControl(side, labelText) {
    const label = node(doc, 'label', '', labelText), select = node(doc, 'select');
    select.dataset.role = 'comparison-' + side; select.setAttribute('aria-label', labelText);
    if (side === 'right') { const option = node(doc, 'option', '', '不比较 · 单版查看'); option.value = ''; select.append(option); }
    if (side === 'left' && !versions.length) { const option = node(doc, 'option', '', '尚无版本'); option.value = ''; select.append(option); }
    for (const candidate of versions) {
      const option = node(doc, 'option', '', (text(candidate.title) || text(candidate.label) || '样片 ' + (versions.indexOf(candidate) + 1))
        + (candidate.id === bestId ? ' · 当前最佳' : '') + (candidate.id === baselineId ? ' · 优化基线' : ''));
      option.value = candidate.id; select.append(option);
    }
    select.value = side === 'left' ? left : right; select.disabled = !versions.length;
    label.append(select); selectors.append(label);
    wire(select, 'change', () => {
      const resume = playing || pendingPlay;
      pause(); restorePlaying = resume; pendingSeek = null;
      if (side === 'left') { left = validId(select.value) ? select.value : left; if (right === left) right = ''; }
      else right = validId(select.value) && select.value !== left ? select.value : '';
      leftSelect.value = left; rightSelect.value = right;
      if (!right && audio === 'right') audio = 'left';
      rebuildMedia(); changed();
    });
    return select;
  }
  const leftSelect = selectControl('left', '左侧样片'), rightSelect = selectControl('right', '右侧样片');
  function control(role, label, handler) {
    const value = node(doc, 'button', 'button secondary', label); value.type = 'button'; value.dataset.role = role;
    wire(value, 'click', handler); actions.append(value); return value;
  }
  const playButton = control('comparison-play', '播放', () => playing || pendingPlay ? pause() : void play());
  const resetButton = control('comparison-reset', '归零', () => { pause(); seekClock(0); });
  const previousButton = control('comparison-previous', '−1帧', () => step(-1));
  const nextButton = control('comparison-next', '+1帧', () => step(1));
  const seekInput = node(doc, 'input'); seekInput.type = 'range'; seekInput.min = '0'; seekInput.step = String(1 / FPS);
  seekInput.dataset.role = 'comparison-seek'; seekInput.setAttribute('aria-label', '两版公共原始时间');
  const timecode = node(doc, 'output', 'comparison-time'); timecode.dataset.role = 'comparison-time';
  const speedLabel = node(doc, 'label', '', '速度'), speedSelect = node(doc, 'select'); speedSelect.dataset.role = 'comparison-speed';
  for (const value of SPEEDS) { const option = node(doc, 'option', '', value + '×'); option.value = String(value); speedSelect.append(option); }
  speedSelect.value = String(speed); speedSelect.setAttribute('aria-label', '比较播放速度'); speedLabel.append(speedSelect); actions.append(speedLabel);
  const audioLabel = node(doc, 'label', '', '音效'), audioSelect = node(doc, 'select'); audioSelect.dataset.role = 'comparison-audio';
  for (const [value, label] of [['left', '仅左侧'], ['right', '仅右侧'], ['none', '静音']]) {
    const option = node(doc, 'option', '', label); option.value = value; audioSelect.append(option);
  }
  audioSelect.setAttribute('aria-label', '比较音效来源'); audioLabel.append(audioSelect); actions.append(audioLabel);
  transport.append(actions, seekInput, timecode, notice, status); element.append(selectors, grid, transport);
  const duration = row => Number.isFinite(row.video.duration) && row.video.duration > 0 ? row.video.duration : 0;
  const total = () => Math.max(0, ...media.map(duration));
  const ready = () => media.length > 0 && media.length === (left ? 1 : 0) + (right ? 1 : 0)
    && media.every(row => !row.error && row.video.readyState >= 1 && duration(row) > 0);
  const canPlay = () => ready() && media.every(row => row.video.readyState >= 2);
  const boundedTime = (row, value) => Math.min(Math.max(0, duration(row) - 1 / FPS), Math.max(0, value));
  const alignTime = (row, target) => {
    if (Math.abs(row.video.currentTime - target) > 0.001) row.video.currentTime = target;
  };
  function update() {
    if (destroyed) return;
    const available = ready(), max = total();
    playButton.disabled = !canPlay(); playButton.textContent = pendingPlay ? '正在开始…' : playing ? '暂停' : '播放';
    for (const control of [resetButton, previousButton, nextButton, seekInput, speedSelect]) control.disabled = !available;
    audioSelect.disabled = !media.length;
    audioSelect.value = audio;
    for (const option of audioSelect.children) if (option.value === 'right') option.disabled = !right;
    seekInput.max = String(max); seekInput.value = String(clock);
    timecode.textContent = clock.toFixed(3) + ' / ' + max.toFixed(3) + ' 秒 · ' + FPS + ' fps 步进';
    if (!available && !status.textContent) status.textContent = media.length ? '等待实际媒体加载完成后，可使用同步播放与定位。' : '尚无实际可播放媒体，播放与时间定位暂不可用。';
  }
  function applyAudio() {
    for (const row of media) { row.video.muted = audio === 'none' || audio !== row.side; row.video.playbackRate = speed; }
    audioSelect.value = audio;
  }
  function pause() {
    epoch++; restorePlaying = false; playing = false; pendingPlay = false;
    if (frame !== null) cancelFrame(frame);
    frame = null;
    for (const row of media) row.video.pause();
    update(); changed();
  }
  function seekClock(value) {
    if (destroyed || !ready()) return false;
    clock = Math.min(total(), Math.max(0, Number(value) || 0));
    for (const row of media) row.video.currentTime = boundedTime(row, clock);
    update(); onTimeChange(clock, snapshot()); changed(); return true;
  }
  function step(delta) { pause(); seekClock((Math.round(clock * FPS) + delta) / FPS); }
  async function play() {
    if (destroyed || pendingPlay || playing || !canPlay()) return false;
    restorePlaying = false;
    if (clock >= total() - 1 / FPS) seekClock(0);
    const started = [...media], currentEpoch = ++epoch;
    pendingPlay = true; status.textContent = ''; applyAudio();
    for (const row of started) alignTime(row, boundedTime(row, clock));
    update(); changed();
    try {
      await Promise.all(started.filter(row => clock < duration(row) - 1 / FPS).map(row => row.video.play()));
    } catch {
      if (!destroyed && currentEpoch === epoch) { pause(); status.textContent = '浏览器未能开始播放，已暂停两侧。可核对媒体后再次播放。'; }
      for (const row of started) if (destroyed || currentEpoch === epoch || !media.includes(row)) row.video.pause();
      return false;
    }
    if (destroyed || currentEpoch !== epoch) { for (const row of started) if (destroyed || !media.includes(row)) row.video.pause(); return false; }
    pendingPlay = false; playing = true; anchorTime = clock; anchorWall = now(); update(); changed();
    const tick = wall => {
      if (destroyed || !playing || currentEpoch !== epoch) return;
      clock = Math.min(total(), anchorTime + Math.max(0, wall - anchorWall) / 1000 * speed);
      for (const row of media) {
        const target = boundedTime(row, clock);
        if (clock >= duration(row) - 1 / FPS) { row.video.pause(); alignTime(row, target); }
        else if (Math.abs(row.video.currentTime - target) > .09) row.video.currentTime = target;
      }
      update(); onTimeChange(clock, snapshot());
      if (clock >= total()) { pause(); return; }
      frame = scheduleFrame(tick);
    };
    frame = scheduleFrame(tick); return true;
  }
  function disposeMedia() {
    for (const [target, type, handler] of mediaListeners) target.removeEventListener(type, handler);
    mediaListeners = [];
    for (const row of media) { row.video.pause(); row.video.removeAttribute('src'); row.video.load(); }
    media = [];
  }
  function rebuildMedia() {
    disposeMedia(); grid.replaceChildren();
    grid.className = 'comparison-grid' + (right ? '' : ' single');
    for (const [side, id] of [['left', left], ['right', right]]) {
      if (side === 'right' && !id) continue;
      const candidate = versions.find(value => value.id === id);
      const card = node(doc, 'article', 'comparison-card'), header = node(doc, 'header');
      header.append(node(doc, 'strong', '', (side === 'left' ? '左侧 · ' : '右侧 · ') + (text(candidate?.title) || '尚无样片')));
      const url = safeUrl(candidate?.media_url ?? candidate?.media?.url), stage = node(doc, 'div', 'media-stage');
      if (url) {
        const video = node(doc, 'video'); video.preload = 'auto'; video.playsInline = true; video.controls = false;
        video.setAttribute('aria-label', (side === 'left' ? '左侧' : '右侧') + '实际样片');
        const row = {id, side, video, error: false}; media.push(row);
        const loaded = () => {
          if (destroyed || !media.includes(row)) return;
          if (ready() && !playing && !pendingPlay) {
            if (pendingSeek) {
              const target = media.find(current => current.id === pendingSeek.candidateId);
              if (target) clock = boundedTime(target, pendingSeek.time);
              pendingSeek = null;
            }
            clock = Math.min(clock, total());
            for (const current of media) alignTime(current, boundedTime(current, clock));
            status.textContent = ''; onTimeChange(clock, snapshot());
          }
          update();
          if (restorePlaying && canPlay()) { restorePlaying = false; void play(); }
        };
        for (const event of ['loadedmetadata', 'loadeddata', 'canplay', 'durationchange']) wire(video, event, loaded, true);
        wire(video, 'error', () => { row.error = true; pause(); status.textContent = '实际媒体加载失败，已暂停；没有自动重新生成。'; }, true);
        video.src = url; stage.append(video);
        const download = node(doc, 'a', 'source-link', '下载视频'); download.href = url;
        download.download = 'animation-' + id.replace(/[^a-zA-Z0-9._-]/g, '_') + '.mp4'; header.append(download);
      } else { stage.classList.add('media-empty'); stage.append(node(doc, 'p', 'muted', '尚无实际可播放媒体')); }
      const source = safeUrl(candidate?.source_url);
      if (source) { const download = node(doc, 'a', 'source-link', '下载源码'); download.href = source; download.download = 'animation-source.json'; header.append(download); }
      card.append(header, stage); grid.append(card);
    }
    status.textContent = ''; applyAudio(); update();
  }
  wire(seekInput, 'input', () => { const requestedTime = seekInput.value; pause(); seekClock(requestedTime); });
  wire(speedSelect, 'change', () => {
    const value = Number(speedSelect.value); if (!SPEEDS.includes(value)) return;
    if (playing) { anchorTime = clock; anchorWall = now(); }
    speed = value; applyAudio(); update(); changed();
  });
  wire(audioSelect, 'change', () => {
    if (['left', 'right', 'none'].includes(audioSelect.value) && (audioSelect.value !== 'right' || right)) audio = audioSelect.value;
    applyAudio(); changed();
  });
  rebuildMedia();
  return {
    element, getSelection: snapshot,
    getTime(candidateId) { const row = media.find(value => value.id === candidateId); return row && ready() ? boundedTime(row, clock) : null; },
    seek(candidateId, time) {
      if (destroyed || !validId(candidateId) || !Number.isFinite(Number(time))) return false;
      const candidate = versions.find(value => value.id === candidateId);
      if (!safeUrl(candidate?.media_url ?? candidate?.media?.url)) return false;
      pause();
      pendingSeek = {candidateId, time: Math.max(0, Number(time))}; clock = pendingSeek.time;
      if (candidateId !== left && candidateId !== right) { left = candidateId; if (right === left) right = ''; leftSelect.value = left; rightSelect.value = right; rebuildMedia(); changed(); }
      const row = media.find(value => value.id === candidateId);
      if (row && ready()) { pendingSeek = null; return seekClock(boundedTime(row, Number(time))); }
      update(); changed(); return Boolean(row);
    },
    destroy() {
      if (destroyed) return;
      destroyed = true; epoch++; restorePlaying = false; playing = false; pendingPlay = false; pendingSeek = null;
      if (frame !== null) cancelFrame(frame);
      frame = null;
      for (const [target, type, handler] of listeners) target.removeEventListener(type, handler);
      listeners.length = 0; disposeMedia();
    },
  };
}
