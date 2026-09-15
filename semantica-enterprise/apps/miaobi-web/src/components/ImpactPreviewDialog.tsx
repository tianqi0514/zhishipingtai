import { useMemo, useState } from 'react';
import type { WritingInputChange } from '../types/domain';

function valueText(value: Record<string, unknown>): string {
  return String(value.number ?? value.value ?? value.text ?? '—');
}

export function ImpactPreviewDialog({ preview, submitting, onCancel, onApply }: {
  preview: WritingInputChange;
  submitting: boolean;
  onCancel: () => void;
  onApply: (acceptedBlockIds: string[]) => void;
}) {
  const selectable = useMemo(() => (preview.impact.content_proposals || []).filter((item) => item.selectable), [preview]);
  const [accepted, setAccepted] = useState<string[]>(() => selectable.map((item) => item.block_id));
  const allSelected = selectable.length > 0 && accepted.length === selectable.length;
  const toggle = (blockId: string) => setAccepted((current) => current.includes(blockId)
    ? current.filter((id) => id !== blockId)
    : [...current, blockId]);
  return <div className="dialog-backdrop" role="presentation"><section className="dialog impact-dialog" role="dialog" aria-modal="true" aria-label="指标变更影响预览">
    <div className="dialog-head"><div><span className="eyebrow">变更预览</span><h2>选择要同步到文章的内容</h2></div><button type="button" className="icon-button" disabled={submitting} onClick={onCancel} aria-label="关闭影响预览">×</button></div>
    <div className="impact-changes">{preview.changes.map((item) => <div key={item.fact_key}><b>{item.label}</b><span>{valueText(item.old_value)} → {valueText(item.new_value)} {item.unit || ''}</span></div>)}</div>
    <h3>计算结果会重新计算</h3>{preview.impact.calculations?.length ? <div className="impact-calculations">{preview.impact.calculations.map((item) => <div key={item.result_key}><b>{item.label}</b><span>{item.old_value} → <strong>{item.new_value}</strong> {item.unit || ''}</span></div>)}</div> : <p className="field-help">没有受影响的确定性计算。</p>}
    <div className="impact-proposal-head"><h3>文章中的影响位置</h3><button type="button" className="secondary compact" disabled={submitting || !selectable.length} onClick={() => setAccepted(allSelected ? [] : selectable.map((item) => item.block_id))}>{allSelected ? '取消全选' : '全选可更新内容'}</button></div>
    <p className="field-help">勾选控制正文是否采用新值；未勾选的段落保留原文并标记待核对。无法安全替换的表述不会被自动勾选。</p>
    <div className="impact-proposals">{(preview.impact.content_proposals || []).map((item) => <label className={item.selectable ? 'impact-proposal' : 'impact-proposal review-only'} key={item.block_id}>
      <input type="checkbox" checked={accepted.includes(item.block_id)} disabled={submitting || !item.selectable} onChange={() => toggle(item.block_id)} aria-label={`采用${item.section}中的更新`} />
      <span><b>{item.section} · {item.kind === 'computed_metric' ? '测算值' : '正文'}</b><small>{item.reason || (item.selectable ? '可更新' : '待人工核对')}</small><em>{item.old_text || '当前内容未找到'}</em>{item.selectable && <strong>→ {item.new_text}</strong>}</span>
    </label>)}{!preview.impact.content_proposals?.length && <p className="field-help">当前文章没有登记受影响的内容；输入仍可更新，文章保持原样。</p>}</div>
    <div className="dialog-actions"><button type="button" className="secondary" disabled={submitting} onClick={onCancel}>取消</button><button type="button" className="primary" disabled={submitting} onClick={() => onApply(accepted)}>{submitting ? '应用中…' : `应用选中的 ${accepted.length} 处`}</button></div>
  </section></div>;
}
