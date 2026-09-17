import { useState } from 'react';
import { api } from '../api';
import type { ProjectMaterial, WritingDocument, WritingSampleProfile } from '../types/domain';

export function SampleProfilePanel({ document, materials, onChanged, onError }: {
  document: WritingDocument | null; materials: ProjectMaterial[];
  onChanged: () => Promise<void>; onError: (message: string) => void;
}) {
  const samples = materials.filter((item) => item.material_role === 'sample_style' && item.adopted_by_article !== false);
  const [materialId, setMaterialId] = useState('');
  const [profile, setProfile] = useState<WritingSampleProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const confirmed = document?.applicability?.sample_profile;
  if (!document || !samples.length) return null;
  const selected = samples.some((item) => item.id === materialId) ? materialId : samples[0].id;
  const openConfirmed = () => {
    if (!confirmed || busy) return;
    const sourceMaterial = samples.find((item) => item.id === confirmed.material_id || item.version.id === confirmed.source_version_id);
    setMaterialId(sourceMaterial?.id || selected);
    setProfile(structuredClone(confirmed));
  };
  const preview = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const response = await api<{ profile: WritingSampleProfile }>(`/writing/documents/${document.id}/sample-profile/preview`, {
        method: 'POST', body: { material_id: selected },
      });
      setMaterialId(selected); setProfile(response.profile);
    } catch (failure) { onError(failure instanceof Error ? failure.message : '样稿结构提取失败'); }
    finally { setBusy(false); }
  };
  const apply = async () => {
    if (!profile || busy) return;
    setBusy(true);
    try {
      await api(`/writing/documents/${document.id}/sample-profile`, {
        method: 'PUT', body: { material_id: materialId, profile },
      });
      setProfile(null); await onChanged();
    } catch (failure) { onError(failure instanceof Error ? failure.message : '样稿结构确认失败'); }
    finally { setBusy(false); }
  };
  return <section className="content-card sample-profile-card">
    <div className="card-toolbar"><div><span className="eyebrow">样稿与格式</span><h2>提取目录和写作要求</h2></div><div className="task-intro-actions"><select aria-label="选择样稿" value={selected} onChange={(event) => { setMaterialId(event.target.value); setProfile(null); }}>{samples.map((item) => <option key={item.id} value={item.id}>{item.document.title} · V{item.version.version_number}</option>)}</select><button type="button" className="secondary" disabled={busy} onClick={() => void preview()}>{busy ? '提取中…' : confirmed ? '重新提取' : '提取样稿结构'}</button></div></div>
    <p className="field-help">只学习标题层级、正式文风与附件形式。样稿的地区、文号、职责和数字不会成为新文章的事实。</p>
    {confirmed && !profile && <div className="sample-profile-confirmed"><b>本文已确认 {confirmed.chapters.length} 个一级章节</b><span>{confirmed.chapters.map((item) => item.title).join(' · ')}</span><button type="button" className="text-button" onClick={openConfirmed}>查看已确认配置</button></div>}
    {profile && <div className="sample-profile-editor"><div><b>请核对并编辑</b><small>提取方法：{profile.status === 'draft' ? '真实样稿版式与编号' : '人工确认'} · {profile.chapters.length} 章 · {profile.attachments?.length || 0} 个附件</small></div>
      {profile.chapters.map((chapter, index) => <div className="sample-chapter-row" key={chapter.key}><label>一级标题 {index + 1}<input value={chapter.title} onChange={(event) => setProfile({ ...profile, chapters: profile.chapters.map((item, row) => row === index ? { ...item, title: event.target.value } : item) })} /></label><label>本章生成要求<textarea rows={2} value={chapter.instruction} onChange={(event) => setProfile({ ...profile, chapters: profile.chapters.map((item, row) => row === index ? { ...item, instruction: event.target.value } : item) })} /></label><small>参考层级：{chapter.subheadings?.join('、') || '未识别'}</small></div>)}
      <div className="sample-profile-details"><b>格式与附件</b><span>文体：{profile.style?.register || '正式文章'} · 标题编号：{profile.style?.heading_numbering || '按目录层级'}</span><span>附件：{profile.attachments?.length ? profile.attachments.map((item) => `${item.number} ${item.title}`).join('；') : '样稿没有附件'}</span><span>指标与公式候选：{profile.indicator_candidates?.length || 0} 项指标、{profile.formula_candidates?.length || 0} 个公式。样稿中的数字不自动成为本文输入。</span></div>
      <div className="sample-profile-footer"><span>先确认本次项目的输入和公式；生成正文时，采用的事实和计算会绑定到对应 Chunk，后续变更先预览影响再应用。</span><button type="button" className="primary compact" disabled={busy} onClick={() => void apply()}>{busy ? '保存中…' : '确认并用于这篇文章'}</button></div>
    </div>}
  </section>;
}
