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
    suspected_impacts: [{ block_id: 'unbound', section: '摘要', reason: '文字提到变更事实但未登记绑定', matched_labels: ['可用搜救人员'] }],
    propagation: {
      roots: ['project_fact:available'], direct_count: 1, indirect_count: 2, truncated: false,
      impacts: [{
        node_id: 'chunk:metric', direct: false, depth: 2, relation: 'RESTATES', action: 'sync_text',
        certainty: 'definite', path: ['project_fact:available', 'computation_run:gap', 'chunk:metric'],
      }],
    },
  },
};

describe('指标影响预览', () => {
  it('表格逐项展示精确指标变更，不把单元格拼成无法阅读的长文本', () => {
    render(<ImpactPreviewDialog preview={{ ...preview, impact: { ...preview.impact, content_proposals: [{
      block_id: 'table', section: '统计表', kind: 'table', selectable: true,
      old_text: '不应该展示的单元格拼接', new_text: '不应该展示的新表格拼接',
      occurrence_updates: [{ occurrence_id: 'gap-cell', fact_key: 'rescue_gap', old_text: '180', new_text: '100', unit: '人' }],
    }] } }} submitting={false} onCancel={vi.fn()} onApply={vi.fn()} />);
    expect(screen.getByText('统计表 · 表格 · 确定影响')).toBeInTheDocument();
    expect(screen.getByText('只更新这些已绑定的单元格，公式与其他内容保持不变。')).toBeInTheDocument();
    expect(screen.queryByText('不应该展示的单元格拼接')).not.toBeInTheDocument();
    expect(screen.getAllByText('搜救人员缺口')).toHaveLength(2);
  });
  it('列出可验算结果及逐项文章提案，不让待人工核对内容被勾选', () => {
    const onApply = vi.fn();
    render(<ImpactPreviewDialog preview={preview} submitting={false} onCancel={vi.fn()} onApply={onApply} />);
    expect(screen.getByText('缺口100人。', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('可用400人。', { exact: false })).toBeInTheDocument();
    expect(screen.getByLabelText('传播摘要')).toHaveTextContent('2 处间接影响');
    expect(screen.getByText('这些文字没有登记权威绑定，系统不会自动修改。')).toBeInTheDocument();
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
