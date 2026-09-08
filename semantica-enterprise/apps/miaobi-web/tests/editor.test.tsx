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
import { MiaobiEditor, EditorKit, normalizeCollaborativeValue } from '../src/editor/MiaobiEditor';
import { lockedTrustedBlockTypes, trustedBlockTypes } from '../src/editor/plugins/trusted-blocks';
import { cleanEvidenceText } from '../src/evidence';
import { createFrameDeltaBuffer } from '../src/streaming';
import { canonicalJson } from '../src/App';

describe('妙笔 Plate 编辑器', () => {
  it('可信块使用与后端一致的递归键排序 JSON', () => {
    expect(canonicalJson({ z: 1, children: [{ text: '震级', bold: true }], a: '中文' }))
      .toBe('{"a":"中文","children":[{"bold":true,"text":"震级"}],"z":1}');
  });
  it('清理检索片段中的 Markdown 与 HTML 实体并安全截断', () => {
    const value = cleanEvidenceText('制度.md：# 地震预案\n&gt; **响应要求** [来源](https://example.test) ' + '处置'.repeat(300));
    expect(value).not.toMatch(/^#/);
    expect(value).not.toContain('&gt;');
    expect(value).not.toContain('**');
    expect(value).not.toContain('https://');
    expect(value).toContain('响应要求');
    expect(value).not.toContain('：#');
    expect(value).toHaveLength(420);
    expect(value.endsWith('…')).toBe(true);
  });

  it('迁移旧协同快照中的引用格式而不改写普通正文', () => {
    const migrated = normalizeCollaborativeValue([
      { id: 'citation', type: 'knowledge_citation', children: [{ text: '# 预案\n&gt; **等级判据**' }] },
      { id: 'body', type: 'p', children: [{ text: '# 普通正文保留原样' }] },
    ]);
    expect(migrated.changed).toBe(true);
    expect(migrated.value[0].children[0].text).toBe('预案 等级判据');
    expect(migrated.value[1].children[0].text).toBe('# 普通正文保留原样');
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
    expect(lockedTrustedBlockTypes).toEqual(['computed_metric', 'inference_conclusion']);
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

  it('将 1000 个流式增量合并到一个动画帧', () => {
    const callbacks: FrameRequestCallback[] = [];
    const values: string[] = [];
    const batch = createFrameDeltaBuffer(
      (value) => values.push(value),
      (callback) => { callbacks.push(callback); return callbacks.length; },
      () => undefined,
    );
    for (let index = 0; index < 1000; index += 1) batch.enqueue('字');
    expect(callbacks).toHaveLength(1);
    callbacks[0](performance.now());
    expect(values).toEqual(['字'.repeat(1000)]);
  });
});
