import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MiaobiEditor, EditorKit } from '../src/editor/MiaobiEditor';
import { trustedBlockTypes } from '../src/editor/plugins/trusted-blocks';

describe('妙笔 Plate 编辑器', () => {
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

  it('渲染真实 Plate 编辑面和可执行工具栏', () => {
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
      />,
    );
    expect(screen.getByTestId('plate-editor')).toBeInTheDocument();
    expect(screen.getByRole('toolbar', { name: '文稿编辑工具' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '知识引用' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '测算值' })).toBeEnabled();
    expect(screen.getByText('积石山县地震应急处置方案')).toBeInTheDocument();
  });
});
