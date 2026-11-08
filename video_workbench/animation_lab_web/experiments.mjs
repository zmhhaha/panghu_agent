import {experimentIdentity, experimentLocalUrl, experimentVersions, experimentPlayable,
  experimentComparison, experimentFrameTime, experimentIssuePayload,
  experimentSourceLabel, experimentStatusLabel, experimentTextDifference, experimentTechnicalLabel} from './experiment-model.mjs';

const $ = id => document.getElementById(id);
const root = new URL('./', import.meta.url);
let listing = {experiments: []};
let current = null;
let serial = 0;
let regressionModule = null;
let regressionLoading = null;
let activeView = 'experiments';
let selected = [];
let media = [];
let clock = 0;
let playing = false;
let playbackFrame = null;
let startWall = 0;
let startClock = 0;
let saving = false;
let playbackEpoch = 0;
const drafts = new Map();
const CAPABILITIES = {'identity-tracking': '移动中的身份追踪', identity_tracking: '移动中的身份追踪',
  motion: '运动与节奏', layout: '布局与避让', continuity: '跨镜承接', morph: '连续形变'};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}

function message(id, text = '', error = false) {
  const element = $(id);
  element.textContent = text;
  element.hidden = !text;
  element.classList.toggle('error', error);
}

async function request(path, options = {}) {
  const headers = {Accept: 'application/json'};
  if (options.body) Object.assign(headers, {'Content-Type': 'application/json',
    'X-Animation-Lab': '1', 'X-Animation-Lab-Token': listing.csrf_token ?? ''});
  const response = await fetch(new URL(path.replace(/^\//, ''), root),
    {...options, headers, credentials: 'same-origin', cache: 'no-store'});
  let value;
  try { value = await response.json(); } catch { throw Error(`服务响应无法读取（HTTP ${response.status}）`); }
  if (!response.ok) throw Error(typeof value.message === 'string' ? value.message
    : typeof value.error === 'string' ? value.error : `HTTP ${response.status}`);
  return value;
}

function captureDraft() {
  if (!current) return;
  drafts.set(current.id, {comment: $('experiment-feedback-comment').value,
    start: $('experiment-feedback-start').value, end: $('experiment-feedback-end').value,
    version: $('experiment-feedback-version').value});
}

function restoreDraft() {
  const draft = drafts.get(current?.id);
  $('experiment-feedback-comment').value = draft?.comment ?? '';
  $('experiment-feedback-start').value = draft?.start ?? '0';
  $('experiment-feedback-end').value = draft?.end ?? '0';
  if (draft?.version && selected.some(row => row.id === draft.version)) $('experiment-feedback-version').value = draft.version;
}

function totalDuration() { return Math.max(0, ...media.map(row => row.version.duration_seconds)); }
function fps() { return Number(current?.constraints?.fps) || 24; }

function pause() {
  playbackEpoch++;
  playing = false;
  if (playbackFrame !== null) cancelAnimationFrame(playbackFrame);
  playbackFrame = null;
  for (const row of media) row.element.pause();
  $('experiment-play').textContent = '播放';
}

function updateTime() {
  $('experiment-seek').value = String(clock);
  $('experiment-time').textContent = `${clock.toFixed(3)} / ${totalDuration().toFixed(3)} 秒`;
}

function seek(value) {
  clock = Math.min(totalDuration(), Math.max(0, Number(value) || 0));
  for (const row of media) {
    const target = experimentFrameTime(clock, row.version.duration_seconds, fps());
    if (row.element.readyState > 0) row.element.currentTime = target;
  }
  updateTime();
}

async function play() {
  if (!media.length || media.some(row => row.error || row.element.readyState < 2)) {
    message('experiment-feedback-message', '作品尚未加载完成，或媒体无法播放。请先核对作品卡上的状态。', true); return;
  }
  if (clock >= totalDuration() - 1 / fps()) seek(0);
  for (const row of media) row.element.currentTime = experimentFrameTime(clock, row.version.duration_seconds, fps());
  const epoch = ++playbackEpoch;
  const requestedMedia = [...media];
  playing = true;
  $('experiment-play').textContent = '正在开始…';
  try {
    await Promise.all(requestedMedia.filter(row => clock < row.version.duration_seconds - 1 / fps()).map(row => row.element.play()));
  } catch {
    if (epoch !== playbackEpoch) return;
    pause(); message('experiment-feedback-message', '浏览器未能开始播放，已暂停；请检查媒体状态后再试。', true); return;
  }
  if (epoch !== playbackEpoch || activeView !== 'experiments') {
    for (const row of requestedMedia) if (!media.includes(row)) row.element.pause();
    return;
  }
  playing = true; startClock = clock; startWall = performance.now();
  $('experiment-play').textContent = '暂停';
  const tick = now => {
    if (!playing || epoch !== playbackEpoch) return;
    clock = Math.min(totalDuration(), startClock + (now - startWall) / 1000);
    for (const row of media) {
      const target = experimentFrameTime(clock, row.version.duration_seconds, fps());
      if (clock >= row.version.duration_seconds - 1 / fps()) {
        row.element.pause(); row.element.currentTime = target;
      } else if (Math.abs(row.element.currentTime - target) > .09) row.element.currentTime = target;
    }
    updateTime();
    if (clock >= totalDuration()) { pause(); return; }
    playbackFrame = requestAnimationFrame(tick);
  };
  playbackFrame = requestAnimationFrame(tick);
}

function renderList() {
  const target = $('experiment-list'); target.replaceChildren();
  const experiments = Array.isArray(listing.experiments) ? listing.experiments : [];
  $('experiment-count').textContent = String(experiments.length);
  if (!experiments.length) target.append(node('p', '还没有能力实验定义。', 'muted small'));
  for (const experiment of experiments) {
    const button = node('button', undefined, 'case-button'); button.type = 'button';
    button.setAttribute('aria-current', String(experiment.id === current?.id));
    button.append(node('span', CAPABILITIES[experiment.capability] ?? experiment.capability ?? '能力实验', 'case-category'),
      node('strong', experiment.title ?? experiment.id),
      node('span', experimentStatusLabel(experiment.status), 'case-description'));
    button.addEventListener('click', () => selectExperiment(experiment.id));
    target.append(button);
  }
}

function jsonText(value) {
  return typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2);
}

function metadataDetails(version) {
  const details = node('details', undefined, 'experiment-version-details');
  details.append(node('summary', '提示词、代码与执行来源（只读）'));
  const text = node('div');
  const provenance = version.provenance ?? {};
  const facts = {source: version.source, prompt_revision: version.prompt_revision,
    input_role: version.input_role, version_id: version.id, ...provenance};
  text.append(node('h4', '执行来源'), node('pre', jsonText(facts)));
  if (typeof version.prompt_text === 'string') text.append(node('h4', '实际提示词'), node('pre', version.prompt_text));
  else text.append(node('p', version.source === 'agent' ? '未提供实际提示词文本，请核对原始执行记录。' : '该作品没有模型提示词。', 'muted small'));
  if (version.input_document) text.append(node('h4', '该次实际输入（含提示词指导）'), node('pre', jsonText(version.input_document)));
  const codeFiles = version.code_files;
  if (codeFiles && typeof codeFiles === 'object') {
    for (const [name, content] of Object.entries(codeFiles)) {
      const source = typeof content === 'string' ? content : content?.text;
      if (typeof source !== 'string') continue;
      text.append(node('h4', name), node('pre', source));
    }
  }
  details.append(text);
  return details;
}

function renderVersion(version, index) {
  const article = node('article', undefined, 'experiment-media-card');
  const header = node('header');
  const hasGeneratedSource = Boolean(version.code_files && Object.keys(version.code_files).length);
  const sourceLabel = version.source === 'agent' && !hasGeneratedSource && !version.media_url ? 'Agent 执行记录 · 尚无生成产物' : experimentSourceLabel(version.source);
  header.append(node('strong', `${index === 0 ? '左侧' : '右侧'} · ${version.label ?? version.id}`),
    node('span', sourceLabel, 'experiment-badge'));
  article.append(header);
  article.append(node('p', `${version.prompt_revision ? `提示词 ${version.prompt_revision} · ` : ''}${experimentStatusLabel(version.status)}`, 'muted small'));
  article.append(node('p', experimentTechnicalLabel(version), version.technical?.status === 'failed' ? 'experiment-technical failed' : 'experiment-technical'));
  if (Array.isArray(version.technical?.issues) && version.technical.issues.length) {
    article.append(node('p', `技术问题：${version.technical.issues.join('、')}`, 'experiment-technical failed'));
  }
  if (experimentPlayable(version, root)) {
    const video = node('video'); video.preload = 'auto'; video.muted = true; video.playsInline = true;
    video.setAttribute('aria-label', `${version.label ?? version.id} 实际渲染视频`);
    const source = node('source'); source.src = experimentLocalUrl(version.media_url, root); source.type = 'video/mp4';
    video.append(source);
    const status = node('p', '正在加载媒体…', 'muted small');
    const row = {version, element: video, error: false}; media.push(row);
    video.addEventListener('loadeddata', () => { status.textContent = '媒体已加载，可播放或逐帧定位。'; });
    video.addEventListener('loadedmetadata', () => { video.currentTime = experimentFrameTime(clock, version.duration_seconds, fps()); });
    video.addEventListener('error', () => { row.error = true; pause(); status.textContent = '媒体加载失败；没有自动重新生成。'; status.classList.add('error'); });
    source.addEventListener('error', () => { row.error = true; pause(); status.textContent = '视频文件无法读取，请核对该次渲染记录。'; status.classList.add('error'); });
    article.append(video, status);
    const download = node('a', '下载实际视频', 'text-button');
    download.href = source.src; download.download = `${version.id}.mp4`; article.append(download);
  } else {
    article.append(node('div', '这次执行没有可播放媒体。', 'experiment-media-unavailable'));
    if (version.error || version.failure) article.append(node('pre', jsonText(version.error ?? version.failure)));
  }
  if (version.strategy) article.append(node('p', typeof version.strategy === 'string' ? version.strategy : jsonText(version.strategy), 'experiment-strategy'));
  const report = experimentLocalUrl(version.report_url, root);
  if (report) {
    const link = node('a', '查看原始检查报告 ↗', 'text-button');
    link.href = report; link.target = '_blank'; link.rel = 'noopener noreferrer'; article.append(link);
  }
  article.append(metadataDetails(version));
  return article;
}

function chosenVersions(versions) {
  const selectors = $('experiment-version-selectors').querySelectorAll('select');
  return [...selectors].map(select => versions.find(row => row.id === select.value)).filter(Boolean)
    .filter((row, index, values) => values.findIndex(value => value.id === row.id) === index);
}

function renderMedia(versions) {
  pause();
  for (const row of media) { row.element.removeAttribute('src'); row.element.replaceChildren(); row.element.load(); }
  media = []; clock = 0;
  selected = chosenVersions(versions);
  $('experiment-media').replaceChildren(...selected.map(renderVersion));
  $('experiment-media').classList.toggle('single', selected.length < 2);
  $('experiment-transport').hidden = !media.length;
  $('experiment-seek').max = String(totalDuration()); $('experiment-seek').step = String(1 / fps());
  updateTime();
  $('experiment-playback-note').textContent = media.length
    ? `公共时间控制 ${media.length} 个媒体，逐帧步长 1/${fps()} 秒；较短版本停在末帧。画面不包含真实旁白。` : '';
  const comparison = experimentComparison(selected[0], selected[1]);
  $('experiment-preferences').hidden = !comparison.allowed || media.length < 2;
  $('experiment-comparison-note').textContent = selected.length > 1 ? comparison.reason : '单作品探索：先观察表达机制，不需要虚构一个对比基线。';
  $('experiment-diff').hidden = selected.length !== 2;
  if (selected.length === 2) {
    const actualText = version => jsonText({context: version.prompt_text ?? null, input: version.input_document ?? null});
    const codeText = version => jsonText(version.code_files ?? null);
    $('experiment-diff-text').textContent = `提示词上下文与实际输入\n${experimentTextDifference(actualText(selected[0]), actualText(selected[1]))}\n\n场景代码\n${experimentTextDifference(codeText(selected[0]), codeText(selected[1]))}`;
  }
  $('experiment-feedback-version').replaceChildren(...selected.map(version => {
    const option = node('option', version.label ?? version.id); option.value = version.id; return option;
  }));
  $('experiment-feedback-save').disabled = saving || !selected.length;
  restoreDraft();
  setFeedbackMaximum();
}

function renderResults() {
  const allVersions = Array.isArray(current?.versions) ? current.versions : [];
  $('experiment-definition-only').hidden = allVersions.length > 0;
  $('experiment-results').hidden = allVersions.length === 0;
  if (!allVersions.length) { media = []; selected = []; return; }
  const roleSelect = $('experiment-input-role');
  const availableRoles = new Set(allVersions.map(row => row.input_role ?? 'development'));
  for (const option of roleSelect.options) option.disabled = !availableRoles.has(option.value);
  if (!availableRoles.has(roleSelect.value)) roleSelect.value = availableRoles.has('development') ? 'development' : 'held_out';
  const versions = experimentVersions(current, roleSelect.value);
  const selectors = $('experiment-version-selectors'); selectors.replaceChildren();
  for (let index = 0; index < Math.min(versions.length, 2); index++) {
    const label = node('label', index === 0 ? '左侧作品' : '右侧作品'); const select = node('select');
    if (index === 1) { const single = node('option', '不比较 · 单作品探索'); single.value = ''; select.append(single); }
    for (const version of versions) {
      const option = node('option', `${version.label ?? version.id}${version.prompt_revision ? ` · ${version.prompt_revision}` : ''}`);
      option.value = version.id; select.append(option);
    }
    select.value = versions[index].id;
    select.addEventListener('change', () => { captureDraft(); renderMedia(versions); renderFeedback(); }); label.append(select); selectors.append(label);
  }
  renderMedia(versions);
}

function renderFeedback() {
  const target = $('experiment-feedback-history'); target.replaceChildren();
  const feedback = Array.isArray(current?.feedback) ? current.feedback : [];
  $('experiment-feedback-count').textContent = String(feedback.length);
  if (!feedback.length) target.append(node('p', '尚无实际作品反馈。技术检查不会自动生成审美评价。', 'muted small'));
  for (const entry of [...feedback].reverse()) {
    const article = node('article', undefined, 'annotation-item');
    const names = (entry.version_ids ?? []).map(id => current.versions?.find(row => row.id === id)?.label ?? id).join(' / ');
    const title = entry.kind === 'preference' ? ({left: '左侧更好', right: '右侧更好', equal: '差异不明显', neither: '都不理想'})[entry.preference] ?? '比较意见' : '具体问题';
    article.append(node('strong', `${title}${names ? ` · ${names}` : ''}`));
    if (Number.isFinite(entry.start_seconds)) {
      const jump = node('button', `${entry.start_seconds.toFixed(3)}—${Number(entry.end_seconds ?? entry.start_seconds).toFixed(3)} 秒`, 'text-button'); jump.type = 'button';
      const identityMatches = !entry.experiment_identity || entry.experiment_identity === experimentIdentity(current);
      jump.disabled = !identityMatches || !selected.some(row => entry.version_ids?.includes(row.id));
      jump.addEventListener('click', () => { pause(); seek(entry.start_seconds); }); article.append(jump);
    }
    if (entry.comment) article.append(node('p', entry.comment));
    if (entry.created_at) article.append(node('p', new Date(typeof entry.created_at === 'number' ? entry.created_at * 1000 : entry.created_at).toLocaleString('zh-CN'), 'muted small'));
    target.append(article);
  }
}

function renderExperiment() {
  pause();
  $('experiment-empty').hidden = Boolean(current); $('experiment-workspace').hidden = !current;
  if (!current) return;
  $('experiment-title').textContent = current.title ?? current.id;
  $('experiment-capability').textContent = CAPABILITIES[current.capability] ?? current.capability ?? '能力问题';
  $('experiment-goal').textContent = current.goal ?? '';
  $('experiment-status').textContent = experimentStatusLabel(current.status);
  $('experiment-mode').textContent = ({exploration: '开放探索', explore: '开放探索', comparison: '受控比较', compare: '受控比较'})[current.mode] ?? '探索与版本比较';
  const sources = [...new Set((current.versions ?? []).map(row => row.source))];
  const anyOutput = (current.versions ?? []).some(row => row.media_url || (row.code_files && Object.keys(row.code_files).length));
  $('experiment-source').textContent = !anyOutput && sources.includes('agent')
    ? 'Agent 执行记录 · 尚无生成产物' : sources.length ? sources.map(experimentSourceLabel).join(' / ') : '尚无产物';
  const change = current.change_source ?? current.changed_factor;
  $('experiment-change').textContent = ({prompt: '提示词版本', component: '组件版本', appearance: '外观参数', combined: '组合探索'})[change] ?? '以实际运行记录为准';
  $('experiment-constraints').textContent = jsonText(current.constraints);
  const rejected = Array.isArray(current.rejected_runs) ? current.rejected_runs : [];
  message('experiment-rejected-runs', rejected.length ? `有 ${rejected.length} 条记录未能载入作品：\n${rejected.map(row => `${row.id}：${row.reason ?? '资料不完整'}`).join('\n')}` : '', true);
  renderResults(); renderFeedback(); renderList(); restoreDraft();
}

async function selectExperiment(id) {
  captureDraft(); pause(); const operation = ++serial;
  try {
    const result = await request(`api/experiments/${encodeURIComponent(id)}`);
    if (operation !== serial) return;
    const experiment = result.experiment ?? result;
    if (experiment.id !== id || !experimentIdentity(experiment)) throw Error('实验详情缺少匹配的冻结身份');
    current = experiment; message('experiment-message'); message('experiment-feedback-message');
    renderExperiment();
    const url = new URL(location.href); url.hash = `experiment-${encodeURIComponent(id)}`; history.replaceState(null, '', url);
  } catch (error) { if (operation === serial) message('experiment-message', `无法读取实验：${error.message}。`, true); }
}

async function loadExperiments() {
  captureDraft(); pause(); $('experiment-reload').disabled = true;
  try {
    const result = await request('api/experiments');
    if (!Array.isArray(result.experiments)) throw Error('实验目录不完整');
    listing = result; renderList();
    const hashId = location.hash.startsWith('#experiment-') ? decodeURIComponent(location.hash.slice(12)) : '';
    const selectedId = result.experiments.find(row => row.id === current?.id || row.id === hashId)?.id ?? result.experiments[0]?.id;
    if (selectedId) { if (activeView === 'experiments') await selectExperiment(selectedId); }
    else { current = null; renderExperiment(); $('experiment-empty').querySelector('h2').textContent = '还没有能力实验定义'; }
    message('experiment-message');
  } catch (error) { message('experiment-message', `无法读取能力实验：${error.message}。可刷新核对；没有自动执行模型任务。`, true); }
  finally { $('experiment-reload').disabled = false; }
}

function setFeedbackMaximum() {
  const version = selected.find(row => row.id === $('experiment-feedback-version').value);
  for (const id of ['experiment-feedback-start', 'experiment-feedback-end']) $(id).max = String(version?.duration_seconds ?? 0);
}

async function saveFeedback(payload) {
  if (!current || saving) return false;
  const id = current.id, identity = experimentIdentity(current); captureDraft(); pause(); saving = true;
  $('experiment-feedback-save').disabled = true;
  for (const button of document.querySelectorAll('[data-experiment-preference]')) button.disabled = true;
  try {
    await request(`api/experiments/${encodeURIComponent(id)}/feedback`, {method: 'POST', body: JSON.stringify(payload)});
    if (current?.id !== id || experimentIdentity(current) !== identity) return true;
    const result = await request(`api/experiments/${encodeURIComponent(id)}`);
    if (current?.id === id && experimentIdentity(current) === identity) {
      current = result.experiment ?? result;
      if (payload.kind === 'issue') { drafts.delete(id); $('experiment-feedback-comment').value = ''; }
      renderFeedback();
      message('experiment-feedback-message', '意见已保存并绑定本次作品；提示词和组件的修改需要实际执行与复测。');
    }
    return true;
  } catch (error) {
    message('experiment-feedback-message', `保存或读回未完成：${error.message}。未自动重试，草稿保留；刷新反馈后再核对。`, true); return false;
  } finally {
    saving = false; $('experiment-feedback-save').disabled = !selected.length;
    for (const button of document.querySelectorAll('[data-experiment-preference]')) button.disabled = false;
  }
}

async function showView(view, updateHash = true) {
  if (!['experiments', 'regressions'].includes(view)) return;
  pause(); captureDraft();
  if (regressionModule) document.dispatchEvent(new Event('animation-lab-regression-pause'));
  activeView = view;
  const regression = view === 'regressions';
  if (regression) serial++;
  $('experiment-lab').hidden = regression; $('regression-lab').hidden = !regression;
  $('regression-actions').hidden = !regression; $('global-message').hidden = true;
  $('show-experiments').setAttribute('aria-pressed', String(!regression));
  $('show-regressions').setAttribute('aria-pressed', String(regression));
  if (regression) {
    if (updateHash && (!location.hash || location.hash.startsWith('#experiment-'))) history.replaceState(null, '', '#regressions');
    if (!regressionLoading) regressionLoading = import('./app.mjs').then(value => { regressionModule = value; });
    try { await regressionLoading; } catch (error) { message('global-message', `回归工具加载失败：${error.message}`, true); }
  } else {
    if (updateHash) history.replaceState(null, '', current ? `#experiment-${encodeURIComponent(current.id)}` : '#experiments');
    if (!listing.experiments.length || !current) await loadExperiments();
  }
}

function boot() {
  $('show-experiments').addEventListener('click', () => showView('experiments'));
  $('show-regressions').addEventListener('click', () => showView('regressions'));
  $('experiment-reload').addEventListener('click', loadExperiments);
  $('experiment-input-role').addEventListener('change', () => { captureDraft(); renderResults(); renderFeedback(); });
  $('experiment-play').addEventListener('click', () => playing ? pause() : play());
  $('experiment-reset').addEventListener('click', () => { pause(); seek(0); });
  $('experiment-previous').addEventListener('click', () => { pause(); seek(clock - 1 / fps()); });
  $('experiment-next').addEventListener('click', () => { pause(); seek(clock + 1 / fps()); });
  $('experiment-seek').addEventListener('input', event => { pause(); seek(event.target.value); });
  $('experiment-feedback-version').addEventListener('change', setFeedbackMaximum);
  $('experiment-capture-time').addEventListener('click', () => {
    pause(); const version = selected.find(row => row.id === $('experiment-feedback-version').value);
    const value = Math.min(clock, version?.duration_seconds ?? 0).toFixed(3);
    $('experiment-feedback-start').value = value; $('experiment-feedback-end').value = value;
  });
  $('experiment-feedback-form').addEventListener('submit', event => {
    event.preventDefault();
    const version = selected.find(row => row.id === $('experiment-feedback-version').value);
    const payload = experimentIssuePayload(current, version, {start_seconds: $('experiment-feedback-start').value,
      end_seconds: $('experiment-feedback-end').value, comment: $('experiment-feedback-comment').value});
    if (!payload) { message('experiment-feedback-message', '请写具体意见，并填写作品时长范围内的有效开始、结束时间。', true); return; }
    void saveFeedback(payload);
  });
  for (const button of document.querySelectorAll('[data-experiment-preference]')) {
    button.addEventListener('click', () => {
      if (!experimentComparison(selected[0], selected[1]).allowed || media.length !== 2) return;
      void saveFeedback({experiment_identity: experimentIdentity(current), kind: 'preference',
        version_identities: Object.fromEntries(selected.map(row => [row.id, row.identity])),
        version_ids: selected.map(row => row.id), comment: '', start_seconds: 0,
        end_seconds: totalDuration(), preference: button.dataset.experimentPreference});
    });
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });
  window.addEventListener('pagehide', () => { pause(); captureDraft(); });
  window.addEventListener('hashchange', () => {
    if (location.hash === '#regressions') void showView('regressions', false);
    else if (location.hash.startsWith('#experiment-')) {
      void showView('experiments', false);
      let id; try { id = decodeURIComponent(location.hash.slice(12)); } catch { return; }
      if (current?.id !== id && listing.experiments.some(row => row.id === id)) void selectExperiment(id);
    }
  });
  const legacyHash = location.hash && !location.hash.startsWith('#experiment') && location.hash !== '#regressions';
  void showView(location.hash === '#regressions' || legacyHash ? 'regressions' : 'experiments', false);
}

if (typeof document !== 'undefined') boot();
