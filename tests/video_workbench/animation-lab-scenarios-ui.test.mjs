import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {
  filterAnimationCases, animationFacetOptions, animationCoverage,
  animationEngineLabel, animationRequirementRows, animationRequirementsUrl,
} from '../../video_workbench/animation_lab_web/model.mjs';

const fixtures = [
  {id: 'chart-load', module: 'charts', domain: 'computing', domain_label: '计算机', engine: 'svg',
    title: '负载与延迟', description: '流量增加，时延上升', identity: 'a', is_matrix_case: true,
    tags: ['吞吐'], narrative: '队列变长时，等待也会增加。'},
  {id: 'chart-power', module: 'charts', domain: 'energy', domain_label: '能源', engine: 'svg',
    title: '供需缺口', identity: 'b', is_matrix_case: true},
  {id: 'interface-book', module: 'interface', domain: 'education', domain_label: '教育', engine: 'html',
    title: '馆藏搜索', identity: 'c', is_matrix_case: true},
  {id: 'domain-canvas-clinic', module: 'canvas', domain: 'computing', domain_label: '计算机', engine: 'canvas',
    title: '队列演示', identity: 'd', is_matrix_case: true},
  {id: 'frozen-evaluation', module: 'charts', domain: 'computing', engine: 'svg', title: '冻结评测',
    identity: 'e', evaluation: {id: 'eval-r1'}},
];

test('module/domain/engine/search/review filters intersect without an unrelated fallback', () => {
  const reviewed = new Set(['chart-load:a', 'chart-power:old-identity']);
  const select = filters => filterAnimationCases(fixtures, filters, reviewed).map(row => row.id);
  assert.deepEqual(select({module: 'charts'}), ['chart-load', 'chart-power']);
  assert.deepEqual(select({module: 'charts', domain: 'computing', engine: 'svg', query: '吞吐', review: 'reviewed'}), ['chart-load']);
  assert.deepEqual(select({module: 'charts', domain: 'energy', engine: 'html'}), []);
  assert.deepEqual(select({module: 'interface', query: '负载'}), []);
  assert.deepEqual(select({query: '等待'}), ['chart-load']);
  assert.deepEqual(select({review: 'reviewed'}), ['chart-load']);
  assert.deepEqual(select({review: 'unreviewed', module: 'charts'}), ['chart-power']);
  assert.deepEqual(select({review: 'unknown'}), []);
  assert.deepEqual(select({module: 'all', domain: 'all', engine: 'all'}), fixtures.slice(0, 4).map(row => row.id));
});

test('coverage counts the active requirement matrix and excludes separate frozen evaluations', () => {
  const coverage = animationCoverage(fixtures, [{id: 'charts', label: '动态图表'}]);
  assert.equal(coverage.total, 4);
  assert.equal(coverage.matrixCases, 4);
  assert.equal(coverage.modules.length, 3);
  assert.equal(coverage.domains.length, 3);
  assert.equal(coverage.engines.length, 3);
  const charts = coverage.modules.find(row => row.id === 'charts');
  assert.equal(charts.label, '动态图表');
  assert.equal(charts.cases, 2);
  assert.equal(charts.domains.length, 2);
  assert.deepEqual(charts.engines, [{id: 'svg', label: 'SVG', count: 2}]);
  assert.equal(animationFacetOptions(fixtures, 'domain').find(row => row.id === 'computing').count, 2);
  assert.deepEqual(animationFacetOptions(fixtures, 'unknown'), []);
});

test('malformed metadata cannot execute conversion hooks or manufacture an evaluation sample', () => {
  const bad = {toString() { throw Error('must not invoke metadata conversion'); }};
  assert.doesNotThrow(() => filterAnimationCases([null, {}, {id: 'x', title: bad, description: bad, tags: [bad]}], {query: 'x'}));
  assert.deepEqual(filterAnimationCases(null), []);
  assert.equal(animationEngineLabel('__proto__'), '__proto__');
  assert.equal(animationEngineLabel(null), '未分类');
  assert.deepEqual(animationRequirementRows({requirement: []}), []);
});

test('requirement download URL stays under the same developer prefix and encodes values', () => {
  const root = new URL('https://video-service.panghuer.top/dev/animations/');
  const result = new URL(animationRequirementsUrl(root, {module: 'charts', domain: 'energy', engine: 'svg', query: 'not-exported'}));
  assert.equal(result.origin, root.origin);
  assert.equal(result.pathname, '/dev/animations/api/requirements');
  assert.equal(result.searchParams.get('seed'), '20261107');
  assert.equal(result.searchParams.get('module'), 'charts');
  assert.equal(result.searchParams.get('domain'), 'energy');
  assert.equal(result.searchParams.get('engine'), 'svg');
  assert.equal(result.searchParams.has('query'), false);
  const attack = new URL(animationRequirementsUrl(root, {domain: '<img src=x onerror=alert(1)>&engine=evil'}));
  assert.equal(attack.searchParams.get('domain'), '<img src=x onerror=alert(1)>&engine=evil');
  assert.equal(attack.searchParams.get('engine'), 'all');
  for (const base of ['javascript:alert(1)', 'file:///tmp/demo/', 'https://user:secret@example.com/']) {
    assert.equal(animationRequirementsUrl(base), null);
  }
  assert.equal(animationRequirementsUrl(root, {}, NaN), null);
  assert.equal(animationRequirementsUrl(root, {}, -1), null);
  assert.equal(new URL(animationRequirementsUrl(root, {}, 0)).searchParams.get('seed'), '0');
  assert.equal(new URL(animationRequirementsUrl(root, {}, 2**32-1)).searchParams.get('seed'), '4294967295');
  assert.equal(animationRequirementsUrl(root, {}, 2**32), null);
  assert.equal(animationRequirementsUrl(root, {}, Number.MAX_SAFE_INTEGER), null);
});

test('temporarily empty filters preserve pending warmup and restore the same frozen preview', async () => {
  const app = readFileSync(new URL('../../video_workbench/animation_lab_web/app.mjs', import.meta.url), 'utf8');
  const functionText = name => {
    const match = new RegExp(`^(?:async )?function ${name}\\([^\\n]*\\) \\{[\\s\\S]*?^\\}`, 'm').exec(app);
    assert.ok(match, `missing application function: ${name}`);
    return match[0];
  };
  const row = {id: 'interface-book', identity: 'frozen-input-and-artifact'};
  const controller = new AbortController();
  const frame = {src: ''};
  const selectedPlayers = [{key: 'candidate', ready: false, error: null, frame,
    variant: {id: 'candidate'}, previewUrl: 'https://video-service.panghuer.top/dev/animations/preview/interface-book/candidate/'}];
  let finishWarmup, matches = [], warmupCalls = 0, captured = 0, paused = 0;
  const pending = new Promise(resolve => { finishWarmup = resolve; });
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {hidden: false, textContent: '', children: new Map(),
      querySelector(name) { if (!this.children.has(name)) this.children.set(name, {textContent: ''}); return this.children.get(name); }});
    return elements.get(id);
  };
  const context = vm.createContext({
    $: get, currentCase: row, players: selectedPlayers, previewWarmController: controller,
    caseEpoch: 3, evaluationBusy: false, caseIdentity: value => value.identity,
    filteredCases: () => matches, captureDraft: () => { captured++; }, pause: () => { paused++; },
    renderCaseList() {}, location: {origin: 'https://video-service.panghuer.top'}, loadTimers: new Set(),
    setTimeout: () => 1, failPlayer: (_, message) => { throw Error(message); },
    warmPreviewAssets: async (_, options) => {
      warmupCalls++; await pending;
      if (options.signal.aborted) throw new DOMException('aborted', 'AbortError');
    },
  });
  vm.runInContext([functionText('preparePlayers'), functionText('selectCase'), functionText('applyCaseFilters')].join('\n'), context);
  const preparation = vm.runInContext('preparePlayers(players, caseEpoch, previewWarmController)', context);
  vm.runInContext('applyCaseFilters()', context);
  assert.equal(get('case-workspace').hidden, true);
  assert.equal(get('empty-state').hidden, false);
  assert.equal(controller.signal.aborted, false);
  matches = [row];
  vm.runInContext('applyCaseFilters()', context);
  assert.equal(get('case-workspace').hidden, false);
  assert.equal(get('empty-state').hidden, true);
  assert.equal(frame.src, '');
  finishWarmup(); await preparation;
  assert.equal(selectedPlayers[0].previewStarted, true);
  assert.equal(frame.src, selectedPlayers[0].previewUrl);
  assert.equal(warmupCalls, 1, 'filtering must not silently restart downloads');
  assert.equal(captured, 2); assert.equal(paused, 2);
});

test('untrusted requirement strings are inserted as text nodes in the real requirement renderer', () => {
  const payload = '<img src=x onerror=alert(1)>';
  const malicious = {domain_label: payload, engine: 'html', narrative: payload,
    requirement: {audience: payload, goal: payload, questions: [payload], required_entities: [payload], expected_outcome: payload, boundaries: [payload]}};
  const rows = animationRequirementRows(malicious);
  assert.equal(rows.length, 7);
  assert.ok(rows.every(row => row.values[0] === payload));
  const app = readFileSync(new URL('../../video_workbench/animation_lab_web/app.mjs', import.meta.url), 'utf8');
  const functionText = name => {
    const marker = `function ${name}(`, start = app.indexOf(marker), next = app.indexOf('\nfunction ', start + marker.length);
    assert.ok(start >= 0);
    return app.slice(start, next < 0 ? undefined : next);
  };
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this._text = ''; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(row => row.textContent).join(''); }
    set innerHTML(_) { throw Error('HTML parsing of requirement content is forbidden'); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = [...children]; this._text = ''; }
  }
  const nodes = new Map(['case-requirement', 'case-requirement-meta', 'case-requirement-body'].map(id => [id, new Element('div')]));
  vm.runInNewContext(`${functionText('node')}\n${functionText('renderCaseRequirement')}\nrenderCaseRequirement(row);`, {
    $: id => nodes.get(id), document: {createElement: tag => new Element(tag)},
    animationRequirementRows, animationEngineLabel, row: malicious,
  });
  assert.equal(nodes.get('case-requirement').hidden, false);
  assert.ok(nodes.get('case-requirement-body').textContent.includes(payload));
  const descendants = element => [element, ...element.children.flatMap(descendants)];
  assert.ok(descendants(nodes.get('case-requirement-body')).every(element => element.tag !== 'img' && element.tag !== 'script'));
});
