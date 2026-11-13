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

test('the release UI starts with no assumed confirmations, one player per exact validation and lazy Skill reads', async () => {
  const h = rendered(fixture());
  assert.equal(h.calls.length, 0);
  assert.equal(h.result.descendants('video').length, 2);
  assert.ok(h.result.descendants('video').every(video => video.preload === 'none' && !video.muted));
  assert.ok(h.result.descendants('input').every(input => !input.checked));
  assert.ok(h.result.descendants('button').filter(button => button.textContent === '认可这条确切样片').every(button => button.disabled));
  assert.equal(findButton(h.result, '发布已认可的方法版本').disabled, true);
  const cards = h.result.descendants('article').filter(card => card.dataset.validationId);
  assert.equal(cards[0].descendants('input').length, 3);
  assert.equal(cards[1].descendants('input').length, 6);
  assert.match(cards[0].textContent, /无需试听/); assert.match(cards[1].textContent, /试听这条样片/);
  confirmCard(cards[0]); const approve = findButton(cards[0], '认可这条确切样片');
  assert.equal(approve.disabled, false); approve.handlers.click(); await settle();
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].path, 'api/method-lab/validations/reference-validation/accept');
  assert.equal(JSON.parse(h.calls[0].options.body).human_verification.audio_listened, false);
  assert.deepEqual(h.refreshed, ['commission']);
  assert.equal(h.calls.some(call => call.path.endsWith('/publish')), false);
});

test('a displayed acknowledgement cannot accept a silently changed method, validation or media', async () => {
  for (const change of [detail => { detail.methods[0].content_sha256 = hash('3'); },
    detail => { detail.validations[0].acceptance_target_sha256 = hash('4'); },
    detail => { detail.validations[0].evidence.media_sha256 = hash('5'); }]) {
    const detail = fixture(), current = structuredClone(detail);
    const h = rendered(detail, {currentDetail: () => current});
    const card = h.result.descendants('article').find(row => row.dataset.validationId === 'reference-validation');
    confirmCard(card); change(current); findButton(card, '认可这条确切样片').handlers.click(); await settle();
    assert.equal(h.calls.length, 0); assert.match(h.result.textContent, /已经更新|版本发生变化/);
  }
});

test('a lost acceptance response stops mutation until an explicit refresh; it never retries or publishes automatically', async () => {
  let calls = 0;
  const h = rendered(fixture(), {request: async () => { calls++; throw Error('Synthetic ack loss'); }});
  const card = h.result.descendants('article').find(row => row.dataset.validationId === 'reference-validation');
  confirmCard(card); const accept = findButton(card, '认可这条确切样片'); accept.handlers.click(); await settle();
  assert.equal(calls, 1); assert.equal(accept.disabled, true); assert.match(h.result.textContent, /没有自动重试/);
  accept.handlers.click(); await settle(); assert.equal(calls, 1);
});

test('an explicit successful GET unlocks an unchanged view and retries the original exact request after ack loss', async () => {
  const posts = [], reads = []; let sequence = 0;
  const h = rendered(fixture(), {uuid: () => 'request-' + (++sequence), request: async (path, options) => {
    posts.push({path, body: JSON.parse(options.body)});
    if (posts.length === 1) throw Error('Synthetic connection loss before admission');
    return {};
  }, refresh: async id => reads.push(id)});
  const card = h.result.descendants('article').find(row => row.dataset.validationId === 'reference-validation');
  confirmCard(card); const accept = findButton(card, '认可这条确切样片');
  accept.handlers.click(); await settle(); assert.equal(accept.disabled, true);
  await findButton(h.result, '刷新发布检查').handlers.click();
  assert.deepEqual(reads, ['commission']); assert.equal(posts.length, 1); assert.equal(accept.disabled, false);
  accept.handlers.click(); await settle();
  assert.equal(posts.length, 2); assert.deepEqual(posts[1], posts[0]);
  assert.equal(accept.disabled, true);
});

test('a failed refresh cannot unlock an uncertain confirmation', async () => {
  let requests = 0;
  const h = rendered(fixture(), {request: async () => { requests++; throw Error('Synthetic ack loss'); },
    refresh: async () => { throw Error('Synthetic GET failure'); }});
  const card = h.result.descendants('article').find(row => row.dataset.validationId === 'reference-validation');
  confirmCard(card); const accept = findButton(card, '认可这条确切样片');
  accept.handlers.click(); await settle(); await findButton(h.result, '刷新发布检查').handlers.click();
  assert.equal(accept.disabled, true); assert.equal(requests, 1); assert.match(h.result.textContent, /GET failure/);
});

test('Skill contents are requested only on demand and published exact versions require a separate release decision', async () => {
  const detail = fixture({accepted: true, ready: true}), h = rendered(detail, {request: async (path, options) => {
    h.calls.push({path, options}); return {...detail.methods[0], skill_package: {sha256: hash('c'), files: {'SKILL.md': 'Synthetic exact Skill.'}}};
  }});
  findButton(h.result, '读取这个版本的 Skill').handlers.click(); await settle();
  assert.deepEqual(h.calls.map(call => call.path), ['api/method-lab/methods/method-v2']);
  assert.match(h.result.textContent, /Synthetic exact Skill/);
  const release = h.result.descendants('input').find(input => input.type === 'checkbox');
  assert.equal(release.checked, false); assert.equal(findButton(h.result, '发布已认可的方法版本').disabled, true);
  release.checked = true; release.handlers.change(); findButton(h.result, '发布已认可的方法版本').handlers.click(); await settle();
  const post = h.calls.find(call => call.options?.method === 'POST');
  assert.equal(post.path, 'api/method-lab/commissions/commission/publish');
  assert.equal(Object.hasOwn(JSON.parse(post.options.body), 'human_verification'), false);
});

test('gallery details wire v2 to the exact panel while retaining the effect library and historical details', () => {
  const detail = fixture();
  const view = renderMethodDetail(doc, detail, root, {onBack() {}, onContinue() {}, onCancel() {}, onFeedback() {}, onPublish() {}});
  assert.equal(view.descendants('section').filter(row => row.dataset.exactSkillPublication === 'true').length, 1);
  assert.equal(findButton(view, '认可并发布方法'), undefined);
  assert.match(view.textContent, /返回效果库/);
});
