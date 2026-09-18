import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MaterialsPanel } from '../src/App';
import type { KnowledgeContext, Project, ProjectMaterial } from '../src/types/domain';

const project: Project = {
  id: 'project-1', code: 'project', name: '科研楼可研', status: 'draft',
  scenario_package_version_id: 'scenario-1', knowledge_space_id: 'space-1',
};
const knowledgeContext: KnowledgeContext = {
  product: { id: 'product-1', name: '科研楼知识', code: 'research-building' },
  release: { id: 'release-1', version: 1, checksum: 'abc', status: 'published', is_latest: true },
  spaces: [{
    id: 'space-1', name: '科研楼空间', code: 'research-building', knowledge_release_id: 'knowledge-1',
    knowledge_release_number: 1, status: 'published', document_count: 0, chunk_count: 0,
    entity_count: 0, fact_count: 0, graph_available: true, vector_available: true,
  }],
  snapshot_locked: true, document_count: 0, chunk_count: 0, entity_count: 0, fact_count: 0,
  task_material_count: 0, task_material_roles: {}, retrieval_scope: 'task_materials', writing_graph: null,
};
const json = (value: unknown) => new Response(JSON.stringify(value), {
  status: 200, headers: { 'content-type': 'application/json' },
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('项目资料池', () => {
  it('一次选择多个文件，分别加工后统一刷新知识版本并加入项目', async () => {
    let uploadNumber = 0;
    const request = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/documents/upload')) {
        uploadNumber += 1;
        return json({ document: { id: `document-${uploadNumber}` }, version: { id: `version-${uploadNumber}` } });
      }
      if (/\/documents\/document-\d+$/.test(url)) {
        const number = url.match(/(\d+)$/)?.[1] || '1';
        return json({
          status: 'published', current_version_id: `version-${number}`,
          versions: [{ id: `version-${number}`, status: 'ready', parse_summary: { knowledge_status: 'published' } }],
        });
      }
      if (url.endsWith('/knowledge-release/refresh')) return json({ id: 'release-2' });
      if (url.endsWith('/materials')) return json({ id: `material-${Math.random()}` });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', request);
    const onChanged = vi.fn(async () => {});
    render(<MaterialsPanel project={project} document={null} materials={[]} knowledgeContext={knowledgeContext}
      onChanged={onChanged} onError={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '上传资料' }));
    const input = screen.getByLabelText('文件') as HTMLInputElement;
    expect(input.multiple).toBe(true);
    fireEvent.change(input, { target: { files: [
      new File(['a'], '项目基础资料.docx'),
      new File(['b'], '投资估算.xlsx'),
    ] } });
    expect(screen.getByText('项目基础资料.docx')).toBeInTheDocument();
    expect(screen.getByText('投资估算.xlsx')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '上传 2 个文件' }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(request.mock.calls.filter(([url]) => String(url).endsWith('/documents/upload'))).toHaveLength(2);
    expect(request.mock.calls.filter(([url]) => String(url).endsWith('/knowledge-release/refresh'))).toHaveLength(1);
    expect(request.mock.calls.filter(([url]) => String(url).endsWith('/materials'))).toHaveLength(2);
  });

  it('一键导出当前文章的 YAML 数据和 Markdown 说明压缩包', () => {
    const material: ProjectMaterial = {
      id: 'material-1', project_id: project.id, document_id: 'source-1', version_id: 'version-1',
      material_role: 'task_data', usage_scope: 'task_only', status: 'active', version_pinned: true,
      current_document_version: true, document: { id: 'source-1', space_id: 'space-1', title: '基础资料', status: 'published', tags: [] },
      version: { id: 'version-1', version_number: 1, filename: '基础资料.docx', content_type: 'application/docx', size: 1, status: 'processed' },
    };
    render(<MaterialsPanel project={project} document={{
      id: 'article-1', project_id: project.id, title: '可研报告', status: 'draft',
    }} materials={[material]} knowledgeContext={knowledgeContext} onChanged={vi.fn(async () => {})} onError={vi.fn()} />);
    expect(screen.getByRole('link', { name: '导出写作数据' })).toHaveAttribute(
      'href', '/api/v1/writing/projects/project-1/writing-support.zip?document_id=article-1',
    );
  });
});
