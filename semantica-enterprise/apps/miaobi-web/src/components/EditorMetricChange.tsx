import { useState } from 'react';
import { api } from '../api';
import type { Fact, WritingDocument, WritingInputChange } from '../types/domain';
import { ImpactPreviewDialog } from './ImpactPreviewDialog';

export function EditorMetricChange({ projectId, document, facts, dirty, onChanged, onError }: {
  projectId: string; document: WritingDocument; facts: Fact[]; dirty: boolean;
  onChanged: () => Promise<void>; onError: (message: string) => void;
}) {
  const inputs = facts.filter((fact) => !['deterministic_computation', 'semantica_inference'].includes(fact.fact_type)
    && typeof (fact.value.number ?? fact.value.value) === 'number');
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [reason, setReason] = useState('已核对本次资料');
  const [preview, setPreview] = useState<WritingInputChange | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const selected = inputs.find((fact) => fact.fact_key === key);
  const start = () => { setKey(inputs[0]?.fact_key || ''); setValue(''); setReason('已核对本次资料'); setOpen(true); };
  const requestPreview = async () => {
    if (submitting || dirty || !selected || !value.trim() || reason.trim().length < 2) return;
    const number = Number(value);
    if (!Number.isFinite(number)) { onError('请输入有效数值'); return; }
    setSubmitting(true);
    try {
      const row = await api<WritingInputChange>(`/writing/projects/${projectId}/input-changes/preview`, {
        method: 'POST', body: { document_id: document.id, changes: [{ fact_key: key, new_value: { number }, reason: reason.trim() }] },
      });
      setPreview(row); setOpen(false);
    } catch (failure) { onError(failure instanceof Error ? failure.message : '指标影响预览失败'); }
    finally { setSubmitting(false); }
  };
  const cancel = async () => {
    if (!preview || submitting) return;
    setSubmitting(true);
    try { await api(`/writing/projects/${projectId}/input-changes/${preview.id}/cancel`, { method: 'POST' }); }
    catch (failure) { onError(failure instanceof Error ? failure.message : '取消预览失败'); }
    finally { setPreview(null); setSubmitting(false); }
  };
  const apply = async (acceptedBlockIds: string[]) => {
    if (!preview || submitting || dirty) return;
    setSubmitting(true);
    try {
      await api(`/writing/projects/${projectId}/input-changes/apply`, {
        method: 'POST', body: { preview_id: preview.id, accepted_block_ids: acceptedBlockIds },
      });
      setPreview(null);
      await onChanged();
    } catch (failure) { onError(failure instanceof Error ? failure.message : '应用指标变化失败'); }
    finally { setSubmitting(false); }
  };
  return <>
    <button type="button" className="writing-metric-change" onClick={start} disabled={!inputs.length || dirty} title={dirty ? '先保存正文，再预览指标变化' : '先预览绑定内容的变化，再选择应用'}>修改输入指标 · 预览正文影响</button>
    {open && <div className="dialog-backdrop" role="presentation"><section className="dialog compact-dialog" role="dialog" aria-modal="true" aria-label="修改输入指标">
      <div className="dialog-head"><div><span className="eyebrow">编辑器 · 输入变化</span><h2>选择要修改的原始指标</h2></div><button type="button" className="icon-button" disabled={submitting} onClick={() => setOpen(false)}>×</button></div>
      <label>原始指标<select value={key} onChange={(event) => setKey(event.target.value)}>{inputs.map((fact) => <option key={fact.id} value={fact.fact_key}>{fact.label} · {String(fact.value.number ?? fact.value.value)}{fact.unit || ''}</option>)}</select></label>
      <label>修改后的值<input autoFocus type="number" value={value} onChange={(event) => setValue(event.target.value)} /></label>
      <label>依据或原因<textarea rows={2} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <p className="field-help">不会立即改写正文。下一步列出计算结果与每一处已绑定的文章内容。</p>
      <div className="dialog-actions"><button type="button" className="secondary" disabled={submitting} onClick={() => setOpen(false)}>取消</button><button type="button" className="primary" disabled={submitting || !value.trim() || reason.trim().length < 2} onClick={() => void requestPreview()}>查看影响</button></div>
    </section></div>}
    {preview && <ImpactPreviewDialog key={preview.id} preview={preview} submitting={submitting} onCancel={() => void cancel()} onApply={(ids) => void apply(ids)} />}
  </>;
}
