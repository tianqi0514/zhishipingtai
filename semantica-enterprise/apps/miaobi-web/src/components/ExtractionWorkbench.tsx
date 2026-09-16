import { useEffect, useMemo, useState } from 'react';
import { Check, ChevronRight, Clipboard, Code2, Eye, LoaderCircle, RefreshCw } from 'lucide-react';
import { api } from '../api';
import type { ExtractionWorkbenchItem, ExtractionWorkbenchSnapshot, ExtractionWorkbenchStep } from '../types/domain';

const ROLE_LABELS: Record<string, string> = {
  policy_basis: '政策依据',
  task_data: '本次数据',
  reference: '参考材料',
  sample_style: '样稿格式',
  attachment: '附件',
};

const STATUS_LABELS: Record<ExtractionWorkbenchStep['status'], string> = {
  ready: '已就绪',
  needs_confirmation: '待确认',
  not_required: '本次不需要',
  waiting_material: '等待材料',
  waiting_result: '等待结果',
};

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') {
    const nested = value as Record<string, unknown>;
    if ('value' in nested) return valueText(nested.value);
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function itemTitle(step: ExtractionWorkbenchStep, item: ExtractionWorkbenchItem): string {
  if (step.key === 'material_role') return String(item.title || item.filename || '项目材料');
  if (step.key === 'sample_profile') return String(item.article || item.genre || '样稿结构');
  if (step.key === 'evidence') return String(item.title || item.id);
  if (step.key === 'entity') return String(item.name || item.original_text || item.id);
  if (step.key === 'claim' || step.key === 'relation') return `${valueText(item.subject)} — ${valueText(item.predicate)} → ${valueText(item.object)}`;
  return String(item.label || item.name || item.key || item.id);
}

function itemMeta(step: ExtractionWorkbenchStep, item: ExtractionWorkbenchItem): string {
  if (step.key === 'material_role') return `${ROLE_LABELS[String(item.role)] || valueText(item.role)} · V${valueText(item.version)}`;
  if (step.key === 'sample_profile') return `${valueText(item.genre)} · ${Array.isArray(item.chapters) ? item.chapters.length : 0} 个章节`;
  if (step.key === 'evidence') return [item.page ? `第 ${item.page} 页` : '', item.path, item.id].filter(Boolean).join(' · ');
  if (step.key === 'entity') return `${valueText(item.type)} · 置信度 ${Math.round(Number(item.confidence || 0) * 100)}%`;
  if (step.key === 'claim' || step.key === 'relation') return `${item.evidence_id ? `证据 ${item.evidence_id}` : '尚未绑定证据'} · 置信度 ${Math.round(Number(item.confidence || 0) * 100)}%`;
  if (step.key === 'fact') return `${valueText(item.value)} ${valueText(item.unit) === '—' ? '' : valueText(item.unit)} · ${valueText(item.status)}`;
  return `${valueText(item.value)} ${valueText(item.unit) === '—' ? '' : valueText(item.unit)} · ${item.kind === 'computed' ? '计算结果' : '原子指标'}`;
}

function OutputCards({ step }: { step: ExtractionWorkbenchStep }) {
  if (!step.items.length) {
    const message = step.status === 'not_required'
      ? '没有选择样稿，本步自动跳过。'
      : step.status === 'waiting_material'
        ? '添加材料后显示结果。'
        : '底座尚未产生这一步的真实结果。';
    return <div className="extraction-empty">{message}</div>;
  }
  return <div className="extraction-result-list">{step.items.map((item) => <article key={item.id}>
    <div className="extraction-result-title"><b>{itemTitle(step, item)}</b>{item.needs_confirmation && <span>待确认</span>}</div>
    {step.key === 'evidence' && <p>{String(item.text || '')}</p>}
    {step.key === 'claim' && Boolean(item.evidence) && <p>{String(item.evidence)}</p>}
    <small>{itemMeta(step, item)}</small>
  </article>)}</div>;
}

export function ExtractionWorkbench({ projectId, documentId, materialCount, onError }: {
  projectId: string;
  documentId?: string;
  materialCount: number;
  onError: (message: string) => void;
}) {
  const [snapshot, setSnapshot] = useState<ExtractionWorkbenchSnapshot | null>(null);
  const [selectedKey, setSelectedKey] = useState<ExtractionWorkbenchStep['key']>('material_role');
  const [view, setView] = useState<'input' | 'prompt' | 'result'>('result');
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [raw, setRaw] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const suffix = documentId ? `?document_id=${encodeURIComponent(documentId)}` : '';
      setSnapshot(await api<ExtractionWorkbenchSnapshot>(`/writing/projects/${projectId}/extraction-workbench${suffix}`));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : '抽取结果加载失败');
    } finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, [projectId, documentId, materialCount]);
  const selectedIndex = Math.max(0, snapshot?.steps.findIndex((item) => item.key === selectedKey) ?? 0);
  const selected = snapshot?.steps[selectedIndex] || null;
  const inputItems = useMemo(() => {
    if (!snapshot || !selected) return [];
    if (selectedIndex === 0) return selected.items;
    return snapshot.steps[selectedIndex - 1]?.items || [];
  }, [snapshot, selected, selectedIndex]);

  const copyPrompt = async () => {
    if (!selected) return;
    await navigator.clipboard.writeText(selected.prompt);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  if (!snapshot && loading) return <section className="content-card extraction-loading"><LoaderCircle className="spin" /><span>正在读取抽取结果</span></section>;
  if (!snapshot || !selected) return null;

  return <section className="content-card extraction-workbench">
    <div className="extraction-head">
      <div><span className="eyebrow">材料抽取</span><h2>从材料到可写事实</h2></div>
      <div className="extraction-summary"><span>{snapshot.material_count} 份材料</span><span>{snapshot.evidence_count} 个证据</span>{snapshot.pending_count > 0 && <span className="pending">{snapshot.pending_count} 项待确认</span>}<button type="button" className="icon-button" aria-label="刷新抽取结果" disabled={loading} onClick={() => void load()}><RefreshCw size={16} className={loading ? 'spin' : ''} /></button></div>
    </div>
    <div className="extraction-stepbar" aria-label="材料抽取步骤">{snapshot.steps.map((step, index) => <button type="button" key={step.key} className={selected.key === step.key ? 'active' : ''} onClick={() => { setSelectedKey(step.key); setRaw(false); }}>
      <span>{index + 1}</span><b>{step.short_title}</b><small>{step.count || '—'}</small>{index < snapshot.steps.length - 1 && <ChevronRight size={13} />}
    </button>)}</div>
    <div className="extraction-toolbar">
      <div><b>{selected.title}</b><span className={`extraction-state ${selected.status}`}>{STATUS_LABELS[selected.status]}</span></div>
      <div className="extraction-view-tabs">
        <button type="button" className={view === 'input' ? 'active' : ''} onClick={() => setView('input')}>输入</button>
        <button type="button" className={view === 'prompt' ? 'active' : ''} onClick={() => setView('prompt')}>提示词</button>
        <button type="button" className={view === 'result' ? 'active' : ''} onClick={() => setView('result')}>结果</button>
      </div>
      {view === 'prompt' && <button type="button" className="secondary compact" onClick={() => void copyPrompt()}>{copied ? <Check size={14} /> : <Clipboard size={14} />}{copied ? '已复制' : '复制提示词'}</button>}
      {view === 'result' && <button type="button" className="secondary compact" onClick={() => setRaw((value) => !value)}>{raw ? <Eye size={14} /> : <Code2 size={14} />}{raw ? '卡片' : 'JSON'}</button>}
    </div>
    <div className="extraction-body">
      {view === 'input' && <><div className="extraction-context-label"><span>{selected.input_label}</span><b>{inputItems.length} 项</b></div>{inputItems.length ? <pre className="extraction-json">{JSON.stringify(inputItems, null, 2)}</pre> : <div className="extraction-empty">当前没有可用输入。</div>}</>}
      {view === 'prompt' && <pre className="extraction-prompt">{selected.prompt}</pre>}
      {view === 'result' && (raw ? <pre className="extraction-json">{JSON.stringify(selected.items, null, 2)}</pre> : <OutputCards step={selected} />)}
    </div>
  </section>;
}
