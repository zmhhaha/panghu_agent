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

// Historical exact-binding helpers above remain readable for saved receipts.
// New production knowledge is released once, from the whole current library.
export function renderExactSkillPublication(doc, detail, root) {
  const section = node(doc, 'section', undefined, 'panel publication skill-publication');
  section.dataset.exactSkillPublication = 'true';
  section.append(node(doc, 'h3', '发布到知识库'), node(doc, 'p',
    '在知识库点击“发布新版本”，冻结当前效果库并由 Agent 提炼整套 WikiSkill。这里的样片、意见及真实执行结果将作为来源；未验证结论不会自动成为已证实能力。', 'muted'));
  const link = node(doc, 'a', '打开知识库', 'button secondary');
  link.href = new URL('knowledge.html', root).href;
  section.append(link);
  return section;
}
