import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ExtractionWorkbench } from '../src/components/ExtractionWorkbench';
import type { ExtractionWorkbenchSnapshot } from '../src/types/domain';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const snapshot: ExtractionWorkbenchSnapshot = {
  project_id: 'project', document_id: 'article', material_count: 1, evidence_count: 1, pending_count: 1,
  limits: { max_items_per_step: 200, read_only_projection: true },
  steps: [
    {
      key: 'material_role', title: '材料归类', short_title: '归类', purpose: '区分材料用途',
      input_label: '项目材料', output_label: '材料用途', prompt: '输出严格 JSON 数组', status: 'ready',
      count: 1, pending_count: 0, items: [{ id: 'm1', title: '工作简报.pdf', role: 'task_data', version: 1 }],
    },
    {
      key: 'evidence', title: '证据切片', short_title: '证据', purpose: '形成证据',
      input_label: '已解析正文', output_label: 'Evidence', prompt: '保留页码并输出严格 JSON 数组', status: 'needs_confirmation',
      count: 1, pending_count: 1, items: [{ id: 'e1', title: '工作简报.pdf', text: '可用搜救人员为320人。', page: 2, path: 'paragraphs/3', needs_confirmation: true }],
    },
  ],
};

describe('材料抽取工作台', () => {
  it('展示真实步骤、输入、提示词和结果源数据', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(snapshot), {
      status: 200, headers: { 'content-type': 'application/json' },
    })));
    render(<ExtractionWorkbench projectId="project" documentId="article" materialCount={1} onError={() => {}} />);
    await waitFor(() => expect(screen.getByText('从材料到可写事实')).toBeInTheDocument());
    expect(screen.getByText('工作简报.pdf')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /证据/ }));
    expect(screen.getByText('可用搜救人员为320人。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '提示词' }));
    expect(screen.getByText(/保留页码并输出严格 JSON 数组/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '输入' }));
    expect(screen.getByText(/工作简报.pdf/)).toBeInTheDocument();
  });
});
