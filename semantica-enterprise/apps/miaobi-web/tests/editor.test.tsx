import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../src/api', () => ({
  api: vi.fn((path: string) => Promise.resolve(path.endsWith('/comments') ? [] : {
    token: 'test-token',
    room: 'writing-document-1',
    url: 'ws://127.0.0.1:9',
    expires_at: '2099-01-01T00:00:00Z',
    role: 'owner',
    read_only: false,
    user: { id: 'user-1', name: '测试用户' },
  })),
}));
import { MiaobiEditor, EditorKit } from '../src/editor/MiaobiEditor';
import { trustedBlockTypes } from '../src/editor/plugins/trusted-blocks';
import { cleanEvidenceText } from '../src/evidence';

describe('妙笔 Plate 编辑器', () => {
  it('清理检索片段中的 Markdown 与 HTML 实体并安全截断', () => {
    const value = cleanEvidenceText('# 地震预案\n&gt; **响应要求** [来源](https://example.test) ' + '处置'.repeat(300));
    expect(value).not.toMatch(/^#/);
    expect(value).not.toContain('&gt;');
    expect(value).not.toContain('**');
    expect(value).not.toContain('https://');
    expect(value).toContain('响应要求');
    expect(value).toHaveLength(420);
    expect(value.endsWith('…')).toBe(true);
  });

  it('使用锁定版 Plate 插件体系并注册全部可信业务块', () => {
    expect(EditorKit.length).toBeGreaterThan(25);
    expect(trustedBlockTypes).toEqual([
      'knowledge_citation',
      'verified_fact',
      'computed_metric',
      'inference_conclusion',
      'manual_assumption',
      'decision_gate',
      'alternative_plan',
      'action_task',
      'data_table',
      'geo_route',
      'risk_warning',
    ]);
  });

  it('渲染真实 Plate 编辑面、协同评论和修订工具', async () => {
    const onRequestSource = vi.fn();
    render(
      <MiaobiEditor
        document={{
          id: 'document-1',
          project_id: 'project-1',
          title: '积石山县地震应急处置方案',
          status: 'draft',
          current_version: {
            id: 'version-1',
            version: 1,
            content_hash: 'a'.repeat(64),
            content: [{ id: 'title', type: 'h1', children: [{ text: '积石山县地震应急处置方案' }] }],
          },
        }}
        onDirtyChange={vi.fn()}
        onSaved={vi.fn()}
        onRequestSource={onRequestSource}
      />,
    );
    expect(await screen.findByTestId('plate-editor')).toBeInTheDocument();
    expect(await screen.findByRole('toolbar', { name: '文稿编辑工具' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '知识引用' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '测算值' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '修订模式' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '接受修订' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '拒绝修订' })).toBeEnabled();
    expect(screen.getByRole('region', { name: '协同评论' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '评论内容' })).toBeInTheDocument();
    expect(screen.getByText('Plate 协同与本地恢复已启用')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '知识引用' }));
    fireEvent.click(screen.getByRole('button', { name: '测算值' }));
    fireEvent.click(screen.getByRole('button', { name: '推演结论' }));
    expect(onRequestSource).toHaveBeenNthCalledWith(1, 'evidence');
    expect(onRequestSource).toHaveBeenNthCalledWith(2, 'calculation');
    expect(onRequestSource).toHaveBeenNthCalledWith(3, 'calculation');
    expect(screen.queryByText('待选择知识来源')).not.toBeInTheDocument();
    expect(screen.queryByText('待插入测算结果')).not.toBeInTheDocument();
    expect(screen.queryByText('待插入推演结论')).not.toBeInTheDocument();
  });
});
