import type { Direction } from './domain';

type RecordValue = Record<string, unknown>;
export interface DirectionPresentation {
  summary: string;
  paletteName: string;
  palette: Array<{ key: string; label: string; color: string }>;
  colors: { background?: string; foreground?: string; accent?: string; surface?: string };
  styleLabels: string[];
  modeLabels: string[];
  pageCount: number;
  layout: 'editorial' | 'console' | 'steps' | 'neutral';
}

const styleLabels: Record<string, string> = {
  editorial: '编辑排版',
  'dark-tech': '深色科技',
  'digital-dashboard': '数字仪表盘',
  'soft-rounded': '柔和圆角',
};
const modeLabels: Record<string, string> = { briefing: '简报', instructional: '教学引导' };
const paletteLabels: Record<string, string> = {
  background: '背景', body_text: '正文', primary: '主色', accent: '强调色',
  secondary_bg: '辅助背景', secondary_accent: '辅助强调色',
};
const layouts: Record<string, DirectionPresentation['layout']> = {
  editorial: 'editorial', 'dark-tech': 'console', 'digital-dashboard': 'console', 'soft-rounded': 'steps',
};

function isRecord(value: unknown): value is RecordValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// Contract fields may carry evidence beside a value. Do not coerce objects or
// serialize their metadata into the short presentation labels.
function unwrap(value: unknown): unknown {
  for (let depth = 0; depth < 4 && isRecord(value) && Object.hasOwn(value, 'value'); depth++) value = value.value;
  return value;
}

function recordValue(value: unknown): RecordValue {
  for (let depth = 0; depth < 4 && isRecord(value) && Object.hasOwn(value, 'value') && isRecord(value.value); depth++) value = value.value;
  return isRecord(value) ? value : {};
}

function plainLabel(value: unknown): string {
  const text = unwrap(value);
  if (typeof text !== 'string') return '';
  const label = text.trim();
  return Array.from(label).length <= 60 && /^[\p{L}\p{M}\p{N} _·、，-]+$/u.test(label) ? label : '';
}

function references(value: unknown, additional: unknown): string[] {
  const result: string[] = [];
  for (const field of [value, additional]) {
    const unwrapped = unwrap(field);
    for (const entry of Array.isArray(unwrapped) ? unwrapped : [unwrapped]) {
      const label = plainLabel(entry);
      if (label && label !== 'custom' && !result.includes(label)) result.push(label);
    }
  }
  return result;
}

function labels(values: string[], known: Record<string, string>): string[] {
  return [...new Set(values.map(value => Object.hasOwn(known, value) ? known[value] : value))];
}

function safeColor(value: unknown): string | undefined {
  const color = unwrap(value);
  if (typeof color !== 'string' || (color.length !== 4 && color.length !== 7)
    || !/^#(?:[\da-f]{3}|[\da-f]{6})$/i.test(color)) return undefined;
  const expanded = color.length === 4 ? '#' + Array.from(color.slice(1), digit => digit + digit).join('') : color;
  return expanded.toUpperCase();
}

function summary(description: unknown): string {
  if (typeof description !== 'string') return '';
  const text = description.trim();
  const boundary = /[。！？!?][”’」』）)\]]*|\.(?=\s|$)/u.exec(text);
  const sentence = boundary ? text.slice(0, boundary.index + boundary[0].length) : text;
  return Array.from(sentence).slice(0, 100).join('').trimEnd();
}

export function presentDirection(direction: Direction): DirectionPresentation {
  const details = isRecord(direction.details) ? direction.details : {};
  const color = recordValue(details.color);
  const palette = Object.entries(recordValue(color.palette)).flatMap(([key, value]) => {
    const color = safeColor(value);
    return color ? [{ key, label: Object.hasOwn(paletteLabels, key) ? paletteLabels[key] : plainLabel(key), color }] : [];
  });
  const semanticColors = new Map(palette.map(entry => [entry.key, entry.color]));
  const colors: DirectionPresentation['colors'] = {};
  if (semanticColors.has('background')) colors.background = semanticColors.get('background');
  if (semanticColors.has('body_text')) colors.foreground = semanticColors.get('body_text');
  if (semanticColors.has('secondary_bg')) colors.surface = semanticColors.get('secondary_bg');
  const accent = semanticColors.get('accent') ?? semanticColors.get('primary');
  if (accent) colors.accent = accent;
  const styles = references(details.visual_style, details.visual_style_references);
  const modes = references(details.mode, details.mode_references);
  const visual = styles.find(style => Object.hasOwn(layouts, style));
  return {
    summary: summary(direction.description), paletteName: plainLabel(color.name), palette: palette.slice(0, 6), colors,
    styleLabels: labels(styles, styleLabels), modeLabels: labels(modes, modeLabels),
    pageCount: Array.isArray(direction.pages) ? direction.pages.length : 0,
    layout: visual ? layouts[visual] : modes.includes('instructional') ? 'steps' : 'neutral',
  };
}
