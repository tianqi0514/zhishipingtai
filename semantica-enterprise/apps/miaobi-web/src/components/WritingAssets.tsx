import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { createClientId } from '../ids';
import type { ProjectMaterial, WritingDocument } from '../types/domain';

type Asset = {
  id: string; title: string; kind: string; version: number; status: string; checksum: string;
  definition: { kind: string; key: string; purpose: string; scope: string; used_in_sections: string[]; evidence_ids: string[]; payload: Record<string, unknown> };
  validation: { execution: string; ontology?: { passed: boolean; issues: unknown[] } };
};
type Release = { id: string; release_number: number; snapshot: { assets: Array<{ id: string }> } };
type Trace = { input: unknown[]; runtime_prompt: string; output: unknown; process: { status: string; metrics: { coverage?: string } } };
type Kinds = { kinds: Array<{ key: string; label: string }>; engine: { version: string } };
const statusLabels: Record<string, string> = { candidate: '待确认', reviewed: '已确认', rejected: '已拒绝' };

const fieldLabels: Record<string, string> = {
  chapters: '章节职责', fields: '写作输入', inputs: '计算输入', output: '输出指标', expression: '计算公式',
  applicability: '适用条件', precision: '小数位', classes: '对象类型', properties: '关系定义', purpose: '用途',
  conditions: '判断条件', conclusion: '结论', premises: '前提', limitations: '限制', assumptions: '假设', tasks: '任务依赖',
  attachments: '附件', style: '表达要求', name: '名称', title: '标题', unit: '单位', instruction: '填写要求', checks: '检查内容',
  stage: '检查阶段', strategy: '复用方式', tool_names: '所需工具', steps: '执行步骤', key: '标识', value_type: '数据类型',
  required: '必填', citation_required: '需要依据', implementation_ref: '实现引用', instructions: '写作指令', origin_note: '来源说明',
  entity_types: '实体类型', predicates: '关系', identity_rules: '归一要求', rule_keys: '规则依赖', required_inputs: '必需输入',
  question: '业务问题', expected_output: '预期输出', condition: '校验条件', message: '提示', severity: '严重程度',
  task: '任务', action: '操作', depends_on: '前置任务', datalog: '技术规则', domain: '主体类型', range: '客体类型',
};
const valueLabels: Record<string, string> = { number: '数值', text: '文字', boolean: '是或否', date: '日期', materials: '准备资料', generation: '写作', finalization: '定稿', warning: '提醒', error: '必须处理', reuse: '直接复用', parameterize: '填写本次参数', branch: '调整后复用', rebuild: '重新建立', reject: '不可复用' };
function DefinitionValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) return <div>{value.length ? value.map((item, index) => <div className="asset-definition-item" key={index}><DefinitionValue value={item} /></div>) : '无'}</div>;
  if (value && typeof value === 'object') return <DefinitionView payload={value as Record<string, unknown>} />;
  if (typeof value === 'boolean') return <>{value ? '是' : '否'}</>;
  const text = String(value ?? '未指定');
  return <>{valueLabels[text] || text || '无'}</>;
}
function DefinitionView({ payload }: { payload: Record<string, unknown> }) {
  return <dl className="asset-definition">{Object.entries(payload).map(([key, value]) => <div key={key}>
    <dt>{fieldLabels[key] || key}</dt><dd><DefinitionValue value={value} /></dd>
  </div>)}</dl>;
}
function sourcePosition(value: unknown): string {
  if (!value || typeof value !== 'object') return '原文位置未提供';
  const locator = value as Record<string, unknown>;
  const span = locator.source_span as Record<string, unknown> | undefined;
  const parts = [locator.page ? `第 ${locator.page} 页` : '', locator.sheet ? `工作表 ${locator.sheet}` : '',
    locator.structural_path ? `位置 ${locator.structural_path}` : '',
    span?.char_start !== undefined ? `原文字符 ${span.char_start}—${span.char_end}` : ''];
  return parts.filter(Boolean).join(' · ') || '原文定位信息见原始输出';
}

export function WritingAssets({ projectId, spaceId, document, materials, onChanged, onError }: {
  projectId: string; spaceId?: string; document: WritingDocument | null; materials: ProjectMaterial[];
  onChanged: () => Promise<void> | void; onError: (message: string) => void;
}) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [releases, setReleases] = useState<Release[]>([]);
  const [kinds, setKinds] = useState<Kinds | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState('');
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<Asset | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [view, setView] = useState('definition');
  const [reason, setReason] = useState('');
  const [tests, setTests] = useState<Array<{ inputs: Record<string, string>; expected: string }>>([]);
  const [unitConfirmed, setUnitConfirmed] = useState(false);
  const [revision, setRevision] = useState('');
  const [job, setJob] = useState<{ id: string; status: string; progress: number; error_message?: string } | null>(null);
  const [materialId, setMaterialId] = useState('');
  const [adoption, setAdoption] = useState<{ documentId: string; body: Record<string, unknown>; titles: string[] } | null>(null);
  const [calculated, setCalculated] = useState<Array<{ name: string; value: string; unit: string; reused: boolean }>>([]);
  const loadId = useRef(0);
  const detailId = useRef('');
  const contextId = useRef('');
  contextId.current = `${projectId}:${document?.id || ''}`;

  async function load() {
    const requestId = ++loadId.current;
    const [rows, catalog, published] = await Promise.all([
      api<Asset[]>(`/writing-assets?project_id=${encodeURIComponent(projectId)}`),
      api<Kinds>('/writing-assets/kinds'),
      spaceId ? api<Release[]>(`/writing-assets/releases?space_id=${encodeURIComponent(spaceId)}`) : Promise.resolve([]),
    ]);
    if (requestId === loadId.current) { setAssets(rows); setKinds(catalog); setReleases(published); }
  }
  useEffect(() => { setSelected([]); setDetail(null); setJob(null); setMaterialId(''); detailId.current = ''; void load().catch((error) => onError(error.message)); return () => { loadId.current++; detailId.current = ''; }; }, [projectId, spaceId, materials.length]);
  useEffect(() => { setAdoption(null); setCalculated([]); }, [projectId, document?.id]);
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api<typeof job>(`/jobs/${job.id}`).then((next) => {
        if (!active) return;
        setJob(next);
        if (next && !['queued', 'running'].includes(next.status)) void load().catch((error) => onError(error.message));
      }).catch((error) => { if (active) onError(error.message); });
    }, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [job?.id, job?.status]);

  async function action(operation: () => Promise<unknown>) {
    if (busy) return;
    const originalContext = contextId.current;
    setBusy(true);
    try { await operation(); if (contextId.current !== originalContext) return; await load(); await onChanged(); }
    catch (error) { onError(error instanceof Error ? error.message : '操作失败'); }
    finally { setBusy(false); }
  }
  async function open(asset: Asset) {
    detailId.current = asset.id;
    setDetail(asset); setView('definition'); setReason(''); setUnitConfirmed(false);
    const variables = (asset.definition.payload.inputs || []) as Array<{ key: string }>;
    setTests([0, 1].map(() => ({ inputs: Object.fromEntries(variables.map((item) => [item.key, ''])), expected: '' })));
    setRevision(JSON.stringify(asset.definition, null, 2)); setTrace(null);
    try { const response = await api<Trace>(`/writing-assets/${asset.id}/trace`); if (detailId.current === asset.id) setTrace(response); }
    catch (error) { onError(error instanceof Error ? error.message : '来源读取失败'); }
  }
  const chosen = assets.filter((asset) => selected.includes(asset.id));
  const release = releases.find((item) => selected.every((id) => item.snapshot.assets.some((asset) => asset.id === id)));
  const adoptedBundle = document?.applicability?.asset_bundle as { release_id: string; checksum: string; assets?: Array<{ id: string; definition: { kind: string } }> } | undefined;
  const adoptedMethods = (adoptedBundle?.assets || []).filter((item) => item.definition?.kind === 'computation_method');
  const rows = filter ? assets.filter((asset) => asset.kind === filter) : assets;
  const labels = Object.fromEntries(kinds?.kinds.map((item) => [item.key, item.label]) || []);
  const formulaInputs = (detail?.definition.payload.inputs || []) as Array<{ key: string; name: string; unit: string }>;
  if (!spaceId) return null;
  return <section className="content-card writing-assets" aria-label="可复用写作资产">
    <div className="extraction-head"><div><span className="eyebrow">历史资料复用</span><h2>文章结构与计算方法</h2></div><button type="button" className="secondary compact" disabled={busy} onClick={() => void action(load)}>刷新</button></div>
    <div className="asset-actions">
      <select aria-label="选择提炼材料" value={materialId} onChange={(event) => setMaterialId(event.target.value)}><option value="">选择已解析材料</option>{materials.map((material) => <option key={material.id} value={material.id}>{material.document.title || material.version.filename}</option>)}</select>
      <button type="button" className="primary" disabled={busy || !materialId || !!job && ['queued', 'running'].includes(job.status)} onClick={() => void action(async () => {
        const material = materials.find((item) => item.id === materialId)!;
        setJob(await api('/writing-assets/extract', { method: 'POST', body: { version_id: material.version_id, material_role: material.material_role, request_id: createClientId() } }));
      })}>提炼可复用内容</button>
      <select aria-label="资产类型" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="">全部资产类型</option>{kinds?.kinds.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select>
    </div>
    {job && <p role="status">{job.status === 'succeeded' ? '提炼完成' : job.status === 'failed' ? job.error_message : '正在提炼已解析资料'}{job.status === 'succeeded' ? '' : ` · ${job.progress}%`}</p>}
    {document && adoptedMethods.length > 0 && <div className="asset-actions"><button type="button" className="secondary" disabled={busy || document.status === 'published'} onClick={() => void action(async () => {
      const originalContext = contextId.current;
      setCalculated([]);
      const result = await api<{ items: Array<{ fact: { label: string; value: { number: number }; unit?: string }; reused: boolean }> }>(`/writing-assets/documents/${document.id}/compute`, { method: 'POST', body: {
        base_version_id: document.current_version_id || null, release_id: adoptedBundle!.release_id, checksum: adoptedBundle!.checksum,
      } });
      if (contextId.current === originalContext) setCalculated(result.items.map((item) => ({ name: item.fact.label,
        value: String(item.fact.value.number), unit: item.fact.unit || '', reused: item.reused })));
    })}>{busy ? '处理中…' : '计算本次结果'}</button><small>按已确认的方法依赖执行，不修改正文</small></div>}
    {calculated.length > 0 && <div role="status" className="asset-calculation-result">{calculated.map((item) => <p key={item.name}>{item.name}：{item.value}{item.unit ? ` ${item.unit}` : ''}<small> · {item.reused ? '输入未变，使用已核验结果' : '已按当前确认输入计算'}</small></p>)}</div>}
    {!assets.length && <div className="extraction-empty">从历史材料提炼结构、输入和方法；新文章仍需要当前业务数据。</div>}
    <div className="asset-list">{rows.map((asset) => <article key={asset.id}>
      <input type="checkbox" aria-label={`选择${asset.title}`} checked={selected.includes(asset.id)} onChange={(event) => setSelected((items) => event.target.checked ? [...items, asset.id] : items.filter((id) => id !== asset.id))} />
      <button type="button" className="asset-open" onClick={() => void open(asset)}><b>{asset.title}</b><small>{labels[asset.kind]} · V{asset.version} · {statusLabels[asset.status] || asset.status}</small><span>{asset.definition.purpose}</span></button>
    </article>)}</div>
    {assets.length >= 500 && <small>当前显示最近500个版本，请使用类型筛选。</small>}
    {chosen.length > 0 && <div className="asset-actions"><span>已选 {chosen.length} 项</span>
      <button type="button" className="secondary" disabled={busy || chosen.some((item) => item.status !== 'reviewed')} onClick={() => void action(() => api('/writing-assets/releases', { method: 'POST', body: { space_id: spaceId, asset_ids: selected } }))}>发布选中资产</button>
      {document && <button type="button" className="primary" disabled={busy || !release} onClick={() => {
        setAdoption({ documentId: document.id, titles: chosen.map((item) => item.title), body: {
          release_id: release!.id, asset_ids: [...selected], base_version_id: document.current_version_id || null,
          scope_confirmed: true, adopt_outline: !(document.applicability?.native_outline) && chosen.filter((item) => item.kind === 'report_structure').length === 1,
        } });
      }}>用于当前文章</button>}
    </div>}
    {adoption && <div className="dialog-backdrop"><section className="dialog compact-dialog" role="dialog" aria-modal="true" aria-label="确认用于本文">
      <div className="dialog-head"><h2>确认用于本文</h2><button type="button" className="icon-button" aria-label="关闭采用确认" disabled={busy} onClick={() => setAdoption(null)}>×</button></div>
      <ul>{adoption.titles.map((title) => <li key={title}>{title}</li>)}</ul>
      <p>只采用结构与方法，历史数字不会填入本次数据。已有正文不被覆盖。</p>
      <div className="dialog-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setAdoption(null)}>取消</button><button type="button" className="primary" disabled={busy} onClick={() => void action(async () => {
        await api(`/writing-assets/documents/${adoption.documentId}/adopt`, { method: 'POST', body: adoption.body });
        setAdoption(null);
      })}>{busy ? '采用中…' : '确认采用'}</button></div>
    </section></div>}
    {detail && <div className="dialog-backdrop"><section className="dialog asset-dialog" role="dialog" aria-modal="true" aria-label="写作资产详情">
      <div className="dialog-head"><h2>{detail.title}</h2><button type="button" className="icon-button" aria-label="关闭资产详情" disabled={busy} onClick={() => setDetail(null)}>×</button></div>
      <nav className="asset-actions">{[['definition','资产内容'],['input','来源输入'],['prompt','实际提示词'],['output','原始输出'],['revise','修订定义']].map(([key, label]) => <button type="button" key={key} className={view === key ? 'primary compact' : 'secondary compact'} onClick={() => setView(key)}>{label}</button>)}</nav>
      {view === 'definition' && <><p>{detail.definition.scope}</p><DefinitionView payload={detail.definition.payload} /><small>用于：{detail.definition.used_in_sections.join('、') || '需确认适用章节'} · {detail.definition.evidence_ids.length} 个来源</small>{detail.validation.execution === 'not_implemented' && <p className="asset-warning">这是定义候选，尚无执行实现；确认不会把它变成可运行工具。</p>}</>}
      {view === 'input' && <div className="asset-sources">{trace?.input.map((item, index) => { const source = item as Record<string, unknown>; return <article key={index}><b>{String(source.filename)} · V{String(source.file_version)}</b><small>{sourcePosition(source.locator)}</small><p>{String(source.text)}</p></article>; })}</div>}
      {view === 'prompt' && <pre className="extraction-prompt">{trace?.runtime_prompt || '原任务没有保存实际提示词，不能以说明模板代替。'}</pre>}
      {view === 'output' && <pre className="extraction-json">{JSON.stringify(trace?.output, null, 2)}</pre>}
      {view === 'revise' && <><textarea aria-label="资产定义JSON" className="asset-json-editor" value={revision} onChange={(event) => setRevision(event.target.value)} /><button type="button" className="secondary" disabled={busy} onClick={() => void action(async () => { await api(`/writing-assets/${detail.id}/revise`, { method: 'POST', body: { checksum: detail.checksum, definition: JSON.parse(revision) } }); setDetail(null); })}>保存为新候选版本</button></>}
      {detail.status === 'candidate' && <div className="asset-review"><label>确认或拒绝的理由<input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        {detail.kind === 'computation_method' && <><label><input type="checkbox" checked={unitConfirmed} onChange={(event) => setUnitConfirmed(event.target.checked)} />已核对单位、适用条件和舍入</label><div className="asset-formula-tests">{tests.map((test, index) => <fieldset key={index}><legend>{index === 0 ? '正常输入验证' : '边界输入验证'}</legend>{formulaInputs.map((input) => <label key={input.key}>{input.name}{input.unit ? `（${input.unit}）` : ''}<input inputMode="decimal" aria-label={`${index + 1}-${input.name}`} value={test.inputs[input.key]} onChange={(event) => setTests((rows) => rows.map((row, position) => position === index ? { ...row, inputs: { ...row.inputs, [input.key]: event.target.value } } : row))} /></label>)}<label>预期结果<input inputMode="decimal" aria-label={`${index + 1}-预期结果`} value={test.expected} onChange={(event) => setTests((rows) => rows.map((row, position) => position === index ? { ...row, expected: event.target.value } : row))} /></label></fieldset>)}</div></>}
        <div className="dialog-actions"><button type="button" className="secondary" disabled={busy || reason.trim().length < 2} onClick={() => void action(async () => { await api(`/writing-assets/${detail.id}/review`, { method: 'POST', body: { checksum: detail.checksum, action: 'reject', reason } }); setDetail(null); })}>拒绝</button><button type="button" className="primary" disabled={busy || reason.trim().length < 2 || detail.kind === 'computation_method' && (!unitConfirmed || tests.some((test) => !test.expected.trim() || Object.values(test.inputs).some((value) => !value.trim())))} onClick={() => void action(async () => { await api(`/writing-assets/${detail.id}/review`, { method: 'POST', body: { checksum: detail.checksum, action: 'accept', reason, unit_confirmed: unitConfirmed, tests: detail.kind === 'computation_method' ? tests : [] } }); setDetail(null); })}>确认定义</button></div>
      </div>}
    </section></div>}
  </section>;
}
