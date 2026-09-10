import { useEffect, useState } from 'react';
import { api } from '../api';

export type KnowledgeSetting = { ontology_version_id?: string | null; entity_types: string[]; predicates: string[]; rule_version_ids: string[] };
type Options = { ontologies: { id: string; name: string; version: number; entity_types: string[]; predicates: string[] }[]; rules: { id: string; name: string; version: number }[] };
type Source = { chunk_id: string; title: string; version_number?: number; page_number?: number; structural_path?: string };
type Reference = { ref: string; text: string; kind: string; sources: Source[]; premises: { text: string; source: Source }[]; rule_version_id?: string };
type Packet = { run_id: string; references: Record<string, Reference>; warnings: string[]; comparison: { new: number; unchanged: number; removed: number; affected_blocks: string[] }; sections: { key: string; title: string; references: string[]; warnings: string[]; configured: boolean; relation_count: number; conclusion_count: number }[] };

export function ChapterKnowledgeSettings({ value, onChange }: { value?: KnowledgeSetting; onChange: (v: KnowledgeSetting) => void }) {
  const [options, setOptions] = useState<Options>({ ontologies: [], rules: [] });
  const [error, setError] = useState('');
  useEffect(() => { let active = true; api<Options>('/writing/knowledge-options').then(v => { if (active) setOptions(v); }).catch(e => { if (active) setError(String(e.message)); }); return () => { active = false; }; }, []);
  const selected = value || { entity_types: [], predicates: [], rule_version_ids: [] };
  const ontology = options.ontologies.find(o => o.id === selected.ontology_version_id);
  const toggle = (key: 'entity_types' | 'predicates' | 'rule_version_ids', item: string) => onChange({ ...selected, [key]: selected[key].includes(item) ? selected[key].filter(v => v !== item) : [...selected[key], item] });
  return <details className="chapter-knowledge-settings"><summary>本章需要哪些对象、关系与规则</summary>
    {error && <p role="alert">{error}</p>}
    <label>语义模型<select value={selected.ontology_version_id || ''} onChange={e => onChange({ ...selected, ontology_version_id: e.target.value || null, entity_types: [], predicates: [], rule_version_ids: [] })}><option value="">只检索原文，不扩展关系</option>{options.ontologies.map(o => <option key={o.id} value={o.id}>{o.name} · V{o.version}</option>)}</select></label>
    {ontology && <>{(['entity_types', 'predicates'] as const).map(key => <fieldset key={key}><legend>{key === 'entity_types' ? '需要覆盖的业务对象' : '需要核验的关系'}</legend>{ontology[key].map(label => <label className="knowledge-chip" key={label}><input type="checkbox" checked={selected[key].includes(label)} onChange={() => toggle(key, label)} />{label}</label>)}</fieldset>)}</>}
    {ontology && <fieldset><legend>需要的规则结论</legend>{options.rules.length ? options.rules.map(r => <label className="knowledge-chip" key={r.id}><input type="checkbox" checked={selected.rule_version_ids.includes(r.id)} onChange={() => toggle('rule_version_ids', r.id)} />{r.name} · V{r.version}</label>) : <small>智库中还没有已启用的业务规则。</small>}</fieldset>}
  </details>;
}

export function ChapterEvidencePanel({ projectId, onChanged }: { projectId: string; onChanged: () => Promise<void> }) {
  const [packet, setPacket] = useState<Packet | null>(null);
  const [configured, setConfigured] = useState(false);
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { let active = true; setPacket(null); setPreview(false); api<{ applied: Packet | null; configured: boolean }>(`/writing/projects/${projectId}/chapter-evidence`).then(v => { if (active) { setPacket(v.applied); setConfigured(v.configured); } }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [projectId]);
  const execute = async (apply: boolean) => { if (busy) return; setBusy(true); setError(''); try {
    const path = apply ? `${packet!.run_id}/apply` : 'preview';
    const next = await api<Packet>(`/writing/projects/${projectId}/chapter-evidence/${path}`, { method: 'POST' });
    setPacket(next); setPreview(!apply); if (apply) await onChanged();
  } catch (e) { setError(e instanceof Error ? e.message : '章节依据准备失败'); } finally { setBusy(false); } };
  if (!configured && !error) return null;
  return <section className="chapter-evidence-panel"><header><div><h3>章节依据</h3><small>{packet && !preview ? '已确认，用于生成正文' : '先检查关系与结论，再用于写作'}</small></div><button type="button" className="secondary compact" disabled={busy} onClick={() => void execute(false)}>{busy ? '处理中…' : packet ? '重新检查' : '准备章节依据'}</button></header>
    {error && <p role="alert" className="warning-mini">{error}</p>}{packet?.warnings.map(w => <p className="warning-mini" key={w}>{w}</p>)}
    {packet?.sections.filter(s => s.configured).map(section => <details key={section.key} open><summary>{section.title}<small>{section.relation_count} 条已有关系 · {section.conclusion_count} 条规则结论</small></summary>{section.warnings.map(w => <p className="warning-mini" key={w}>{w}</p>)}{section.references.map(ref => { const item = packet.references[ref]; return <details className="relation-proof" key={ref}><summary><span className="status">{item.kind === 'inference' ? '规则结论' : '已有关系'}</span>{item.text}</summary>{item.premises.map((p, i) => <p key={i}>{p.text}<small>{p.source.title} · {p.source.page_number ? `第 ${p.source.page_number} 页` : p.source.structural_path}</small></p>)}</details>; })}</details>)}
    {preview && packet && <footer><span>新增 {packet.comparison.new} · 保留 {packet.comparison.unchanged} · 失效 {packet.comparison.removed}</span><button className="primary compact" type="button" disabled={busy} onClick={() => void execute(true)}>确认用于写作</button></footer>}
  </section>;
}

type Paragraph = { block_id: string; section: string; text: string; status: string; evidence: { type: string; status: string; chunk_id?: string; query_run_id?: string; metadata: { source_title?: string; formula?: string; input_keys?: string[]; knowledge_evidence?: Reference[] }; computation_run_id?: string }[] };
export function ParagraphEvidenceList({ documentId, versionId, projectId }: { documentId: string; versionId?: string; projectId: string }) {
  const [rows, setRows] = useState<Paragraph[]>([]);
  const [activeBlock, setActiveBlock] = useState('');
  const [review, setReview] = useState('');
  const [reason, setReason] = useState('');
  const [reviewBusy, setReviewBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => { const listener = (event: Event) => setActiveBlock(String((event as CustomEvent).detail)); window.addEventListener('miaobi:paragraph-selected', listener); return () => window.removeEventListener('miaobi:paragraph-selected', listener); }, []);
  const [error, setError] = useState('');
  const [source, setSource] = useState<{ title?: string; text: string } | null>(null);
  useEffect(() => { let active = true; setRows([]); setSource(null); setError(''); api<{ paragraphs: Paragraph[] }>(`/writing/documents/${documentId}/paragraph-evidence`).then(v => { if (active) setRows(v.paragraphs); }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [documentId, versionId, revision]);
  const confirmReview = async () => { if (reviewBusy || reason.trim().length < 4) return; setReviewBusy(true); try {
    await api(`/writing/documents/${documentId}/paragraphs/${review}/review`, { method: 'POST', body: { document_version_id: versionId, reason } });
    setReview(''); setReason(''); setRevision(v => v + 1);
  } catch (e) { setError(e instanceof Error ? e.message : '核对失败'); } finally { setReviewBusy(false); } };
  const openSource = async (chunk: string) => { try { setSource(await api(`/writing/projects/${projectId}/chapter-evidence/source/${chunk}`)); } catch (e) { setError(e instanceof Error ? e.message : '来源无法访问'); } };
  return <section className="paragraph-evidence-list" aria-label="正文段落依据"><h3>正文依据</h3>{error && <p role="alert">{error}</p>}{source && <article className="source-preview"><button type="button" onClick={() => setSource(null)}>关闭来源</button><b>{source.title}</b><p>{source.text}</p></article>}
    {rows.map(row => <details key={row.block_id} open={activeBlock === row.block_id ? true : undefined} className={activeBlock === row.block_id ? 'active-paragraph' : ''}><summary><small>{row.section} · {row.status === 'stale' ? '需要更新' : row.evidence.length ? '有来源绑定' : '未绑定依据'}</small>{row.text || '引用'} </summary>
      {row.evidence.map((e, i) => <div className="paragraph-source" key={i}>{e.type === 'model_extraction' ? <small>AI 草稿，需人工核对措辞</small> : <b>{e.metadata.source_title || ({ computation: '确定性计算', semantica_inference: '规则结论', policy_document: '文档依据' } as Record<string, string>)[e.type] || '来源依据'}</b>}
      {e.metadata.knowledge_evidence?.map(ref => <article key={ref.ref}><b>{ref.text}</b>{ref.premises.map((p, j) => <p key={j}>{p.text}<button type="button" className="text-button" onClick={() => void openSource(p.source.chunk_id)}>{p.source.title}{p.source.page_number ? ` · 第 ${p.source.page_number} 页` : ''}</button></p>)}</article>)}
      {!e.metadata.knowledge_evidence?.length && e.chunk_id && e.query_run_id && <button type="button" className="text-button" onClick={() => { api<{ document_title: string; text: string }>(`/writing/projects/${projectId}/knowledge/fragments/${e.chunk_id}?query_run_id=${e.query_run_id}`).then(v => setSource({ title: v.document_title, text: v.text })).catch(x => setError(x.message)); }}>打开来源</button>}
      {!!e.metadata.input_keys?.length && <small>已绑定 {e.metadata.input_keys.length} 项业务输入；输入变化后本段需重新核对。</small>}</div>)}
      {row.evidence.some(e => e.type === 'model_extraction' && e.status === 'stale') && <button type="button" className="secondary compact" onClick={() => { setReview(row.block_id); setReason(''); }}>已修改并核对本段</button>}
      {review === row.block_id && <div><label>核对说明<textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="请先保存正文修改，再说明已核对的输入和措辞" /></label><button type="button" disabled={reviewBusy || reason.trim().length < 4 || !versionId} onClick={() => void confirmReview()}>确认核对</button><button type="button" disabled={reviewBusy} onClick={() => setReview('')}>取消</button></div>}
    </details>)}
  </section>;
}
