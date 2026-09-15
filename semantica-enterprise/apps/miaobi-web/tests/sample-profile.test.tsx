import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SampleProfilePanel } from '../src/components/SampleProfilePanel';
import type { ProjectMaterial, WritingDocument, WritingSampleProfile } from '../src/types/domain';

afterEach(cleanup);

const profile: WritingSampleProfile = {
  source_version_id: 'sample-v1', source_sha256: 'hash', material_id: 'sample-material',
  status: 'confirmed', genre: '应急预案',
  chapters: ['总则', '组织体系', '运行机制', '应急保障', '其他地震事件应急', '监督管理', '附则']
    .map((title, index) => ({ key: `section-${index}`, title, instruction: `${title}只依据本文确认的资料撰写` })),
  attachments: [{ number: '1', title: '应急响应流程' }],
  style: { register: '正式预案', heading_numbering: '一、（一）' },
  indicator_candidates: [], formula_candidates: [],
};
const document: WritingDocument = {
  id: 'article', project_id: 'project', title: '讨论稿', status: 'draft',
  applicability: { sample_profile: profile },
};
const sample: ProjectMaterial = {
  id: 'sample-material', project_id: 'project', document_id: 'sample-doc', version_id: 'sample-v1',
  material_role: 'sample_style', usage_scope: 'task_only', status: 'active', version_pinned: true,
  current_document_version: true, adopted_by_article: true,
  document: { id: 'sample-doc', space_id: 'space', title: '样稿.pdf', status: 'active', tags: [] },
  version: { id: 'sample-v1', version_number: 1, filename: '样稿.pdf', content_type: 'application/pdf', size: 1, status: 'processed' },
};

describe('已确认样稿配置恢复', () => {
  it('刷新后可查看七章提示要求、文体、附件和指标状态，不需要重新抽取', () => {
    render(<SampleProfilePanel document={document} materials={[sample]} onChanged={async () => {}} onError={() => {}} />);
    expect(screen.getByText('本文已确认 7 个一级章节')).toBeInTheDocument();
    expect(screen.queryAllByText('本章生成要求')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '查看已确认配置' }));
    expect(screen.getAllByText('本章生成要求')).toHaveLength(7);
    expect(screen.getByText('附件：1 应急响应流程')).toBeInTheDocument();
    expect(screen.getByText(/指标与公式候选：0 项指标、0 个公式/)).toBeInTheDocument();
    expect(screen.getByText(/文体：正式预案/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('附则只依据本文确认的资料撰写')).toBeInTheDocument();
  });
});
