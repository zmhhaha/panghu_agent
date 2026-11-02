import { Plus, X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import './feedback-list.css';

export interface FeedbackRow { id: number; page: number | null; feedback: string }
export const emptyFeedbackRows = (): FeedbackRow[] => [{ id: 1, page: null, feedback: '' }];

/** Keep page scope beside each opinion so mixed whole-deck/page requests remain explicit. */
export function collectFeedback(rows: FeedbackRow[], availablePages: number[]) {
  const filled = rows.filter(row => row.feedback.trim());
  const invalidPage = filled.some(row => row.page !== null && !availablePages.includes(row.page));
  const pages = filled.some(row => row.page === null) ? [] : [...new Set(filled.map(row => row.page as number))].sort((a, b) => a - b);
  const feedback = filled.map((row, index) => `${index + 1}. ${row.page === null ? '整份演示稿' : `第 ${row.page} 页`}：${row.feedback.trim()}`).join('\n\n');
  return { pages, feedback, invalidPage, count: filled.length };
}

export function FeedbackList({ rows, slides, onChange, maxLength, disabled = false }: {
  rows: FeedbackRow[];
  slides: { number: number; title: string }[];
  onChange: (rows: FeedbackRow[]) => void;
  maxLength: number;
  disabled?: boolean;
}) {
  const textareas = useRef(new Map<number, HTMLTextAreaElement>());
  const pendingFocus = useRef<number | null>(null);
  useEffect(() => {
    if (pendingFocus.current !== null) textareas.current.get(pendingFocus.current)?.focus();
    pendingFocus.current = null;
  }, [rows]);
  const update = (id: number, values: Partial<FeedbackRow>) => onChange(rows.map(row => row.id === id ? { ...row, ...values } : row));
  return <div className="feedback-list">
    <p className="hint">一行写一条意见；只调整某一页时，再选择页码。空白行不会提交。</p>
    <ol aria-label="修改意见清单">{rows.map((row, index) => <li key={row.id} className="feedback-list-row">
      <span className="feedback-row-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
      <label className="feedback-row-content"><span className="sr-only">第 {index + 1} 条修改意见</span><textarea ref={element => { if (element) textareas.current.set(row.id, element); else textareas.current.delete(row.id); }} rows={2} maxLength={maxLength} value={row.feedback} disabled={disabled} placeholder="例如：第三页的连线太多，请简化关系并突出共享基座" onChange={event => update(row.id, { feedback: event.target.value })}/></label>
      <label className="feedback-row-scope"><span className="sr-only">第 {index + 1} 条意见适用范围</span><select value={row.page ?? ''} disabled={disabled} onChange={event => update(row.id, { page: event.target.value ? Number(event.target.value) : null })}><option value="">整份演示稿</option>{slides.map(slide => <option key={slide.number} value={slide.number}>第 {slide.number} 页 · {slide.title}</option>)}</select></label>
      <button className="feedback-remove" type="button" disabled={disabled} aria-label={`删除第 ${index + 1} 条意见`} onClick={() => { pendingFocus.current = rows[index + 1]?.id ?? rows[index - 1]?.id ?? null; onChange(rows.filter(candidate => candidate.id !== row.id)); }}><X size={17}/></button>
    </li>)}</ol>
    {!rows.length && <p className="hint">还没有修改意见。可添加一条，或继续查看预览。</p>}
    <button className="secondary compact" type="button" disabled={disabled || rows.length >= 50} onClick={() => { const id = Math.max(0, ...rows.map(row => row.id)) + 1; pendingFocus.current = id; onChange([...rows, { id, page: null, feedback: '' }]); }}><Plus size={16}/>添加一条意见</button>
  </div>;
}
