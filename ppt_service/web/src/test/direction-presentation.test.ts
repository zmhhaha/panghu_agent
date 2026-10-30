import { describe, expect, it } from 'vitest';
import type { Direction } from '../domain';
import { presentDirection } from '../directionPresentation';
import { adaptDirections } from '../planning';
import backendPlan from './fixtures/backend-stage2-api-plan-unconfirmed.json';

const direction = (changes: Partial<Direction> = {}): Direction => ({
  id: 'unrelated-identifier', title: '设计方向', description: '', pages: [], production_settings: {}, ...changes,
});

describe('direction presentation from actual plan values', () => {
  it('presents the real three palettes, reference labels and page counts without manufacturing recommendations', () => {
    const directions = adaptDirections(backendPlan.design.directions)!;
    const presented = directions.map(presentDirection);
    expect(presented.map(value => value.layout)).toEqual(['editorial', 'console', 'steps']);
    expect(presented.map(value => value.styleLabels)).toEqual([['编辑排版'], ['深色科技'], ['柔和圆角']]);
    expect(presented.map(value => value.modeLabels)).toEqual([['简报'], ['简报'], ['教学引导']]);
    expect(presented.map(value => value.pageCount)).toEqual([6, 6, 6]);
    for (const [index, source] of backendPlan.design.directions.entries()) {
      expect(presented[index].paletteName).toBe(source.color.name);
      expect(presented[index].palette).toHaveLength(6);
      expect(presented[index].colors).toEqual({
        background: source.color.palette.background, foreground: source.color.palette.body_text,
        accent: source.color.palette.accent, surface: source.color.palette.secondary_bg,
      });
      expect(presented[index].summary).toBe(source.note.split('。')[0] + '。');
      expect(presented[index].palette.find(value => value.key === 'primary')).toMatchObject({
        label: '主色', color: source.color.palette.primary,
      });
    }
    for (const source of [...directions].reverse()) {
      expect(presentDirection({ ...source, id: 'soft-rounded', title: '深色科技' })).toEqual(presentDirection(source));
    }
  });

  it('leaves missing values empty and does not infer a theme from identifiers, prose or production settings', () => {
    const presented = presentDirection(direction({ id: 'editorial', title: 'dark-tech', description: '深色技术报告。',
      production_settings: { color: { palette: { background: '#111923' } }, visual_style: 'dark-tech' },
      pages: [{ number: 1, title: '实际页面' }],
    }));
    expect(presented).toEqual({ summary: '深色技术报告。', paletteName: '', palette: [], colors: {},
      styleLabels: [], modeLabels: [], pageCount: 1, layout: 'neutral' });
    expect(presentDirection(direction({ description: 'custom', details: {
      mode: 'custom', visual_style: 'custom', color: { name: { note: '不是名称' }, palette: { note: '不是颜色' } },
    } })).styleLabels).toEqual([]);
  });

  it('unwraps evidence values and only maps actual semantic color keys', () => {
    const presented = presentDirection(direction({ details: {
      color: { value: { name: { value: '白、墨与绿' }, palette: { value: {
        background: { value: '#fFf' }, body_text: '#123', primary: '#abcdef', chart_series: '#0a0',
        accent: '#11223344', value: '#456',
      } } } },
      visual_style: { value: 'custom' }, visual_style_references: { value: [{ value: 'editorial' }] },
      mode: { value: 'custom' }, mode_references: { value: ['briefing'] },
    } }));
    expect(presented.paletteName).toBe('白、墨与绿');
    expect(presented.colors).toEqual({ background: '#FFFFFF', foreground: '#112233', accent: '#ABCDEF' });
    expect(presented.palette).toEqual([
      { key: 'background', label: '背景', color: '#FFFFFF' },
      { key: 'body_text', label: '正文', color: '#112233' },
      { key: 'primary', label: '主色', color: '#ABCDEF' },
      { key: 'chart_series', label: 'chart_series', color: '#00AA00' },
      { key: 'value', label: 'value', color: '#445566' },
    ]);
    expect(presented.styleLabels).toEqual(['编辑排版']);
    expect(presented.modeLabels).toEqual(['简报']);
    expect(presented.layout).toBe('editorial');
  });

  it('rejects CSS expressions and partial colors and caps swatches at six actual safe colors', () => {
    const hostile = ['#1234', '#12345678', '#abc;', '#abc\n', '#aabbcc url(https://example.invalid)',
      'var(--accent)', 'rgb(0,0,0)', 'url(javascript:alert(1))', 'red', ' #abc'];
    const presented = presentDirection(direction({ details: {
      color: { name: '<img src=x onerror=alert(1)>', palette: {
        background: 'url(javascript:alert(1))', body_text: 'var(--text)', primary: '#abc;display:none',
        ...Object.fromEntries(hostile.map((value, index) => [`bad-${index}`, value])),
        ...Object.fromEntries(Array.from({ length: 8 }, (_, index) => [`chart-${index}`, '#abc'])),
      } },
      visual_style: '<script>alert(1)</script>', mode_references: ['var(--x)', 'url(javascript:alert(1))'],
    } }));
    expect(presented.colors).toEqual({});
    expect(presented.paletteName).toBe('');
    expect(presented.palette).toHaveLength(6);
    expect(presented.palette.every(value => value.key.startsWith('chart-') && value.color === '#AABBCC')).toBe(true);
    expect(presented.styleLabels).toEqual([]);
    expect(presented.modeLabels).toEqual([]);
    expect(presented.layout).toBe('neutral');
  });

  it('uses exact references for layout while keeping safe unknown labels as ordinary text', () => {
    for (const visual_style of ['dark-tech', 'digital-dashboard']) {
      expect(presentDirection(direction({ details: { visual_style } })).layout).toBe('console');
    }
    const unknown = presentDirection(direction({ details: {
      visual_style: 'editorial-like', visual_style_references: ['custom', '纸面阅读', 'x'.repeat(61), { note: 'raw' }],
      mode: 'custom', mode_references: ['briefing', 'briefing', '循序讲解'],
    } }));
    expect(unknown.styleLabels).toEqual(['editorial-like', '纸面阅读']);
    expect(unknown.modeLabels).toEqual(['简报', '循序讲解']);
    expect(unknown.layout).toBe('neutral');
    const instructional = presentDirection(direction({ details: { mode: 'instructional' } }));
    expect(instructional.layout).toBe('steps');
    expect(instructional.styleLabels).toEqual([]);
    expect(instructional.modeLabels).toEqual(['教学引导']);
    expect(presentDirection(direction({ details: { visual_style: 'editorial', mode: 'instructional' } })).layout).toBe('editorial');
  });

  it('retains the first complete sentence and truncates long prose without adding meaning', () => {
    expect(presentDirection(direction({ description: '  保留原来的完整第一句。第二句仍在原方案中。  ' })).summary).toBe('保留原来的完整第一句。');
    expect(presentDirection(direction({ description: 'Version 3.14 stays as written. Another sentence.' })).summary).toBe('Version 3.14 stays as written.');
    expect(presentDirection(direction({ description: '原文说“先核对资料！”然后继续。' })).summary).toBe('原文说“先核对资料！”');
    expect(presentDirection(direction({ description: '文'.repeat(101) + '。后文' })).summary).toBe('文'.repeat(100));
    expect(presentDirection(direction({ description: '🔎'.repeat(101) })).summary).toBe('🔎'.repeat(100));
    expect(presentDirection(direction()).summary).toBe('');
  });

  it('preserves unknown raw fields and never mutates the full original direction', () => {
    const raw = JSON.parse(JSON.stringify(backendPlan.design.directions[0]));
    raw.future_contract = { anything: ['retained', { nested: true }] };
    raw.color.palette.future_color = '#123456';
    const source = adaptDirections([raw])![0];
    const before = JSON.stringify({ raw, source });
    Object.freeze(source.details);
    Object.freeze(source);
    presentDirection(source);
    expect(JSON.stringify({ raw, source })).toBe(before);
    expect(source.details?.future_contract).toEqual(raw.future_contract);
    expect(source.description).toBe(raw.note);
  });
});
