import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WritingAssets } from '../src/components/WritingAssets';
import type { WritingDocument, ProjectMaterial } from '../src/types/domain';

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
const asset = { id: 'asset', title: '保障方案章节', kind: 'report_structure', version: 1, checksum: 'checksum',
  status: 'reviewed', definition: { kind: 'report_structure', key: 'outline', purpose: '明确各章职责',
    scope: '同类方案，需确认本次适用性', used_in_sections: [], evidence_ids: ['evidence'],
    payload: { chapters: [{ key: 'resources', title: '资源保障', instruction: '采用当前资源口径' }] } },
  validation: { execution: 'not_required' } };
const article = { id: 'article', current_version_id: 'article-v1', applicability: {} } as WritingDocument;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function setup(row = asset, currentArticle = article) {
  const requests = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/trace')) return json({ input: [{ filename: '历史方案.md', file_version: 1, text: '原文依据', locator: { page: 1 } }], runtime_prompt: '真实留存的提示词', output: { assets: [row] }, process: { status: 'succeeded', metrics: { coverage: 'complete' } } });
    if (url.endsWith('/kinds')) return json({ kinds: [{ key: row.kind, label: '报告结构' }], engine: { version: '0.7.0' } });
    if (url.includes('/summary?')) return json({ total: 1, kinds: { [row.kind]: { total: 1, candidate: row.status === 'candidate' ? 1 : 0, reviewed: row.status === 'reviewed' ? 1 : 0, rejected: 0 } } });
    if (url.includes('/releases?')) return json([{ id: 'release', release_number: 1, snapshot: { assets: [{ id: row.id }] } }]);
    if (url.endsWith('/compute')) return json({ items: [{ fact: { label: '资源缺口', value: { number: 180 }, unit: '人' }, reused: true }] });
    if (init?.method === 'POST') return json({ id: 'new', status: 'queued', progress: 0 });
    return json([row]);
  });
  vi.stubGlobal('fetch', requests);
  render(<WritingAssets projectId="project" spaceId="space" document={currentArticle} materials={[]} onChanged={vi.fn()} onError={vi.fn()} />);
  return requests;
}

describe('历史结构与方法', () => {
  it('资产数量来自后端，类型按钮触发真实服务端过滤', async () => {
    const requests = setup();
    const kind = await screen.findByRole('button', { name: /报告结构\s*1/ });
    fireEvent.click(kind);
    await waitFor(() => expect(requests.mock.calls.some(([url]) => String(url).includes('&kind=report_structure'))).toBe(true));
    expect(kind).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/共 1 个资产版本/)).toBeInTheDocument();
  });
  it('采用必须确认，取消不会请求；只发送发布资产版本而不是历史数字', async () => {
    const requests = setup();
    await screen.findByText('保障方案章节');
    fireEvent.click(screen.getByRole('checkbox', { name: '选择保障方案章节' }));
    fireEvent.click(screen.getByRole('button', { name: '用于当前文章' }));
    expect(screen.getByRole('dialog', { name: '确认用于本文' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '用于当前文章' }));
    fireEvent.click(screen.getByRole('button', { name: '确认采用' }));
    await waitFor(() => expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1));
    const call = requests.mock.calls.find(([url]) => String(url).endsWith('/adopt'))!;
    expect(JSON.parse(String(call[1]?.body))).toEqual({ release_id: 'release', asset_ids: ['asset'], base_version_id: 'article-v1', scope_confirmed: true, adopt_outline: true });
  });
  it('可查看真实输入和实际提示词，关闭详情不提交', async () => {
    const requests = setup();
    fireEvent.click(await screen.findByRole('button', { name: /保障方案章节/ }));
    fireEvent.click(screen.getByRole('button', { name: '来源输入' }));
    expect(await screen.findByText('原文依据')).toBeInTheDocument();
    expect(screen.getByText('第 1 页')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '实际提示词' }));
    expect(screen.getByText('真实留存的提示词')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭资产详情' }));
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  });
  it('缺少实现的技能明确提示，不显示执行按钮', async () => {
    setup({ ...asset, kind: 'skill', status: 'candidate', validation: { execution: 'not_implemented' } });
    fireEvent.click(await screen.findByRole('button', { name: /保障方案章节/ }));
    expect(screen.getByText(/尚无执行实现/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '计算本次结果' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认定义' })).toBeDisabled();
  });
  it('已采用方法的计算结果可见，重复输入明确复用真实结果', async () => {
    const requests = setup({ ...asset, kind: 'computation_method' }, {
      ...article, applicability: { asset_bundle: { release_id: 'release', checksum: 'bundle-checksum', assets: [{ id: asset.id, definition: { kind: 'computation_method' } }] } },
    });
    fireEvent.click(await screen.findByRole('button', { name: '计算本次结果' }));
    expect(await screen.findByText('资源缺口：180 人')).toBeInTheDocument();
    expect(screen.getByText(/输入未变，使用已核验结果/)).toBeInTheDocument();
    const computeRequests = requests.mock.calls.filter(([url]) => String(url).endsWith('/compute'));
    expect(computeRequests).toHaveLength(1);
    expect(JSON.parse(String(computeRequests[0][1]?.body))).toEqual({ base_version_id: 'article-v1', release_id: 'release', checksum: 'bundle-checksum' });
  });
});
