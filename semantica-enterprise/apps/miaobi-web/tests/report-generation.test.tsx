import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ReportGenerationPanel } from '../src/App';
import type { Project, WritingDocument, WritingGenerationRun } from '../src/types/domain';

const request = vi.fn();
const project: Project = {
  id: 'project-1', code: 'test', name: '测试报告', status: 'draft',
  scenario_package_version_id: 'scenario-1', knowledge_product_release_id: 'release-1',
};
const created: WritingGenerationRun = {
  id: 'run-1', project_id: project.id, document_id: 'document-1',
  status: 'awaiting_agent', stage: 'agent_generation', progress: 45,
};
const issueMessage = 'Agent 未返回唯一且完整的分章节 JSON，请重新生成。';
const failed: WritingGenerationRun = {
  ...created, status: 'quality_failed', stage: 'structured_output_validation', progress: 100,
  error_message: issueMessage,
  quality_report: { ok: false, issues: [{ code: 'invalid_agent_report', severity: 'error', message: issueMessage }] },
};
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'content-type': 'application/json' },
});
const stream = () => new Response('event: turn_completed\ndata: {}\n\n', {
  headers: { 'content-type': 'text/event-stream' },
});
const renderPanel = (document: WritingDocument | null = null) => {
  const onError = vi.fn();
  const onChanged = vi.fn(async () => {});
  const onOpenEditor = vi.fn();
  const view = render(<ReportGenerationPanel project={project} facts={[]} computations={[]} plans={[]}
    document={document} ready onChanged={onChanged} onOpenEditor={onOpenEditor} onBack={vi.fn()} onError={onError} />);
  return { ...view, onError, onChanged, onOpenEditor };
};

beforeEach(() => {
  request.mockReset();
  vi.stubGlobal('fetch', request);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('报告生成失败恢复', () => {
  it('422 后读取当前任务真实终态与 100% 进度，保留问题并允许成功重试', async () => {
    request.mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json(created))
      .mockResolvedValueOnce(stream())
      .mockResolvedValueOnce(json({ detail: failed.quality_report }, 422))
      .mockResolvedValueOnce(json(failed));
    const { onError, onChanged, onOpenEditor } = renderPanel();
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '开始生成报告' }));

    expect(await screen.findByText('上次生成失败，可重新生成')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '100');
    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(screen.queryByText('正在执行报告质量检查')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始生成报告' })).toBeEnabled();
    expect(screen.getByText('报告没有通过质量门，未覆盖当前正文')).toBeInTheDocument();
    expect(onError).toHaveBeenLastCalledWith(issueMessage);
    expect(request).toHaveBeenLastCalledWith('/api/v1/writing/generation-runs/run-1', expect.any(Object));
    expect(onChanged).not.toHaveBeenCalled();
    expect(onOpenEditor).not.toHaveBeenCalled();

    request.mockResolvedValueOnce(json({ ...created, id: 'run-2' }))
      .mockResolvedValueOnce(stream())
      .mockResolvedValueOnce(json({ ...created, id: 'run-2', status: 'completed', progress: 100 }));
    fireEvent.click(screen.getByRole('button', { name: '开始生成报告' }));
    await waitFor(() => expect(onOpenEditor).toHaveBeenCalledOnce());
    expect(onChanged).toHaveBeenCalledOnce();
    expect(onError).toHaveBeenLastCalledWith('');
    expect(screen.getByText('报告已生成')).toBeInTheDocument();
    expect(screen.queryByText('报告没有通过质量门，未覆盖当前正文')).not.toBeInTheDocument();
  });

  it('刷新页面恢复失败结果，问题作为文本转义而非执行 HTML', async () => {
    const message = '<img src=x onerror=alert(1)> 请补齐章节。';
    request.mockResolvedValueOnce(json([{
      ...failed, error_message: message,
      quality_report: { issues: [{ code: 'invalid_agent_report', message }] },
    }]));
    const { container } = renderPanel();
    expect(await screen.findByText('上次生成失败，可重新生成')).toBeInTheDocument();
    expect(screen.getAllByText(message)).toHaveLength(2);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '100');
    expect(screen.getByRole('button', { name: '开始生成报告' })).toBeEnabled();
  });

  it('状态刷新也失败时保留首个真实错误，不继续显示质量检查中或伪造完成进度', async () => {
    request.mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json(created))
      .mockResolvedValueOnce(stream())
      .mockResolvedValueOnce(json({ detail: failed.quality_report }, 422))
      .mockRejectedValueOnce(new Error('状态读取失败'));
    const { onError } = renderPanel();
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '开始生成报告' }));
    expect(await screen.findByText('本次生成未完成，可重新生成')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '开始生成报告' })).toBeEnabled());
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '45');
    expect(onError).toHaveBeenLastCalledWith(issueMessage);
    expect(screen.queryByText('报告已生成')).not.toBeInTheDocument();
  });

  it('Agent 启动的结构化错误也只显示问题文案，并恢复 Agent 失败终态', async () => {
    request.mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json(created))
      .mockResolvedValueOnce(json({ detail: { issues: [{ message: '写作服务暂时不可用，请重试。' }], secret: 'hidden' } }, 503))
      .mockResolvedValueOnce(json({ ...created, status: 'agent_failed', progress: 45 }));
    const { onError } = renderPanel();
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '开始生成报告' }));
    expect(await screen.findByText('上次生成失败，可重新生成')).toBeInTheDocument();
    expect(onError).toHaveBeenLastCalledWith('写作服务暂时不可用，请重试。');
    expect(screen.getByRole('button', { name: '开始生成报告' })).toBeEnabled();
  });

  it('后端尚未终止时保留真实进度，但前端中断后不声称仍在生成', async () => {
    request.mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json(created))
      .mockRejectedValueOnce(new TypeError('网络连接中断'))
      .mockResolvedValueOnce(json({ ...created, status: 'agent_running', progress: 60 }));
    renderPanel();
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '开始生成报告' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '开始生成报告' })).toBeEnabled());
    expect(screen.getByText('本次生成未完成，可重新生成')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '60');
    expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument();
  });

  it('真实任务的零进度不会被已有报告替换为 100%', async () => {
    request.mockResolvedValueOnce(json([{ ...failed, progress: 0 }]));
    renderPanel({ id: 'document-1', project_id: project.id, title: '已有报告', status: 'draft' });
    expect(await screen.findByText('上次生成失败，可重新生成')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '0');
  });
});
