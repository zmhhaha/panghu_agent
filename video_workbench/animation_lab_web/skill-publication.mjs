// Developer release decisions target exact Skill, validation and media bytes.
const API = 'api/method-lab/';
const METHOD = 'video.animation-method.v2';
const VALIDATION = 'video.animation-method-validation.v2';
const ACTIVE = new Set(['queued', 'running', 'planning', 'creating', 'reviewing', 'rendering', 'cancelling', 'submission_unknown']);
const array = value => Array.isArray(value) ? value : [];
const string = value => typeof value === 'string' ? value : '';
const id = value => string(value?.version_id) || string(value?.id);
const sha = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const canonical = value => JSON.stringify(value, (_key, row) => row && typeof row === 'object' && !Array.isArray(row)
  ? Object.fromEntries(Object.keys(row).sort().map(key => [key, row[key]])) : row);
const requireValue = (value, message) => { if (!value) throw Error(message); };
const GATES = {
  animation_method_representative_acceptance_required: '请逐条认可代表样片',
  animation_method_substantive_transfer_required: '需要完成内容与结构均不同的迁移验证',
  animation_method_content_and_structure_evidence_required: '迁移任务还缺内容与结构变化证据',
  animation_method_reference_free_execution_required: '迁移执行还缺不携带参考素材的记录',
  animation_method_transfer_acceptance_required: '请逐条认可迁移样片',
  animation_method_human_transfer_verification_required: '请确认迁移确实改变内容与结构，且脱离参考可复用',
  animation_method_v2_version_required: '此版本沿用历史发布方式',
};

export function usesExactSkillPublication(detail) {
  return array(detail?.publication_method_version_ids).some(version =>
    array(detail?.methods).some(method => id(method) === version && method.schema === METHOD));
}

function selectedMethods(detail) {
  return array(detail?.publication_method_version_ids).map(version => array(detail?.methods).find(method => id(method) === version));
}

function validationContext(detail, validationId) {
  const validation = array(detail?.validations).find(row => row.id === validationId);
  const method = selectedMethods(detail).find(row => row && id(row) === validation?.method_version_id);
  requireValue(method?.schema === METHOD && validation?.schema === VALIDATION,
    '请选择当前方法版本关联的确切样片');
  requireValue(['reference', 'transfer'].includes(validation.role) && validation.outcome !== 'failed',
    '这条样片尚未具备可认可的验证结果');
  requireValue(sha(method.content_sha256) && validation.method_content_sha256 === method.content_sha256
    && validation.criteria_sha256 === method.criteria_sha256 && sha(validation.acceptance_target_sha256),
  '方法或验证版本发生变化，请刷新核对');
  const sample = [...array(detail.candidates), ...array(detail.transfers)].find(row =>
    (row.demonstration_id ?? row.demonstration_version_id) === validation.demonstration_version_id);
  const mediaSha = validation.media_sha256 ?? validation.evidence?.media_sha256;
  requireValue(sample && sha(mediaSha), '尚无与这条验证绑定的完整媒体');
  const criteria = array(method.criteria);
  requireValue(criteria.length && criteria.every(row => string(row.id) && string(row.description))
    && new Set(criteria.map(row => row.id)).size === criteria.length, '方法的确切验收标准不完整');
  const audioRequired = typeof validation.requires_audio_verification === 'boolean'
    ? validation.requires_audio_verification : detail.requires_audio_verification !== false;
  return {validation, method, sample, mediaSha, criteria, audioRequired};
}

export function exactValidationBinding(detail, validationId) {
  const context = validationContext(detail, validationId), {validation, method, sample} = context;
  return {commission_id: detail.id, validation_id: validation.id, method_version_id: id(method),
    method_content_sha256: method.content_sha256, criteria_sha256: method.criteria_sha256,
    skill_package_sha256: method.skill_package_sha256 ?? method.skill_package?.sha256,
    acceptance_target_sha256: validation.acceptance_target_sha256,
    demonstration_version_id: validation.demonstration_version_id, media_sha256: context.mediaSha,
    media_url: sample.media_url ?? sample.media?.url, audio_sha256: validation.audio_sha256 ?? null,
    requires_audio_verification: context.audioRequired, role: validation.role};
}

export function buildExactValidationAcceptance(detail, validationId, checks, requestId) {
  const {validation, method, criteria, mediaSha, audioRequired} = validationContext(detail, validationId);
  requireValue(!ACTIVE.has(detail.state) && !detail.ui_preview && !validation.acceptance,
    '当前样片不能再次认可，请刷新确认状态');
  const wanted = criteria.map(row => row.id), checked = array(checks.criteria_ids);
  requireValue(checked.length === wanted.length && new Set(checked).size === wanted.length
    && checked.every(value => wanted.includes(value)), '请逐项确认此方法版本的全部标准');
  requireValue(checks.motion_watched === true, '请完整观看这条动画后明确确认');
  requireValue(!audioRequired || checks.audio_listened === true, '请试听这条样片的音效后明确确认');
  // If the API explicitly reports a missing required track, do not claim it was heard.
  requireValue(!audioRequired || !Object.hasOwn(validation, 'audio_sha256') || sha(validation.audio_sha256),
    '该标准要求音效，但这条样片缺少可核对的音轨');
  if (validation.role === 'transfer') requireValue(checks.content_and_structure_changed === true
    && checks.reference_free_reuse === true, '请核对内容、结构变化及脱离参考后的复用结果');
  requireValue(string(checks.comment).trim().length > 0 && checks.comment.length <= 16000,
    '请填写你的认可依据，或指出已核对的效果');
  requireValue(string(requestId).length > 0, '无法分配确认请求编号');
  const human = {criteria_ids: wanted, motion_watched: true, audio_listened: audioRequired && checks.audio_listened === true,
    validation_target_sha256: validation.acceptance_target_sha256, media_sha256: mediaSha};
  if (validation.role === 'transfer') Object.assign(human, {content_and_structure_changed: true, reference_free_reuse: true});
  return {request_id: requestId, method_content_sha256: method.content_sha256,
    comment: checks.comment.trim(), human_verification: human};
}

export function exactPublicationBinding(detail) {
  const methods = selectedMethods(detail);
  requireValue(methods.length && methods.every(method => method?.schema === METHOD),
    '当前发布目标不完整，请刷新核对');
  return {commission_id: detail.id, methods: methods.map(method => ({method_version_id: id(method),
    content_sha256: method.content_sha256, skill_package_sha256: method.skill_package_sha256 ?? method.skill_package?.sha256,
    readiness: array(detail.method_publication_readiness).find(row => row.method_version_id === id(method)),
    validations: array(detail.validations).filter(row => row.method_version_id === id(method)).map(row => ({id: row.id,
      acceptance_target_sha256: row.acceptance_target_sha256, acceptance: row.acceptance ?? null}))}))};
}

export function buildExactPublicationRequest(detail, acknowledged, requestId) {
  const methods = selectedMethods(detail), demonstrations = new Set();
  requireValue(acknowledged === true && !ACTIVE.has(detail?.state) && !detail?.ui_preview,
    '请明确确认发布这些已经逐条认可的方法版本');
  requireValue(methods.length && methods.every(method => method?.schema === METHOD), '当前发布版本不完整');
  requireValue(methods.some(method => method.status !== 'published'), '这些方法版本已经发布');
  for (const method of methods) {
    const ready = array(detail.method_publication_readiness).find(row => row.method_version_id === id(method));
    requireValue(ready?.ready === true && ready.historical_contract === false
      && ready.release_contract === 'video.animation-method-publication.v2'
      && !array(ready.unmet_conditions).length, '请先逐条认可样片，并通过服务器发布检查');
    const references = array(ready.reference_validation_ids), transfers = array(ready.transfer_validation_ids);
    requireValue(references.length && transfers.length, '代表样片或迁移验证尚未完整');
    for (const [ids, role] of [[references, 'reference'], [transfers, 'transfer']]) {
      for (const validationId of ids) {
        const context = validationContext(detail, validationId), validation = context.validation;
        const accepted = validation.acceptance;
        requireValue(id(context.method) === id(method) && validation.role === role && accepted
          && accepted.method_content_sha256 === method.content_sha256
          && accepted.acceptance_target_sha256 === validation.acceptance_target_sha256
          && accepted.media_sha256 === context.mediaSha, '认可记录与当前方法或媒体不一致，请刷新核对');
        demonstrations.add(validation.demonstration_version_id);
      }
    }
  }
  requireValue(string(requestId).length > 0, '无法分配发布请求编号');
  // A release never supplies human_verification or manufactures acceptances.
  return {request_id: requestId, method_version_ids: methods.map(id), demonstration_ids: [...demonstrations]};
}

function node(doc, tag, text, className = '') {
  const result = doc.createElement(tag); result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}
function action(doc, text, handler, primary = false) {
  const result = node(doc, 'button', text, 'button ' + (primary ? 'primary' : 'secondary'));
  result.type = 'button'; result.addEventListener('click', handler); return result;
}
function checkbox(doc, text) {
  const label = node(doc, 'label', undefined, 'checkbox-label'), input = node(doc, 'input');
  input.type = 'checkbox'; input.checked = false; label.append(input, node(doc, 'span', text));
  return {label, input};
}
function details(doc, title, value) {
  const section = node(doc, 'details', undefined, 'technical-details');
  section.append(node(doc, 'summary', title));
  if (value !== undefined) section.append(node(doc, 'pre', typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
  return section;
}

export function renderExactSkillPublication(doc, detail, root, {assetUrl, request,
  currentDetail = () => detail, refresh = async () => {}, uuid = () => crypto.randomUUID()} = {}) {
  const section = node(doc, 'section', undefined, 'panel publication skill-publication');
  section.dataset.exactSkillPublication = 'true';
  section.append(node(doc, 'h3', '逐条确认样片，再发布创作方法'), node(doc, 'p',
    '分别观看代表片段和迁移片段，按各自方法版本的标准确认。发布只使用已经记录的确切认可，不会代替你认可效果。', 'muted'));
  const notice = node(doc, 'p', '', 'message'); notice.hidden = true; notice.setAttribute('role', 'status');
  section.append(notice);
  let busy = false, uncertain = false;
  const controls = [], pending = new Map();
  const report = (message, error = false) => {
    notice.textContent = message; notice.hidden = false; notice.className = 'message' + (error ? ' error' : '');
  };
  const updateControls = () => { for (const control of controls) control.update(); };
  const send = async (key, path, body, expected, binding) => {
    if (busy || uncertain || !request) return false;
    try {
      requireValue(canonical(expected) === canonical(binding(currentDetail())),
        '样片或方法已经更新，请刷新后重新核对');
      const previous = pending.get(key);
      if (previous) requireValue(canonical({...previous, request_id: ''}) === canonical({...body, request_id: ''}),
        '上次确认结果尚待核对，请先刷新');
      else pending.set(key, body);
      busy = true; updateControls(); report('正在保存你的明确决定…');
      await request(path, {method: 'POST', body: JSON.stringify(pending.get(key))});
      pending.delete(key);
      uncertain = true; // Keep this old view inert until fresh server state replaces it.
      await refresh(detail.id);
      report('决定已保存。请以刷新后的认可及发布状态为准。');
      return true;
    } catch (error) {
      // A lost acknowledgement must not turn into a different automatic write.
      if (busy) uncertain = true;
      report(error.message + (busy ? '。请刷新核对结果，没有自动重试。' : ''), true);
      return false;
    } finally { busy = false; updateControls(); }
  };
  const reload = action(doc, '刷新发布检查', async () => {
    if (busy) return;
    try {
      await refresh(detail.id);
      requireValue(currentDetail()?.id === detail.id, '当前选择已变化，请重新打开这条效果');
      uncertain = false;
      updateControls();
      report('已读取服务器发布状态。若确认仍未保存，可以按原确认内容安全重试。');
    }
    catch (error) { report(error.message, true); }
  });
  section.append(reload);
  const methods = selectedMethods(detail).filter(method => method?.schema === METHOD);
  for (const method of methods) {
    const block = node(doc, 'article', undefined, 'method-item'); block.dataset.methodVersionId = id(method);
    block.append(node(doc, 'h4', (string(method.title) || '创作方法') + ' · v' + (method.version ?? '?')));
    const methodReady = array(detail.method_publication_readiness).find(row => row.method_version_id === id(method));
    block.append(node(doc, 'p', method.status === 'published' ? '此确切方法版本已发布；继续优化将形成新的候选版本。'
      : methodReady?.ready === true ? '逐条认可及服务器发布检查已完成，等待你的发布决定。'
        : '尚未发布。请先核对下方代表片段和迁移片段。', 'small muted'));
    const conditions = array(methodReady?.unmet_conditions);
    if (conditions.length) block.append(node(doc, 'p', conditions.map(code => GATES[code] ?? code).join('；'), 'small muted'));
    const versions = details(doc, '方法版本与发布检查', {method_version_id: id(method),
      method_content_sha256: method.content_sha256, skill_package_sha256: method.skill_package_sha256,
      criteria_sha256: method.criteria_sha256, readiness: methodReady ?? null});
    const source = details(doc, '按需查看 Skill 原文');
    const readSkill = action(doc, '读取这个版本的 Skill', async () => {
      if (!request || readSkill.disabled) return; readSkill.disabled = true;
      try {
        const value = await request(API + 'methods/' + encodeURIComponent(id(method)));
        requireValue(id(value) === id(method) && value.content_sha256 === method.content_sha256
          && value.skill_package?.sha256 === (method.skill_package_sha256 ?? method.skill_package?.sha256),
        '返回的 Skill 与当前版本不一致');
        for (const [path, contents] of Object.entries(value.skill_package.files ?? {})) {
          source.append(node(doc, 'h4', path), node(doc, 'pre', contents));
        }
      } catch (error) { report(error.message, true); readSkill.disabled = false; }
    });
    source.append(readSkill); versions.append(source); block.append(versions);
    const validations = array(detail.validations).filter(row => row.method_version_id === id(method) && row.schema === VALIDATION);
    const grid = node(doc, 'div', undefined, 'transfer-grid');
    for (const validation of validations) {
      const card = node(doc, 'article', undefined, 'transfer-item exact-validation');
      card.dataset.validationId = validation.id;
      card.append(node(doc, 'h4', validation.role === 'transfer' ? '迁移片段 · 内容与结构变化' : '代表片段 · 原目标表达'));
      let context;
      try { context = validationContext(detail, validation.id); }
      catch (error) { card.append(node(doc, 'p', error.message, 'muted')); grid.append(card); continue; }
      const {sample, criteria, audioRequired} = context, mediaUrl = assetUrl?.(sample.media_url ?? sample.media?.url, root);
      card.append(node(doc, 'p', sample.title ?? sample.goal ?? '', 'muted'));
      const media = node(doc, 'div', undefined, 'media-stage');
      if (mediaUrl) {
        const video = node(doc, 'video'); video.controls = true; video.preload = 'none'; video.playsInline = true;
        video.muted = false; video.src = mediaUrl; video.setAttribute('aria-label', (validation.role === 'transfer' ? '迁移' : '代表') + '完整片段'); media.append(video);
      } else media.append(node(doc, 'p', '没有可播放的受控媒体，不能认可这条样片。', 'muted'));
      card.append(media, node(doc, 'p', audioRequired ? '请播放完整动画，并试听这条样片的音效。'
        : '这条样片没有音效要求，无需试听；请完整观看动作过程。', 'small muted'));
      if (validation.role === 'transfer') {
        card.append(node(doc, 'p', '新目标：' + string(sample.goal), 'small'));
        card.append(details(doc, '核对内容与结构的变化', {content: validation.content_changes,
          structure: validation.structural_changes, reference_free_execution: validation.reference_removal_summary ?? null}));
      }
      card.append(details(doc, '确切媒体与验证版本', exactValidationBinding(detail, validation.id)));
      if (validation.acceptance) {
        card.append(node(doc, 'p', '开发者已明确认可此版本', 'badge published'),
          node(doc, 'p', string(validation.acceptance.comment), 'small muted'));
        const standards = node(doc, 'ul', undefined, 'criteria-list');
        for (const criterion of criteria) standards.append(node(doc, 'li', criterion.description));
        card.append(standards); grid.append(card); continue;
      }
      if (validation.outcome === 'incomplete') card.append(node(doc, 'p', '自动评审证据不完整，请根据完整样片逐项判断。', 'small muted'));
      const criterionChecks = criteria.map(criterion => {
        const field = checkbox(doc, criterion.description); field.input.dataset.criterionId = criterion.id;
        if (criterion.observation) field.label.append(node(doc, 'small', criterion.observation));
        card.append(field.label); return {id: criterion.id, ...field};
      });
      const motion = checkbox(doc, '我已完整观看这条动画，确认动作过程符合以上标准。');
      const audio = audioRequired ? checkbox(doc, '我已试听这条样片的音效，确认声音与动作的表达合适。') : null;
      card.append(motion.label); if (audio) card.append(audio.label);
      const changed = validation.role === 'transfer' ? checkbox(doc, '内容与结构均已改变，当前效果仍符合上述方法标准。') : null;
      const referenceFree = validation.role === 'transfer' ? checkbox(doc, '不依赖原参考画面或源码，这个方法也能完成新的表达。') : null;
      if (changed) card.append(changed.label, referenceFree.label);
      const label = node(doc, 'label', '认可依据'), comment = node(doc, 'textarea');
      comment.rows = 2; comment.maxLength = 16000; comment.placeholder = '写下你已核对的表达效果。'; label.append(comment); card.append(label);
      const checks = () => ({criteria_ids: criterionChecks.filter(field => field.input.checked).map(field => field.id),
        motion_watched: motion.input.checked, audio_listened: audio?.input.checked ?? false,
        content_and_structure_changed: changed?.input.checked ?? false,
        reference_free_reuse: referenceFree?.input.checked ?? false, comment: comment.value ?? ''});
      const expected = exactValidationBinding(detail, validation.id);
      const accept = action(doc, '认可这条确切样片', () => {
        try {
          const body = buildExactValidationAcceptance(detail, validation.id, checks(), uuid());
          void send('accept:' + validation.id, API + 'validations/' + encodeURIComponent(validation.id) + '/accept', body,
            expected, value => exactValidationBinding(value, validation.id));
        } catch (error) { report(error.message, true); }
      }, true);
      const update = () => {
        let valid = false;
        try { buildExactValidationAcceptance(detail, validation.id, checks(), 'check-only'); valid = true; } catch {}
        accept.disabled = busy || uncertain || !request || !mediaUrl || !valid;
      };
      controls.push({update});
      for (const field of [...criterionChecks.map(row => row.input), motion.input, audio?.input, changed?.input, referenceFree?.input].filter(Boolean)) field.addEventListener('change', update);
      comment.addEventListener('input', update);
      card.append(accept); grid.append(card);
    }
    if (!validations.length) grid.append(node(doc, 'p', '此方法尚无可核对的代表或迁移样片。', 'muted'));
    block.append(grid); section.append(block);
  }
  const release = checkbox(doc, '我确认将以上已逐条认可的方法版本发布，供之后的正式制作使用。');
  let publicationBinding = null;
  try { publicationBinding = exactPublicationBinding(detail); } catch {}
  const publish = action(doc, '发布已认可的方法版本', () => {
    try {
      const body = buildExactPublicationRequest(detail, release.input.checked, uuid());
      void send('publish', API + 'commissions/' + encodeURIComponent(detail.id) + '/publish', body,
        publicationBinding, exactPublicationBinding);
    } catch (error) { report(error.message, true); }
  }, true);
  const updateRelease = () => {
    let ready = false;
    try { buildExactPublicationRequest(detail, true, 'check-only'); ready = true; } catch {}
    release.input.disabled = busy || uncertain || !request || !ready;
    publish.disabled = release.input.disabled || !release.input.checked;
  };
  controls.push({update: updateRelease}); release.input.addEventListener('change', updateRelease);
  section.append(release.label, publish); updateControls(); return section;
}
