import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('../src/api', () => ({ api: vi.fn() }));
import { api } from '../src/api';
import { ChapterEvidencePanel, ChapterKnowledgeSettings, ParagraphEvidenceList } from '../src/components/ChapterEvidence';
const request = vi.mocked(api);
beforeEach(() => { request.mockReset(); });
afterEach(() => cleanup());
describe('章节依据业务流程（协议级）', () => {
  it('没有配置图谱需求时不增加无意义面板', async () => {
    request.mockResolvedValue({ configured: false, applied: null });
    const view = render(<ChapterEvidencePanel projectId="p" onChanged={async () => {}} />);
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect(view.container.textContent).toBe('');
  });
  it('后端失败显示真实错误，不伪造准备成功', async () => {
    request.mockRejectedValue(new Error('规则版本已停用'));
    render(<ChapterEvidencePanel projectId="p" onChanged={async () => {}} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('规则版本已停用');
    expect(screen.queryByText('已确认，用于生成正文')).toBeNull();
  });
  it('对象与关系只显示已发布词表', async () => {
    request.mockResolvedValue({ ontologies: [{ id: 'o', name: '安置保障', version: 1, entity_types: ['安置点'], predicates: ['依赖'] }], rules: [] });
    const changed = vi.fn();
    render(<ChapterKnowledgeSettings value={{ ontology_version_id: 'o', entity_types: [], predicates: [], rule_version_ids: [] }} onChange={changed} />);
    fireEvent.click(screen.getByText('本章需要哪些对象、关系与规则'));
    const checkbox = await screen.findByRole('checkbox', { name: '依赖' });
    fireEvent.click(checkbox);
    expect(changed.mock.calls[0][0].predicates).toEqual(['依赖']);
  });
  it('正文作为文本转义，点击段落定位右栏', async () => {
    request.mockResolvedValue({ paragraphs: [{ block_id: 'p1', section: '现状', text: '<img src=x onerror=alert(1)>', status: 'current', evidence: [] }] });
    const view = render(<ParagraphEvidenceList projectId="p" documentId="d" versionId="v" />);
    await screen.findByText('<img src=x onerror=alert(1)>', { exact: false });
    expect(view.container.querySelector('img')).toBeNull();
    window.dispatchEvent(new CustomEvent('miaobi:paragraph-selected', { detail: 'p1' }));
    await waitFor(() => expect(view.container.querySelector('details.active-paragraph')).toHaveAttribute('open'));
  });
  it('切回只检索原文时清除旧规则，不暗中继续推演', async () => {
    request.mockResolvedValue({ ontologies: [{ id: 'o', name: '安置保障', version: 1, entity_types: [], predicates: [] }], rules: [] });
    const changed = vi.fn();
    render(<ChapterKnowledgeSettings value={{ ontology_version_id: 'o', entity_types: [], predicates: [], rule_version_ids: ['old-rule'] }} onChange={changed} />);
    fireEvent.click(screen.getByText('本章需要哪些对象、关系与规则'));
    await screen.findByRole('option', { name: '安置保障 · V1' });
    fireEvent.change(screen.getByLabelText('语义模型'), { target: { value: '' } });
    expect(changed).toHaveBeenCalledWith({ ontology_version_id: null, entity_types: [], predicates: [], rule_version_ids: [] });
  });
  it('零依据也可确认失效结果，不能永远保留旧结论', async () => {
    const packet = { run_id: 'r', references: {}, sections: [], warnings: ['当前没有匹配关系'], comparison: { new: 0, unchanged: 0, removed: 1, affected_blocks: ['p'] } };
    request.mockResolvedValueOnce({ configured: true, applied: null }).mockResolvedValue(packet);
    const changed = vi.fn(async () => {});
    render(<ChapterEvidencePanel projectId="p" onChanged={changed} />);
    fireEvent.click(await screen.findByRole('button', { name: '准备章节依据' }));
    expect(await screen.findByText('当前没有匹配关系')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认用于写作' }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(request).toHaveBeenCalledWith('/writing/projects/p/chapter-evidence/r/apply', { method: 'POST' });
  });
});
