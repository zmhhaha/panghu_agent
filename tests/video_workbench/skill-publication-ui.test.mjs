import test from 'node:test';
import assert from 'node:assert/strict';
import {usesExactSkillPublication, exactValidationBinding, exactPublicationBinding,
  buildExactValidationAcceptance, buildExactPublicationRequest, renderExactSkillPublication,
} from '../../video_workbench/animation_lab_web/skill-publication.mjs';
import {renderMethodDetail, methodAssetUrl} from '../../video_workbench/animation_lab_web/method-gallery.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const requestId = '00000000-0000-4000-8000-000000000001';
const hash = char => char.repeat(64);
function fixture({accepted = false, ready = false} = {}) {
  const method = {schema: 'video.animation-method.v2', id: 'method-v2', version: 2, title: '追踪因果关系', status: 'candidate',
    content_sha256: hash('a'), criteria_sha256: hash('b'), skill_package_sha256: hash('c'),
    criteria: [{id: 'identity', description: '对象身份在动作中可追踪', requires_motion: true},
      {id: 'cause', description: '动作与结果的因果关系清晰', requires_motion: true}]};
  const validation = (role, audio) => ({schema: 'video.animation-method-validation.v2', id: role + '-validation', role,
    method_version_id: method.id, method_content_sha256: method.content_sha256, criteria_sha256: method.criteria_sha256,
    demonstration_version_id: role + '-demo', acceptance_target_sha256: hash(role === 'reference' ? 'd' : 'e'),
    evidence: {media_sha256: hash(role === 'reference' ? 'f' : '1')}, outcome: 'incomplete',
    requires_audio_verification: audio, audio_sha256: audio ? hash('2') : null,
    ...(role === 'transfer' ? {content_changes: {before: '分组筛选', after: '资源分配', reason: '新表达问题'},
      structural_changes: {relations: {before: '单链', after: '竞争分支'}},
      reference_removal_summary: {scope: 'exact_host_records_retained_in_private_validation_store'}} : {})});
  const validations = [validation('reference', false), validation('transfer', true)];
  if (accepted) for (const row of validations) row.acceptance = {method_content_sha256: method.content_sha256,
    acceptance_target_sha256: row.acceptance_target_sha256, media_sha256: row.evidence.media_sha256,
    comment: 'TEST FIXTURE explicit synthetic developer decision'};
  return {id: 'commission', title: '完整表达片段', state: 'completed', candidates: [{id: 'clip', title: '代表片段',
    demonstration_id: 'reference-demo', media_url: '/api/method-lab/commissions/commission/candidates/clip/media',
    technical_status: 'passed'}], best_candidate_id: 'clip', methods: [method], validations,
    publication_method_version_ids: [method.id], method_publication_readiness: [{method_version_id: method.id,
      ready, historical_contract: false, release_contract: 'video.animation-method-publication.v2',
      unmet_conditions: ready ? [] : ['animation_method_representative_acceptance_required'],
      reference_validation_ids: ready ? ['reference-validation'] : [], transfer_validation_ids: ready ? ['transfer-validation'] : []}],
    transfers: [{id: 'transfer-validation', title: '迁移片段', goal: '解释竞争分配', demonstration_id: 'transfer-demo',
      media_url: '/api/method-lab/commissions/transfer/candidates/second/media'}], criteria: method.criteria,
    requires_audio_verification: true};
}
const confirmations = (extra = {}) => ({criteria_ids: ['identity', 'cause'], motion_watched: true,
  audio_listened: true, content_and_structure_changed: true, reference_free_reuse: true,
  comment: 'TEST FIXTURE: 已按标准核对完整动作及其因果。', ...extra});

test('exact confirmation binds each validation, method, media and criterion without sharing audio decisions', () => {
  const detail = fixture();
  assert.equal(usesExactSkillPublication(detail), true);
  const silent = buildExactValidationAcceptance(detail, 'reference-validation', confirmations(), requestId);
  assert.deepEqual(silent.human_verification, {criteria_ids: ['identity', 'cause'], motion_watched: true,
    audio_listened: false, validation_target_sha256: hash('d'), media_sha256: hash('f')});
  const audible = buildExactValidationAcceptance(detail, 'transfer-validation', confirmations(), requestId);
  assert.deepEqual(audible.human_verification, {criteria_ids: ['identity', 'cause'], motion_watched: true,
    audio_listened: true, validation_target_sha256: hash('e'), media_sha256: hash('1'),
    content_and_structure_changed: true, reference_free_reuse: true});
  assert.equal(audible.method_content_sha256, hash('a'));
  assert.notEqual(exactValidationBinding(detail, 'reference-validation').audio_sha256,
    exactValidationBinding(detail, 'transfer-validation').audio_sha256);
});

test('incomplete, duplicate, silent or unobserved confirmations cannot create acceptance', () => {
  const detail = fixture();
  for (const checks of [confirmations({criteria_ids: ['identity']}), confirmations({criteria_ids: ['identity', 'identity']}),
    confirmations({motion_watched: false}), confirmations({audio_listened: false}),
    confirmations({content_and_structure_changed: false}), confirmations({reference_free_reuse: false}), confirmations({comment: ''})]) {
    assert.throws(() => buildExactValidationAcceptance(detail, 'transfer-validation', checks, requestId));
  }
  const missingTrack = structuredClone(detail); missingTrack.validations[1].audio_sha256 = null;
  assert.throws(() => buildExactValidationAcceptance(missingTrack, 'transfer-validation', confirmations(), requestId), /缺少.*音轨/);
  const changed = structuredClone(detail); changed.methods[0].content_sha256 = hash('3');
  assert.throws(() => buildExactValidationAcceptance(changed, 'reference-validation', confirmations(), requestId), /版本发生变化/);
});

test('publishing needs exact saved decisions and authoritative readiness and never signs on behalf of the developer', () => {
  assert.throws(() => buildExactPublicationRequest(fixture(), true, requestId), /逐条认可/);
  const detail = fixture({accepted: true, ready: true});
  assert.throws(() => buildExactPublicationRequest(detail, false, requestId), /明确确认/);
  const result = buildExactPublicationRequest(detail, true, requestId);
  assert.deepEqual(result, {request_id: requestId, method_version_ids: ['method-v2'],
    demonstration_ids: ['reference-demo', 'transfer-demo']});
  assert.equal(Object.hasOwn(result, 'human_verification'), false);
  const changed = structuredClone(detail); changed.validations[1].acceptance.media_sha256 = hash('4');
  assert.throws(() => buildExactPublicationRequest(changed, true, requestId), /媒体不一致/);
  assert.notDeepEqual(exactPublicationBinding(changed), exactPublicationBinding(detail));
});

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.handlers = {}; this.dataset = {}; this.attributes = {};
    this.className = ''; this._text = ''; this.value = ''; this.checked = false;
    this.classList = {add: value => { this.className += ' ' + value; }};
    this.readyState = 0; this.duration = NaN; this.currentTime = 0; this.paused = true;
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(row => row.textContent).join(''); }
  append(...children) { this.children.push(...children); for (const child of children) child.parentElement = this; }
  replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  removeEventListener(name) { delete this.handlers[name]; }
  descendants(tag = '') { return this.children.flatMap(child => [child, ...child.descendants()]).filter(child => !tag || child.tagName === tag.toUpperCase()); }
  querySelectorAll(tag) { return this.descendants(tag); }
  pause() { this.paused = true; }
  load() {}
}
const doc = {createElement: tag => new Element(tag), createDocumentFragment: () => new Element('fragment')};
const settle = () => new Promise(resolve => setImmediate(resolve));
function rendered(detail, overrides = {}) {
  const calls = [], refreshed = [];
  const result = renderExactSkillPublication(doc, detail, root, {assetUrl: methodAssetUrl,
    uuid: () => requestId, request: async (path, options) => { calls.push({path, options}); return {}; },
    refresh: async value => refreshed.push(value), ...overrides});
  return {result, calls, refreshed};
}
function findButton(host, label) { return host.descendants('button').find(button => button.textContent === label); }
function confirmCard(card) {
  for (const field of card.descendants('input').filter(field => field.type === 'checkbox')) { field.checked = true; field.handlers.change?.(); }
  const comment = card.descendants('textarea')[0]; comment.value = 'TEST FIXTURE: 完整动作及因果符合标准。'; comment.handlers.input();
}

test('effect detail links to the single whole-library release workflow without writes', () => {
  for (const detail of [fixture(), fixture({accepted: true, ready: true}), {id:'old', state:'failed'}]) {
    const h = rendered(detail);
    assert.equal(h.calls.length, 0);
    assert.equal(h.result.descendants('button').length, 0);
    assert.equal(h.result.descendants('input').length, 0);
    assert.equal(h.result.descendants('video').length, 0);
    const link = h.result.descendants('a')[0];
    assert.equal(link.textContent, '打开知识库');
    assert.equal(link.href, new URL('knowledge.html', root).href);
    assert.match(h.result.textContent, /整套 WikiSkill/);
    assert.match(h.result.textContent, /未验证结论/);
  }
});

test('all detail schemas use the same knowledge release entry and preserve the effect workflow', () => {
  for (const detail of [fixture(), {...fixture(), publication_method_version_ids: [], methods: []}]) {
    const view = renderMethodDetail(doc, detail, root, {onBack() {}, onContinue() {}, onCancel() {}, onFeedback() {}, onPublish() {}});
    assert.equal(view.descendants('section').filter(row => row.dataset.exactSkillPublication === 'true').length, 1);
    assert.equal(findButton(view, '认可并发布方法'), undefined);
    assert.equal(findButton(view, '发布已认可的方法版本'), undefined);
    assert.match(view.textContent, /返回效果库/);
    assert.equal(view.descendants('a').find(row => row.textContent === '打开知识库').href, new URL('knowledge.html', root).href);
  }
});
