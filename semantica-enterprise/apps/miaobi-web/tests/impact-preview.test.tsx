import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ImpactPreviewDialog } from '../src/components/ImpactPreviewDialog';
import type { WritingInputChange } from '../src/types/domain';

afterEach(cleanup);

const preview: WritingInputChange = {
  id: 'preview-1', project_id: 'project-1', document_id: 'document-1', status: 'preview',
  changes: [{ fact_key: 'rescue_available', label: '可用搜救人员', old_value: { number: 320 }, new_value: { number: 400 }, unit: '人' }],
  impact: {
    calculations: [{ result_key: 'rescue_gap', label: '搜救人员缺口', old_value: 180, new_value: 100, unit: '人' }],
    content_proposals: [
      { block_id: 'metric', section: '资源保障', kind: 'computed_metric', selectable: true, old_text: '缺口180人。', new_text: '缺口100人。' },
      { block_id: 'paragraph', section: '处置行动', kind: 'p', selectable: true, old_text: '可用320人。', new_text: '可用400人。' },
      { block_id: 'review', section: '综合判断', kind: 'p', selectable: false, old_text: '资源压力较大。', reason: '需人工核对' },
    ],
  },
};

describe('指标影响预览', () => {
  it('列出可验算结果及逐项文章提案，不让待人工核对内容被勾选', () => {
    const onApply = vi.fn();
    render(<ImpactPreviewDialog preview={preview} submitting={false} onCancel={vi.fn()} onApply={onApply} />);
    expect(screen.getByText('缺口100人。', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('可用400人。', { exact: false })).toBeInTheDocument();
    const review = screen.getByLabelText('采用综合判断中的更新') as HTMLInputElement;
    expect(review.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText('采用处置行动中的更新'));
    fireEvent.click(screen.getByRole('button', { name: /应用选中的 1 处/ }));
    expect(onApply).toHaveBeenCalledWith(['metric']);
  });

  it('支持取消全选、重新全选与关闭，不提交变更', () => {
    const onApply = vi.fn();
    const onCancel = vi.fn();
    render(<ImpactPreviewDialog preview={preview} submitting={false} onCancel={onCancel} onApply={onApply} />);
    fireEvent.click(screen.getByRole('button', { name: '取消全选' }));
    expect(screen.getByRole('button', { name: /应用选中的 0 处/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '全选可更新内容' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onApply).not.toHaveBeenCalled();
  });
});
