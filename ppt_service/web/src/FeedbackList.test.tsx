import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { FeedbackList, collectFeedback, emptyFeedbackRows, type FeedbackRow } from './FeedbackList';

const slides = [{ number: 1, title: '背景' }, { number: 2, title: '方法' }, { number: 3, title: '结果' }];

describe('revision feedback list', () => {
  it('keeps every opinion and its target when global and page feedback are mixed', () => {
    const request = collectFeedback([
      { id: 1, page: 2, feedback: '  简化连线  ' },
      { id: 2, page: null, feedback: '统一字号' },
      { id: 3, page: 3, feedback: '补充结论' },
      { id: 4, page: null, feedback: '   ' },
    ], [1, 2, 3]);
    expect(request).toEqual({ pages: [], count: 3, invalidPage: false, feedback: '1. 第 2 页：简化连线\n\n2. 整份演示稿：统一字号\n\n3. 第 3 页：补充结论' });
  });

  it('targets the sorted union of populated page rows and ignores blank global rows', () => {
    expect(collectFeedback([
      { id: 1, page: 3, feedback: '结论更简洁' }, { id: 2, page: null, feedback: '' },
      { id: 3, page: 1, feedback: '标题缩短' }, { id: 4, page: 3, feedback: '增大字号' },
    ], [1, 2, 3]).pages).toEqual([1, 3]);
    expect(collectFeedback([{ id: 1, page: 9, feedback: '旧页面意见' }], [1, 2, 3]).invalidPage).toBe(true);
    expect(collectFeedback(emptyFeedbackRows(), [1, 2, 3]).count).toBe(0);
  });

  it('adds and deletes rows without losing neighboring text or page scope', () => {
    function Harness() {
      const [rows, setRows] = useState<FeedbackRow[]>(emptyFeedbackRows);
      return <FeedbackList rows={rows} slides={slides} onChange={setRows} maxLength={10000}/>;
    }
    render(<Harness/>);
    fireEvent.change(screen.getByLabelText('第 1 条修改意见'), { target: { value: '简化关系图' } });
    fireEvent.change(screen.getByLabelText('第 1 条意见适用范围'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: '添加一条意见' }));
    expect(screen.getByLabelText('第 2 条修改意见')).toHaveFocus();
    fireEvent.change(screen.getByLabelText('第 2 条修改意见'), { target: { value: '统一字体' } });
    fireEvent.click(screen.getByRole('button', { name: '删除第 1 条意见' }));
    expect(screen.getByLabelText('第 1 条修改意见')).toHaveValue('统一字体');
    expect(screen.getByLabelText('第 1 条修改意见')).toHaveFocus();
    expect(screen.getByLabelText('第 1 条意见适用范围')).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: '添加一条意见' }));
    expect(screen.getByLabelText('第 1 条修改意见')).toHaveValue('统一字体');
    expect(screen.getByLabelText('第 2 条修改意见')).toHaveValue('');
  });
});
