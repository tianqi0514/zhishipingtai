import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  BookOpenCheck,
  Calculator,
  CheckCircle2,
  ChevronRight,
  FileText,
  GitCompareArrows,
  LayoutDashboard,
  LoaderCircle,
  PenLine,
  Plus,
  RefreshCw,
  Save,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
} from 'lucide-react';
import { api, ApiError } from './api';
import { cleanEvidenceText } from './evidence';
import { sha256 } from './hash';
import { createClientId } from './ids';
import { createFrameDeltaBuffer } from './streaming';
import type { AgentEvent, AgentMessage, AlternativePlan, ComputationRun, DecisionGate, ExportJob, Fact, KnowledgeResult, KnowledgeSearchResponse, PlateNode, Project, ScenarioPackage, WritingAgentSession, WritingDocument } from './types/domain';

type Product = { id: string; name: string; code: string };
type Release = { id: string; version: number; status: string };
type User = { id: string; display_name: string; is_admin: boolean };
type Tab = 'overview' | 'facts' | 'reasoning' | 'plans' | 'writing' | 'review';

const tabs: Array<{ key: Tab; label: string; icon: typeof LayoutDashboard }> = [
  { key: 'overview', label: '任务概览', icon: LayoutDashboard },
  { key: 'facts', label: '数据与事实', icon: BookOpenCheck },
  { key: 'reasoning', label: '推演工作台', icon: GitCompareArrows },
  { key: 'plans', label: '方案比较', icon: Calculator },
  { key: 'writing', label: '妙笔文稿', icon: PenLine },
  { key: 'review', label: '审校发布', icon: ShieldCheck },
];

const MiaobiEditor = lazy(() => import('./editor/MiaobiEditor').then((module) => ({ default: module.MiaobiEditor })));

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string>('');
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>('overview');
  const [facts, setFacts] = useState<Fact[]>([]);
  const [plans, setPlans] = useState<AlternativePlan[]>([]);
  const [documents, setDocuments] = useState<WritingDocument[]>([]);
  const [computations, setComputations] = useState<ComputationRun[]>([]);
  const [gates, setGates] = useState<DecisionGate[]>([]);
  const [document, setDocument] = useState<WritingDocument | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [assistantTab, setAssistantTab] = useState<'assistant' | 'evidence' | 'calculation' | 'review'>('assistant');
  const [insertionRequest, setInsertionRequest] = useState<PlateNode | null>(null);

  const selected = projects.find((item) => item.id === projectId) || null;

  const loadProjects = async () => {
    const rows = await api<Project[]>('/writing/projects');
    setProjects(rows);
    const stored = sessionStorage.getItem('miaobi-project');
    const next = rows.some((row) => row.id === stored) ? stored! : rows[0]?.id || '';
    setProjectId(next);
  };

  const loadProjectDetails = async (id: string) => {
    const [detail, factRows, planRows, documentRows, computationRows, gateRows] = await Promise.all([
      api<Project>(`/writing/projects/${id}`),
      api<Fact[]>(`/writing/projects/${id}/facts`),
      api<AlternativePlan[]>(`/writing/projects/${id}/plans`),
      api<WritingDocument[]>(`/writing/projects/${id}/documents`),
      api<ComputationRun[]>(`/writing/projects/${id}/computations`),
      api<DecisionGate[]>(`/writing/projects/${id}/decision-gates`),
    ]);
    setProject(detail);
    setFacts(factRows);
    setPlans(planRows);
    setDocuments(documentRows);
    setComputations(computationRows);
    setGates(gateRows);
    setDocument(documentRows.length ? await api<WritingDocument>(`/writing/documents/${documentRows[0].id}`) : null);
  };

  useEffect(() => {
    Promise.all([api<User>('/auth/me'), loadProjects()])
      .then(([me]) => setUser(me))
      .catch((reason) => setError(reason instanceof Error ? reason.message : '妙笔加载失败'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!projectId) {
      setProject(null);
      return;
    }
    sessionStorage.setItem('miaobi-project', projectId);
    loadProjectDetails(projectId).catch((reason) => setError(reason instanceof Error ? reason.message : '方案任务加载失败'));
  }, [projectId]);

  const createDocument = async () => {
    if (!project) return;
    const created = await api<WritingDocument>('/writing/documents', {
      method: 'POST',
      body: { project_id: project.id, title: `${project.name}（初稿）`, content: [] },
    });
    setDocument(created);
    setDocuments([created]);
    setTab('writing');
  };

  if (loading) return <div className="full-state"><LoaderCircle className="spin" /><b>正在加载妙笔工作台</b></div>;
  if (error && !user) return <div className="full-state error"><AlertTriangle /><b>{error}</b><a href="/">返回传神智库</a></div>;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a href="/" className="brand" aria-label="返回传神智库"><span className="brand-mark">妙</span><span><b>妙笔</b><small>知识约束的推演式写作</small></span></a>
        <div className="project-switcher">
          <span>当前方案任务</span>
          <select value={projectId} onChange={(event) => setProjectId(event.target.value)} aria-label="选择方案任务">
            {!projects.length && <option value="">尚未创建</option>}
            {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          <button type="button" className="icon-button" title="刷新" onClick={() => void loadProjects()}><RefreshCw size={16} /></button>
        </div>
        <div className="top-actions"><span className="save-state"><Save size={15} />{dirty ? '有未保存修改' : '内容已保存'}</span><a className="secondary" href="/#configuration"><Settings2 size={16} />配置中心</a><span className="avatar">{user?.display_name?.slice(0, 1) || '用'}</span></div>
      </header>

      <aside className="sidebar">
        <button type="button" className="new-project" onClick={() => setCreateOpen(true)}><Plus size={17} />新建方案任务</button>
        <nav aria-label="妙笔主流程">
          {tabs.map(({ key, label, icon: Icon }) => (
            <button type="button" key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)} disabled={!projectId}>
              <Icon size={18} /><span>{label}</span>{key === 'facts' && facts.filter((item) => item.verification_status !== 'verified').length > 0 && <i>{facts.filter((item) => item.verification_status !== 'verified').length}</i>}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot"><a href="/">← 返回传神智库</a><span>Plate 53.3.11</span></div>
      </aside>

      <main className={`main ${tab === 'writing' ? 'writing-mode' : ''}`}>
        {!selected ? <Welcome onCreate={() => setCreateOpen(true)} /> : (
          <>
            {tab !== 'writing' && <PageHeader project={selected} tab={tab} />}
            {tab === 'overview' && <Overview project={project || selected} facts={facts} plans={plans} documents={documents} onContinue={(next) => setTab(next)} />}
            {tab === 'facts' && <Facts project={selected} facts={facts} onChanged={() => loadProjectDetails(selected.id)} onError={setError} />}
            {tab === 'reasoning' && <Reasoning project={selected} facts={facts} computations={computations} gates={gates} onChanged={() => loadProjectDetails(selected.id)} onError={setError} />}
            {tab === 'plans' && <Plans project={selected} rows={plans} onChanged={() => loadProjectDetails(selected.id)} onError={setError} />}
            {tab === 'writing' && (
              <div className="writing-layout">
                <section className="outline-pane"><b>文稿目录</b>{document ? <Outline content={document.current_version?.content || []} /> : <p>创建文稿后自动生成目录。</p>}<div className="missing-box"><AlertTriangle size={16} /><span>缺失项会在这里提示，不会由模型静默补齐。</span></div></section>
                <section className="document-pane">
                  {document ? <Suspense fallback={<div className="editor-shell editor-loading">正在加载完整文稿编辑器…</div>}><MiaobiEditor key={document.id} document={document} onDirtyChange={setDirty} onSaved={(saved) => setDocument(saved)} onRequestSource={setAssistantTab} insertionRequest={insertionRequest} onInserted={() => setInsertionRequest(null)} /></Suspense> : <EmptyAction title="还没有文稿" detail="从锁定的知识产品版本创建第一份方案初稿。" action="创建文稿" onClick={() => void createDocument()} />}
                </section>
                <aside className="assistant-pane">
                  <div className="assistant-tabs">
                    {([['assistant','妙笔助手'],['evidence','引用依据'],['calculation','计算与推演'],['review','审校问题']] as const).map(([key,label]) => <button type="button" key={key} className={assistantTab === key ? 'active' : ''} onClick={() => setAssistantTab(key)}>{label}</button>)}
                  </div>
                  <AssistantPanel tab={assistantTab} project={selected} document={document} facts={facts} plans={plans} computations={computations} onInsert={setInsertionRequest} onError={setError} />
                </aside>
              </div>
            )}
            {tab === 'review' && <Review project={selected} document={document} gates={gates} onChanged={() => loadProjectDetails(selected.id)} onError={setError} />}
          </>
        )}
      </main>
      {error && <div className="toast" role="alert">{error}<button type="button" onClick={() => setError('')}>×</button></div>}
      {createOpen && <CreateProjectDialog onClose={() => setCreateOpen(false)} onCreated={async (row) => { setCreateOpen(false); await loadProjects(); setProjectId(row.id); }} onError={setError} />}
    </div>
  );
}

function Welcome({ onCreate }: { onCreate: () => void }) {
  return <div className="welcome"><div className="welcome-icon"><Sparkles /></div><h1>把依据、计算和推演写进一份可信方案</h1><p>妙笔会锁定知识版本，每个事实、数值和结论都能回到原始依据。</p><button type="button" className="primary" onClick={onCreate}><Plus size={17} />创建第一个方案任务</button></div>;
}

function PageHeader({ project, tab }: { project: Project; tab: Tab }) {
  const current = tabs.find((item) => item.key === tab)!;
  return <div className="page-header"><div><span className="eyebrow">{project.name}</span><h1>{current.label}</h1></div><span className={`status ${project.status}`}>{statusLabel(project.status)}</span></div>;
}

function Overview({ project, facts, plans, documents, onContinue }: { project: Project; facts: Fact[]; plans: AlternativePlan[]; documents: WritingDocument[]; onContinue: (tab: Tab) => void }) {
  const verified = facts.filter((item) => item.verification_status === 'verified').length;
  const steps: Array<{ title: string; detail: string; done: boolean; target: Tab }> = [
    { title: '准备数据与事实', detail: `${facts.length} 条事实，${verified} 条已核验`, done: facts.length > 0 && verified === facts.length, target: 'facts' },
    { title: '规则与计算推演', detail: '形成等级、任务与资源缺口依据', done: plans.length > 0, target: 'reasoning' },
    { title: '比较备选方案', detail: `${plans.length} 套真实算法方案`, done: plans.length >= 2, target: 'plans' },
    { title: '撰写与审校', detail: `${documents.length} 份文稿`, done: documents.length > 0, target: 'writing' },
  ];
  return <div className="overview-grid"><section className="hero-card"><div><span className="eyebrow">当前进度</span><h2>{steps.find((item) => !item.done)?.title || '可以进入审校发布'}</h2><p>系统不会替您跳过缺失事实、口径冲突和人工确认。</p></div><button type="button" className="primary" onClick={() => onContinue(steps.find((item) => !item.done)?.target || 'review')}>继续处理<ChevronRight size={17} /></button></section><section className="step-list">{steps.map((step, index) => <button type="button" key={step.title} onClick={() => onContinue(step.target)}><span className={step.done ? 'step done' : 'step'}>{step.done ? <CheckCircle2 size={18} /> : index + 1}</span><span><b>{step.title}</b><small>{step.detail}</small></span><ChevronRight size={17} /></button>)}</section><section className="metrics"><Metric label="已核验事实" value={`${verified}/${facts.length}`} /><Metric label="备选方案" value={String(plans.length)} /><Metric label="文稿版本" value={String(documents.length)} /><Metric label="待确认" value={String(project.pending_gates || 0)} /></section></div>;
}

function Facts({ project, facts, onChanged, onError }: { project: Project; facts: Fact[]; onChanged: () => Promise<void>; onError: (message: string) => void }) {
  const [query, setQuery] = useState('');
  const [pendingOnly, setPendingOnly] = useState(false);
  const [decision, setDecision] = useState<{ fact: Fact; mode: 'confirm' | 'reject' | 'override' } | null>(null);
  const [reason, setReason] = useState('已核对当前业务材料');
  const [overrideValue, setOverrideValue] = useState('');
  const [submitting, setSubmitting] = useState(false);
  if (!facts.length) return <EmptyAction title="尚未形成项目事实" detail="接入灾情简报、预案、表格或数据库后，抽取结果会进入待核验事实清单。" action="前往知识资产" onClick={() => { window.location.href = '/#assets'; }} />;
  const visible = facts.filter((fact) => {
    if (pendingOnly && fact.verification_status === 'verified') return false;
    const text = `${fact.label} ${fact.fact_key} ${formatValue(fact.value)}`.toLowerCase();
    return text.includes(query.trim().toLowerCase());
  });
  const openDecision = (fact: Fact, mode: 'confirm' | 'reject' | 'override') => {
    setReason(mode === 'reject' ? '来源不足，暂不采用' : mode === 'override' ? '根据业务核验修正' : '已核对当前业务材料');
    setOverrideValue(formatValue(fact.value));
    setDecision({ fact, mode });
  };
  const submit = async () => {
    if (!decision || submitting || reason.trim().length < 2) return;
    setSubmitting(true);
    try {
      let newValue: Record<string, unknown> | undefined;
      if (decision.mode === 'override') {
        const originalNumber = decision.fact.value.number ?? decision.fact.value.value;
        const parsed = Number(overrideValue);
        newValue = typeof originalNumber === 'number' && Number.isFinite(parsed) ? { number: parsed } : { text: overrideValue.trim() };
      }
      await api(`/writing/projects/${project.id}/facts/${decision.fact.id}/confirm`, {
        method: 'POST', body: { decision: decision.mode, reason: reason.trim(), ...(newValue ? { new_value: newValue } : {}) },
      });
      setDecision(null);
      await onChanged();
    } catch (failure) { onError(failure instanceof Error ? failure.message : '事实处理失败'); }
    finally { setSubmitting(false); }
  };
  return <><div className="content-card"><div className="card-toolbar"><div className="search"><Search size={16} /><input placeholder="搜索事实" value={query} onChange={(event) => setQuery(event.target.value)} /></div><button type="button" className={pendingOnly ? 'primary compact' : 'secondary'} onClick={() => setPendingOnly((value) => !value)}>{pendingOnly ? '显示全部事实' : '只看待确认'}</button></div><div className="table-scroll"><table><thead><tr><th>事实</th><th>当前值</th><th>来源</th><th>版本</th><th>状态</th><th>操作</th></tr></thead><tbody>{visible.map((fact) => <tr key={fact.id}><td><b>{fact.label}</b></td><td>{formatValue(fact.value)} {fact.unit || ''}</td><td>{sourceLabel(fact.source_type)}</td><td>v{fact.version}</td><td><span className={`status ${fact.verification_status}`}>{fact.verification_status === 'verified' ? '已核验' : fact.verification_status === 'rejected' ? '已驳回' : '待确认'}</span></td><td><div className="row-actions">{fact.verification_status !== 'verified' && <><button type="button" onClick={() => openDecision(fact, 'confirm')}>确认</button><button type="button" onClick={() => openDecision(fact, 'reject')}>驳回</button></>}<button type="button" onClick={() => openDecision(fact, 'override')}>修正</button></div></td></tr>)}</tbody></table></div>{!visible.length && <div className="empty-table">没有符合当前条件的事实</div>}</div>{decision && <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) setDecision(null); }}><section className="dialog compact-dialog" role="dialog" aria-modal="true"><div className="dialog-head"><div><span className="eyebrow">业务核验</span><h2>{decision.mode === 'confirm' ? '确认事实' : decision.mode === 'reject' ? '驳回事实' : '修正事实'}</h2></div><button type="button" className="icon-button" disabled={submitting} onClick={() => setDecision(null)}>×</button></div><div className="fact-preview"><b>{decision.fact.label}</b><span>{formatValue(decision.fact.value)} {decision.fact.unit || ''}</span></div>{decision.mode === 'override' && <label>修正后的值<input value={overrideValue} onChange={(event) => setOverrideValue(event.target.value)} autoFocus /></label>}<label>处理理由<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={3} /></label><p className="field-help">修正会创建新事实版本，原值继续保留用于追溯；依赖该事实的正文块会被标记为需要更新。</p><div className="dialog-actions"><button type="button" className="secondary" disabled={submitting} onClick={() => setDecision(null)}>取消</button><button type="button" className="primary" disabled={submitting || reason.trim().length < 2 || (decision.mode === 'override' && !overrideValue.trim())} onClick={() => void submit()}>{submitting ? '处理中…' : '确认处理'}</button></div></section></div>}</>;
}

function Reasoning({ project, facts, computations, gates, onChanged, onError }: { project: Project; facts: Fact[]; computations: ComputationRun[]; gates: DecisionGate[]; onChanged: () => Promise<void>; onError: (message: string) => void }) {
  const [running, setRunning] = useState<'criteria' | 'reason' | 'compute' | 'confirm' | ''>('');
  const criteria = facts.filter((item) => item.fact_key.startsWith('criterion_'));
  const conclusion = facts.find((item) => item.fact_key === 'disaster_grade');
  const latestMetrics = new Map<string, ComputationRun>();
  computations.forEach((item) => {
    const key = item.result.output_fact?.fact_key;
    if (key && !latestMetrics.has(key)) latestMetrics.set(key, item);
  });
  const run = async (step: 'criteria' | 'reason' | 'compute') => {
    if (running) return;
    setRunning(step);
    try {
      const path = step === 'criteria' ? 'criteria/evaluate' : step === 'reason' ? 'reason' : 'computations/run-baseline';
      await api(`/writing/projects/${project.id}/${path}`, { method: 'POST', body: step === 'reason' ? { mode: 'preview' } : undefined });
      await onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : '规则推演失败'); }
    finally { setRunning(''); }
  };
  const confirmConclusion = async () => {
    if (!conclusion || running) return;
    setRunning('confirm');
    try {
      await api(`/writing/projects/${project.id}/facts/${conclusion.id}/confirm`, { method: 'POST', body: { decision: 'confirm', reason: '已核验判据与推演证据链' } });
      await onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : '结论确认失败'); }
    finally { setRunning(''); }
  };
  return <div className="reasoning-stack"><div className="two-columns"><section className="content-card"><span className="eyebrow">业务推演</span><h2>从已核验事实形成可解释结论</h2><div className="reason-flow"><span>确定性判据</span><ChevronRight /><span>语义规则推演</span><ChevronRight /><span>证据链</span><ChevronRight /><span>人工确认</span></div><div className="notice"><ShieldCheck /><div><b>模型不负责正式结论</b><p>数值条件先由确定性判据服务计算，再由规则推演引擎基于事实形成结论。</p></div></div><div className="reason-actions"><button type="button" className="secondary" disabled={!!running} onClick={() => void run('criteria')}>{running === 'criteria' ? '判据计算中…' : criteria.length ? '重新计算判据' : '计算等级判据'}</button><button type="button" className="primary" disabled={!!running || criteria.length < 2} onClick={() => void run('reason')}>{running === 'reason' ? '推演中…' : '预览推演结论'}</button></div></section><section className="content-card"><h3>执行结果</h3><div className="timeline"><p><i className={criteria.length >= 2 ? 'done' : ''} />确定性判据 {criteria.length >= 2 ? '已完成' : '待执行'}</p><p><i className={conclusion ? 'done' : ''} />规则推演 {conclusion ? '已完成' : '待执行'}</p><p><i className={conclusion?.verification_status === 'verified' ? 'done' : ''} />人工确认 {conclusion ? (conclusion.verification_status === 'verified' ? '已确认' : '待确认') : '待推演'}</p></div>{criteria.map((item) => <div className="reason-result" key={item.id}><b>{item.label}</b><span>{item.value.boolean ? '成立' : '不成立'}</span></div>)}{conclusion && <div className="reason-conclusion"><small>推演结论</small><b>{formatValue(conclusion.value)}</b><span>{conclusion.verification_status === 'verified' ? '已由业务人员确认，可作为方案依据' : '需要业务人员确认后才能作为正式方案依据'}</span>{conclusion.verification_status !== 'verified' && <button type="button" className="primary compact" disabled={!!running} onClick={() => void confirmConclusion()}>{running === 'confirm' ? '确认中…' : '确认该结论'}</button>}</div>}</section></div><section className="content-card computation-workbench"><div className="card-toolbar"><div><span className="eyebrow">确定性计算</span><h2>资源需求与缺口</h2></div><button type="button" className="primary" disabled={!!running} onClick={() => void run('compute')}>{running === 'compute' ? '计算中…' : latestMetrics.size ? '重新计算受控公式' : '计算资源缺口'}</button></div>{latestMetrics.size ? <div className="calculation-grid">{Array.from(latestMetrics.values()).map((item) => <article key={item.id}><small>{item.result.output_fact?.label || item.result.operation}</small><b>{item.result.value} {item.result.output_fact?.unit || ''}</b><span>{Object.values(item.result.dependencies || {}).join('、')} · 不可直接手改</span></article>)}</div> : <div className="empty-mini">确认搜救人员、床位和帐篷的需求与可用量后，可一次运行四项版本化公式。</div>}</section><section className="content-card"><div className="card-toolbar"><div><span className="eyebrow">人工确认闸门</span><h2>发布前业务决策</h2></div><span className="status">{gates.filter((item) => item.status !== 'confirmed').length} 项待确认</span></div><div className="gate-list">{gates.map((gate) => <div key={gate.id}><span className={gate.status === 'confirmed' ? 'step done' : 'step'}>{gate.status === 'confirmed' ? <CheckCircle2 size={16} /> : '!'}</span><b>{gate.name}</b><small>{gate.status === 'confirmed' ? '已确认' : '需要在审校发布前确认'}</small></div>)}</div></section></div>;
}

function Plans({ project, rows, onChanged, onError }: { project: Project; rows: AlternativePlan[]; onChanged: () => Promise<void>; onError: (message: string) => void }) {
  const [selecting, setSelecting] = useState('');
  const [generating, setGenerating] = useState(false);
  const generate = async () => {
    if (generating) return;
    setGenerating(true);
    try {
      await api(`/writing/projects/${project.id}/plans/generate`, { method: 'POST', body: { count: 3, inputs: {} } });
      await onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : '备选方案生成失败'); }
    finally { setGenerating(false); }
  };
  if (!rows.length) return <EmptyAction title="尚未生成备选方案" detail="先完成事实确认和资源计算，再根据任务中接入的路线网络，用三个不同优化目标真实求解。" action={generating ? '求解中…' : '生成三套方案'} onClick={() => void generate()} />;
  const select = async (plan: AlternativePlan) => {
    if (plan.status === 'selected' || selecting) return;
    setSelecting(plan.id);
    try {
      await api(`/writing/projects/${project.id}/plans/${plan.id}/select`, { method: 'POST', body: { reason: '业务人员在方案比较页确认' } });
      await onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : '选择方案失败'); }
    finally { setSelecting(''); }
  };
  const latest = ['speed','safety','balanced'].map((key) => rows.find((item) => item.plan_key === key)).filter(Boolean) as AlternativePlan[];
  return <div className="plans-page"><div className="plan-page-actions"><div><span className="eyebrow">算法方案比较</span><h2>同一组输入，不同优化目标</h2><p>三套方案由真实路线网络与资源约束计算，不是语言改写。</p></div><button type="button" className="secondary" disabled={generating} onClick={() => void generate()}>{generating ? '重新求解中…' : '重新生成'}</button></div><div className="plan-grid">{latest.map((plan) => <article className={plan.status === 'selected' ? 'plan-card selected' : 'plan-card'} key={plan.id}><span className="status">{plan.status === 'selected' ? '已选择' : '待比较'}</span><h2>{plan.name}</h2><p>{plan.result.route?.path?.join(' → ') || '暂无路线'}</p><dl><div><dt>预计耗时</dt><dd>{plan.result.route?.minutes ?? '—'} 分钟</dd></div><div><dt>路线风险</dt><dd>{plan.result.route?.risk ?? '—'}</dd></div><div><dt>未解决缺口</dt><dd>{plan.unresolved_gaps.length} 项</dd></div></dl><button type="button" disabled={plan.status === 'selected' || !!selecting} className={plan.status === 'selected' ? 'secondary' : 'primary'} onClick={() => void select(plan)}>{plan.status === 'selected' ? '当前方案' : selecting === plan.id ? '确认中…' : '选择方案'}</button></article>)}</div></div>;
}

function Review({ project, document, gates, onChanged, onError }: { project: Project; document: WritingDocument | null; gates: DecisionGate[]; onChanged: () => Promise<void>; onError: (message: string) => void }) {
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState(false);
  const [issues, setIssues] = useState<Array<{ code: string; message: string }>>([]);
  const [pendingGates, setPendingGates] = useState<DecisionGate[]>([]);
  const [confirming, setConfirming] = useState('');
  const [exporting, setExporting] = useState('');
  const [exports, setExports] = useState<ExportJob[]>([]);
  useEffect(() => {
    setIssues([]);
    setPendingGates([]);
    setChecked(false);
    if (document) api<ExportJob[]>(`/writing/documents/${document.id}/exports`).then(setExports).catch(() => setExports([]));
    else setExports([]);
  }, [document?.id]);
  const validate = async () => {
    if (!document) return;
    setChecking(true);
    try {
      const result = await api<{ issues: Array<{ code: string; message: string }>; pending_decision_gates: DecisionGate[] }>(`/writing/documents/${document.id}/validate`, { method: 'POST', body: { for_publish: true } });
      setIssues(result.issues || []);
      setPendingGates(result.pending_decision_gates || []);
      setChecked(true);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '审校失败'); }
    finally { setChecking(false); }
  };
  const confirmGate = async (gate: DecisionGate) => {
    if (confirming) return;
    setConfirming(gate.id);
    try {
      await api(`/writing/projects/${project.id}/decision-gates/${gate.id}/records`, { method: 'POST', body: { decision: 'confirm', reason: '业务人员在审校发布页确认' } });
      await onChanged();
      setPendingGates((current) => current.filter((item) => item.id !== gate.id));
    } catch (reason) { onError(reason instanceof Error ? reason.message : '确认节点处理失败'); }
    finally { setConfirming(''); }
  };
  const createExport = async (format: ExportJob['output_format']) => {
    if (!document || exporting) return;
    setExporting(format);
    try {
      const job = await api<ExportJob>(`/writing/documents/${document.id}/exports`, { method: 'POST', body: { output_format: format } });
      setExports((current) => [job, ...current]);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '导出失败'); }
    finally { setExporting(''); }
  };
  const unresolved = gates.filter((item) => item.required && item.status !== 'confirmed');
  return <div className="review-page"><div className="two-columns"><section className="content-card"><h2>发布前检查</h2>{document ? <div className="check-list"><p><CheckCircle2 />文稿已创建并具有不可变版本</p><p><AlertTriangle />{unresolved.length ? `仍有 ${unresolved.length} 个人工确认节点` : '人工确认节点已完成'}</p><p><AlertTriangle />可信块不得存在失效或未核验依据</p></div> : <p>请先创建文稿。</p>}<button type="button" className="primary" disabled={!document || checking} onClick={() => void validate()}>{checking ? '检查中…' : '执行审校'}</button>{issues.length > 0 && <ul className="issue-list">{issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul>}{pendingGates.length > 0 && <div className="gate-review-list">{pendingGates.map((gate) => <div key={gate.id}><span>{gate.name}</span><button type="button" disabled={!!confirming} onClick={() => void confirmGate(gate)}>{confirming === gate.id ? '确认中…' : '确认'}</button></div>)}</div>}{document && !checking && !checked && issues.length === 0 && pendingGates.length === 0 && <p className="field-help">点击“执行审校”核对正文来源和发布条件。</p>}{document && !checking && checked && issues.length === 0 && pendingGates.length === 0 && <p className="review-success"><CheckCircle2 />审校通过，可以生成正式文件。</p>}</section><section className="content-card"><h2>正式导出</h2><p>Word 与 PDF 只有在全部业务节点确认、可信块有效后才能生成；证据数据可另行导出。</p><div className="export-actions">{(['docx','pdf','json','xlsx','geojson'] as const).map((format) => <button type="button" key={format} className={format === 'docx' || format === 'pdf' ? 'primary' : 'secondary'} disabled={!document || !!exporting} onClick={() => void createExport(format)}>{exporting === format ? '生成中…' : format.toUpperCase()}</button>)}</div></section></div><section className="content-card export-history"><h2>导出记录</h2>{exports.length ? <div className="export-list">{exports.map((job) => <div key={job.id}><span className={`status ${job.status}`}>{job.status === 'succeeded' ? '已生成' : job.status === 'failed' ? '失败' : `${job.progress}%`}</span><b>{job.manifest?.filename || job.output_format.toUpperCase()}</b><small>{job.checksum ? `校验值 ${job.checksum.slice(0, 12)}…` : job.error_message || ''}</small>{job.status === 'succeeded' && <a className="secondary" href={`/api/v1/writing/exports/${job.id}/download`}>下载</a>}</div>)}</div> : <div className="empty-mini">完成审校后生成的文件会保留版本、模板和校验记录。</div>}</section></div>;
}

function AssistantPanel({ tab, project, document, facts, plans, computations, onInsert, onError }: { tab: string; project: Project; document: WritingDocument | null; facts: Fact[]; plans: AlternativePlan[]; computations: ComputationRun[]; onInsert: (node: PlateNode) => void; onError: (message: string) => void }) {
  if (tab === 'evidence') return <EvidencePanel project={project} document={document} onInsert={onInsert} onError={onError} />;
  if (tab === 'calculation') return <CalculationPanel project={project} document={document} facts={facts} plans={plans} computations={computations} onInsert={onInsert} onError={onError} />;
  if (tab === 'review') return <div className="assistant-content"><h3>审校问题</h3><div className="empty-mini">执行审校后按严重程度列出事实、引用、结构和表述问题。</div></div>;
  return <WritingAssistant project={project} document={document} onInsert={onInsert} onError={onError} />;
}

function CalculationPanel({ project, document, facts, plans, computations, onInsert, onError }: { project: Project; document: WritingDocument | null; facts: Fact[]; plans: AlternativePlan[]; computations: ComputationRun[]; onInsert: (node: PlateNode) => void; onError: (message: string) => void }) {
  const [inserting, setInserting] = useState('');
  const latest = new Map<string, ComputationRun>();
  computations.forEach((item) => {
    const key = item.result.output_fact?.fact_key;
    if (key && !latest.has(key)) latest.set(key, item);
  });
  const insertMetric = async (run: ComputationRun) => {
    if (!document || inserting) return;
    setInserting(run.id);
    const label = run.result.output_fact?.label || '确定性测算';
    const unit = run.result.output_fact?.unit || '';
    const block: PlateNode = { id: createClientId(), type: 'computed_metric', formula: run.result.operation, freshness_status: 'current', children: [{ text: `${label}：${run.result.value}${unit}` }] };
    try {
      await api(`/writing/documents/${document.id}/bindings`, { method: 'POST', body: { block_id: block.id, block_type: block.type, source_type: 'computation', source_id: run.id, computation_run_id: run.id, evidence_ids: run.input_fact_ids, content_hash: await sha256(block), block_content: block, verification_status: 'verified', freshness_status: 'current', metadata: { formula: run.result.operation, dependencies: run.result.dependencies || {}, project_id: project.id } } });
      onInsert(block);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '插入测算结果失败'); }
    finally { setInserting(''); }
  };
  const insertPlan = async (plan: AlternativePlan) => {
    if (!document || inserting) return;
    setInserting(plan.id);
    const route = plan.result.route;
    const block: PlateNode = { id: createClientId(), type: 'alternative_plan', freshness_status: 'current', children: [{ text: `${plan.name}：${route?.path?.join(' → ') || '无路线'}，预计 ${route?.minutes ?? '—'} 分钟，路线风险 ${route?.risk ?? '—'}。` }] };
    try {
      await api(`/writing/documents/${document.id}/bindings`, { method: 'POST', body: { block_id: block.id, block_type: block.type, source_type: 'mcp_tool', source_id: plan.id, evidence_ids: [], content_hash: await sha256(block), block_content: block, verification_status: plan.status === 'selected' ? 'verified' : 'unverified', freshness_status: 'current', metadata: { plan_key: plan.plan_key, project_id: project.id } } });
      onInsert(block);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '插入备选方案失败'); }
    finally { setInserting(''); }
  };
  const insertInference = async (fact: Fact) => {
    if (!document || inserting || fact.verification_status !== 'verified' || fact.freshness_status !== 'current') return;
    setInserting(fact.id);
    const block: PlateNode = { id: createClientId(), type: 'inference_conclusion', freshness_status: 'current', children: [{ text: `${fact.label}：${formatValue(fact.value)}` }] };
    const evidence = Array.isArray(fact.source_locator?.evidence) ? fact.source_locator.evidence : [];
    const evidenceIds = evidence.map((item) => String((item as Record<string, unknown>).source_fact_id || '')).filter(Boolean);
    try {
      await api(`/writing/documents/${document.id}/bindings`, { method: 'POST', body: { block_id: block.id, block_type: block.type, source_type: 'semantica_inference', source_id: fact.source_id, source_version: fact.source_version, fact_id: fact.id, evidence_ids: evidenceIds, content_hash: await sha256(block), block_content: block, verification_status: 'verified', freshness_status: 'current', metadata: { rule_id: fact.source_locator?.rule_id, reasoning_run_id: fact.source_id, project_id: project.id } } });
      onInsert(block);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '插入推演结论失败'); }
    finally { setInserting(''); }
  };
  const selected = plans.find((item) => item.status === 'selected');
  const inferences = facts.filter((item) => item.fact_type === 'semantica_inference' && item.verification_status === 'verified' && item.freshness_status === 'current');
  return <div className="assistant-content"><h3>计算与推演</h3><p>已核验事实 {facts.filter((item) => item.verification_status === 'verified').length} 条；只有真实运行且已确认的结果可以插入正文。</p>{inferences.length > 0 && <div className="calculation-list inference-list">{inferences.map((fact) => <article key={fact.id}><div><b>{fact.label}</b><strong>{formatValue(fact.value)}</strong></div><small>规则推演 · 已人工确认 · 可追溯前提</small><button type="button" disabled={!document || !!inserting} onClick={() => void insertInference(fact)}>{inserting === fact.id ? '插入中…' : '插入推演块'}</button></article>)}</div>}<div className="calculation-list">{Array.from(latest.values()).map((run) => <article key={run.id}><div><b>{run.result.output_fact?.label || run.result.operation}</b><strong>{run.result.value} {run.result.output_fact?.unit || ''}</strong></div><small>{Object.entries(run.result.dependencies || {}).map(([name, key]) => `${name}←${key}`).join('；')}</small><button type="button" disabled={!document || !!inserting} onClick={() => void insertMetric(run)}>{inserting === run.id ? '插入中…' : '插入测算块'}</button></article>)}</div>{selected && <div className="selected-plan-mini"><span className="eyebrow">已选方案</span><b>{selected.name}</b><p>{selected.result.route?.path?.join(' → ')}</p><button type="button" disabled={!document || !!inserting} onClick={() => void insertPlan(selected)}>{inserting === selected.id ? '插入中…' : '插入方案块'}</button></div>}{!inferences.length && !latest.size && <div className="empty-mini">先在“推演工作台”运行并确认规则推演或确定性计算，再将结果作为可信块插入正文。</div>}</div>;
}

function WritingAssistant({ project, document, onInsert, onError }: { project: Project; document: WritingDocument | null; onInsert: (node: PlateNode) => void; onError: (message: string) => void }) {
  const [session, setSession] = useState<WritingAgentSession | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [input, setInput] = useState('请根据当前场景包和已核验事实，为我生成方案目录草稿。');
  const [running, setRunning] = useState(false);
  const [stage, setStage] = useState('等待指令');
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const deltaBatchRef = useRef<ReturnType<typeof createFrameDeltaBuffer> | null>(null);
  if (!deltaBatchRef.current) {
    deltaBatchRef.current = createFrameDeltaBuffer((delta) => {
      setMessages((current) => current.map((item) => item.id === 'streaming' ? { ...item, content: item.content + delta } : item));
    });
  }

  const flushAnswerDelta = () => deltaBatchRef.current?.flush();

  const applySession = (value: WritingAgentSession) => {
    setSession(value);
    setMessages(value.conversation?.messages || []);
    const restoredEvents = (value.conversation?.events || []).filter((item) => visibleAgentEvent(item.event_type));
    setEvents(restoredEvents.slice(-30));
    const lastStarted = [...restoredEvents].reverse().find((item) => item.event_type === 'turn_started');
    const lastTerminal = [...restoredEvents].reverse().find((item) => ['turn_completed','turn_failed','turn_cancelled'].includes(item.event_type));
    const start = eventTime(lastStarted);
    const terminalDuration = Number(lastTerminal?.payload?.duration_ms || 0);
    setStartedAt(null);
    setElapsedSeconds(terminalDuration > 0 ? Math.max(1, Math.round(terminalDuration / 1000)) : start ? Math.max(0, Math.round((Date.now() - start) / 1000)) : 0);
    setStage(lastTerminal ? (lastTerminal.event_type === 'turn_completed' ? '已完成' : lastTerminal.event_type === 'turn_cancelled' ? '已停止' : '生成失败') : '等待指令');
  };

  const createSession = async (startNew = false) => {
    const value = await api<WritingAgentSession>(`/writing/projects/${project.id}/agent-sessions`, {
      method: 'POST', body: { document_id: document?.id || null, start_new: startNew },
    });
    applySession(value);
  };

  useEffect(() => {
    abortRef.current?.abort();
    flushAnswerDelta();
    setSession(null);
    setMessages([]);
    setEvents([]);
    void createSession().catch((reason) => onError(reason instanceof Error ? reason.message : '妙笔助手会话加载失败'));
    return () => {
      abortRef.current?.abort();
      deltaBatchRef.current?.dispose();
    };
  }, [project.id, document?.id]);

  useEffect(() => {
    if (!running || startedAt === null) return;
    const update = () => setElapsedSeconds(Math.max(0, Math.round((Date.now() - startedAt) / 1000)));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [running, startedAt]);

  const send = async () => {
    if (!session || !input.trim() || running) return;
    const prompt = input.trim();
    setInput('');
    setRunning(true);
    setStage('正在连接知识 Agent');
    setEvents([]);
    deltaBatchRef.current?.dispose();
    setStartedAt(Date.now());
    setElapsedSeconds(0);
    setMessages((current) => [...current, { id: createClientId(), role: 'user', content: prompt, status: 'completed' }, { id: 'streaming', role: 'assistant', content: '', status: 'generating' }]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await fetch(`/api/v1/writing/agent-sessions/${session.id}/messages`, {
        method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: prompt }),
      });
      if (!response.ok || !response.body) throw new Error(String((await response.json().catch(() => ({})))?.detail || '妙笔助手请求失败'));
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let finished = false;
      while (!finished) {
        const read = await reader.read();
        finished = read.done;
        buffer += decoder.decode(read.value || new Uint8Array(), { stream: !finished });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';
        for (const frame of frames) {
          let event = 'message';
          const data: string[] = [];
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) event = line.slice(6).trim();
            if (line.startsWith('data:')) data.push(line.slice(5).trim());
          }
          const payload = JSON.parse(data.join('\n') || '{}') as Record<string, unknown>;
          if (visibleAgentEvent(event)) {
            setEvents((current) => [...current, { event_type: event, payload }].slice(-30));
          }
          if (event === 'turn_started') setStartedAt(eventTime({ event_type: event, payload }) || Date.now());
          if (event === 'answer_delta') {
            deltaBatchRef.current?.enqueue(String(payload.text || ''));
            setStage('正在生成修订建议');
          } else if (event === 'tool_started' || event === 'writing_stage_started') {
            setStage(writingStage(String(payload.name || '')));
          } else if (event === 'turn_completed') {
            flushAnswerDelta();
            setStage('已完成');
            setStartedAt(null);
            if (payload.duration_ms) setElapsedSeconds(Math.max(1, Math.round(Number(payload.duration_ms) / 1000)));
            setMessages((current) => current.map((item) => item.id === 'streaming' ? { ...item, status: 'completed' } : item));
          } else if (event === 'turn_failed') {
            flushAnswerDelta();
            setStage('生成失败');
            setStartedAt(null);
            setMessages((current) => current.map((item) => item.id === 'streaming' ? { ...item, status: 'failed', error_message: String(payload.message || payload.reason || '生成失败') } : item));
          } else if (event === 'turn_cancelled') {
            flushAnswerDelta();
            setStage('已停止');
            setStartedAt(null);
            setMessages((current) => current.map((item) => item.id === 'streaming' ? { ...item, status: 'cancelled' } : item));
          }
        }
      }
      const restored = await api<WritingAgentSession>(`/writing/agent-sessions/${session.id}`);
      applySession(restored);
    } catch (reason) {
      if (!controller.signal.aborted) onError(reason instanceof Error ? reason.message : '妙笔助手生成失败');
    } finally {
      abortRef.current = null;
      setRunning(false);
    }
  };

  const stop = async () => {
    if (!session || !running) return;
    await api(`/writing/agent-sessions/${session.id}/cancel`, { method: 'POST' }).catch(() => undefined);
    abortRef.current?.abort();
    flushAnswerDelta();
    setRunning(false);
    setStage('已停止');
    setStartedAt(null);
  };

  const latest = [...messages].reverse().find((item) => item.role === 'assistant' && item.content.trim());
  const insertSuggestion = () => {
    if (!latest) return;
    const suggestionId = createClientId();
    onInsert({
      id: createClientId(),
      type: 'p',
      suggestion: { id: suggestionId, type: 'insert', userId: 'miaobi-agent', createdAt: Date.now() },
      children: [{ text: latest.content }],
    });
  };

  return <div className="assistant-content writing-agent"><div className="assistant-title"><h3>妙笔助手</h3><div className="agent-title-actions"><span className={running ? 'agent-status running' : 'agent-status'}>{stage}{(running || elapsedSeconds > 0) ? ` · ${elapsedSeconds} 秒` : ''}</span><button type="button" className="text-button" disabled={running} onClick={() => void createSession(true).catch((reason) => onError(reason instanceof Error ? reason.message : '新建对话失败'))}>新对话</button></div></div>{events.length > 0 && <div className="agent-timeline" aria-label="Agent 可核验执行过程">{events.map((item, index) => <div key={`${item.sequence || index}-${item.event_type}`}><i className={['turn_completed','tool_finished','retrieval_ranked'].includes(item.event_type) ? 'done' : ''} /><span>{agentEventLabel(item)}</span><small>{eventDuration(item)}</small></div>)}</div>}<div className="agent-messages">{messages.filter((item) => item.content || item.status !== 'completed').map((item, index) => <article key={`${item.id}-${index}`} className={item.role}><b>{item.role === 'user' ? '我' : '妙笔'}</b><p>{item.content || (item.status === 'generating' ? '正在组织内容…' : item.error_message || '未生成内容')}</p>{item.status !== 'completed' && <small>{item.status === 'generating' ? '生成中' : item.status === 'cancelled' ? '已停止' : '失败'}</small>}</article>)}{!messages.length && <div className="empty-mini">助手将调用真实知识、推演和计算工具，输出只作为待确认的修订建议。</div>}</div>{latest && !running && <button type="button" className="insert-suggestion" onClick={insertSuggestion}>插入为修订建议</button>}<div className="agent-input"><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder="说明希望撰写或修改的内容" /><button type="button" disabled={!session || (!running && !input.trim())} onClick={() => running ? void stop() : void send()}>{running ? <Square size={16} /> : <Send size={16} />}{running ? '停止' : '发送'}</button></div></div>;
}

function visibleAgentEvent(event: string) {
  return ['turn_started','step_started','retrieval_started','tool_started','tool_finished','retrieval_ranked','citation','warning','turn_completed','turn_failed','turn_cancelled'].includes(event);
}

function eventTime(event?: AgentEvent) {
  const raw = event?.payload?.occurred_at || event?.created_at;
  const parsed = raw ? Date.parse(String(raw)) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function eventDuration(event: AgentEvent) {
  const duration = Number(event.payload.duration_ms || 0);
  return duration > 0 ? `${(duration / 1000).toFixed(duration >= 1000 ? 1 : 2)} 秒` : '';
}

function agentEventLabel(event: AgentEvent) {
  const tool = String(event.payload.name || event.payload.tool || '');
  if (event.event_type === 'tool_started') return writingStage(tool);
  if (event.event_type === 'tool_finished') return `${writingStage(tool).replace('正在', '')}完成`;
  return ({ turn_started: '开始处理当前请求', step_started: '开始新的处理步骤', retrieval_started: '开始检索知识', retrieval_ranked: '完成召回排序', citation: '绑定一条来源依据', warning: String(event.payload.message || '执行过程出现降级'), turn_completed: '本轮处理完成', turn_failed: '本轮处理失败', turn_cancelled: '本轮已停止' } as Record<string,string>)[event.event_type] || '执行中';
}

function writingStage(tool: string) {
  return ({ writing_get_project_context: '正在读取任务资料', writing_get_document_outline: '正在检查文稿目录', writing_create_outline_draft: '正在生成目录草稿', writing_generate_section_draft: '正在组织章节依据', writing_bind_evidence: '正在绑定来源', writing_validate_document: '正在检查文稿', writing_get_stale_blocks: '正在检查过期内容', writing_recompute_impacts: '正在分析变更影响', writing_compare_alternative_plans: '正在比较备选方案', writing_prepare_export: '正在准备导出', knowledge_search: '正在检索知识', knowledge_reason: '正在执行规则推演', structured_execute_query: '正在查询业务数据' } as Record<string,string>)[tool] || '正在执行知识工具';
}

function EvidencePanel({ project, document, onInsert, onError }: { project: Project; document: WritingDocument | null; onInsert: (node: PlateNode) => void; onError: (message: string) => void }) {
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [result, setResult] = useState<KnowledgeSearchResponse | null>(null);
  const [inserting, setInserting] = useState('');

  const search = async () => {
    if (!query.trim() || searching) return;
    setSearching(true);
    try {
      setResult(await api<KnowledgeSearchResponse>(`/writing/projects/${project.id}/knowledge/search`, {
        method: 'POST',
        body: { query: query.trim(), top_k: 8, use_keyword: true, use_vector: true, use_graph: true, use_reranker: false },
      }));
    } catch (reason) { onError(reason instanceof Error ? reason.message : '知识检索失败'); }
    finally { setSearching(false); }
  };

  const insert = async (item: KnowledgeResult) => {
    if (!document || !result || inserting) return;
    setInserting(item.chunk_id);
    const evidenceText = cleanEvidenceText(item.snippet || item.text || '');
    const block: PlateNode = {
      id: createClientId(),
      type: 'knowledge_citation',
      source_title: item.title,
      source_locator: { page_number: item.page_number, structural_path: item.structural_path },
      freshness_status: 'current',
      children: [{ text: `${item.title}${item.page_number ? `（第 ${item.page_number} 页）` : ''}：${evidenceText}` }],
    };
    try {
      await api(`/writing/documents/${document.id}/bindings`, {
        method: 'POST',
        body: {
          block_id: block.id,
          block_type: block.type,
          source_type: 'policy_document',
          source_id: item.document_id,
          source_version: item.version_id,
          knowledge_product_release_id: project.knowledge_product_release_id,
          chunk_id: item.chunk_id,
          query_run_id: result.query_id,
          content_hash: await sha256(block),
          block_content: block,
          verification_status: 'verified',
          freshness_status: 'current',
          metadata: { rank: item.rank, channels: item.channels, fused_score: item.fused_score },
        },
      });
      onInsert(block);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '插入引用失败'); }
    finally { setInserting(''); }
  };

  return <div className="assistant-content"><h3>引用依据</h3><p>检索范围固定为当前方案任务锁定的知识产品版本。</p><div className="evidence-search"><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void search(); }} placeholder="检索本章所需依据" /><button type="button" disabled={!query.trim() || searching} onClick={() => void search()}>{searching ? '检索中…' : '检索'}</button></div>{result?.warnings?.map((warning) => <div className="warning-mini" key={warning}>{warning}</div>)}<div className="evidence-list">{result?.items.map((item) => <article key={item.chunk_id}><div><span className="rank">{item.rank}</span><b>{item.title}</b></div><p>{cleanEvidenceText(item.snippet || item.text || '无摘要')}</p><small>{item.page_number ? `第 ${item.page_number} 页 · ` : ''}{item.channels.join(' / ')} · 融合分 {Number(item.fused_score || 0).toFixed(4)}</small><button type="button" disabled={!document || !!inserting} onClick={() => void insert(item)}>{!document ? '请先创建文稿' : inserting === item.chunk_id ? '插入中…' : '插入正文'}</button></article>)}{result && !result.items.length && <div className="empty-mini">当前锁定版本中没有检索到依据。</div>}</div></div>;
}

function Outline({ content }: { content: Array<Record<string, unknown>> }) {
  const headings = content.filter((node) => ['h1','h2','h3'].includes(String(node.type))).map((node) => ({ id: String(node.id || ''), level: String(node.type), text: Array.isArray(node.children) ? (node.children as Array<Record<string, unknown>>).map((child) => String(child.text || '')).join('') : '' }));
  return <ol className="outline-list">{headings.map((item, index) => <li key={item.id || index} className={item.level}>{item.text || '未命名标题'}</li>)}</ol>;
}

function Metric({ label, value }: { label: string; value: string }) { return <div><span>{label}</span><b>{value}</b></div>; }

function EmptyAction({ title, detail, action, onClick }: { title: string; detail: string; action?: string; onClick?: () => void }) { return <div className="empty-action"><FileText /><h2>{title}</h2><p>{detail}</p>{action && onClick && <button type="button" className="primary" onClick={onClick}>{action}</button>}</div>; }

function CreateProjectDialog({ onClose, onCreated, onError }: { onClose: () => void; onCreated: (project: Project) => void; onError: (message: string) => void }) {
  const [packages, setPackages] = useState<ScenarioPackage[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [releases, setReleases] = useState<Release[]>([]);
  const [form, setForm] = useState({ name: '', code: '', scenario_version_id: '', product_id: '', release_id: '' });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    Promise.all([api<ScenarioPackage[]>('/writing/scenario-packages'), api<Product[]>('/knowledge-products')])
      .then(async ([scenarioRows, productRows]) => {
        const detailed = await Promise.all(scenarioRows.map((item) => api<ScenarioPackage>(`/writing/scenario-packages/${item.id}`)));
        setPackages(detailed);
        setProducts(productRows);
        const firstVersion = detailed.find((item) => item.current_version_id)?.current_version_id || '';
        const firstProduct = productRows[0]?.id || '';
        setForm((current) => ({ ...current, scenario_version_id: firstVersion, product_id: firstProduct }));
        if (firstProduct) setReleases(await api<Release[]>(`/knowledge-products/${firstProduct}/releases`));
      }).catch((reason) => onError(reason instanceof Error ? reason.message : '创建表单加载失败'));
  }, []);

  const selectedScenario = useMemo(() => packages.find((item) => item.current_version_id === form.scenario_version_id), [packages, form.scenario_version_id]);

  const selectProduct = async (id: string) => {
    setForm((current) => ({ ...current, product_id: id, release_id: '' }));
    setReleases(await api<Release[]>(`/knowledge-products/${id}/releases`));
  };

  const submit = async () => {
    if (!form.name.trim() || !form.code.trim() || !form.scenario_version_id || !form.release_id) return onError('请完整填写任务名称、编码、场景和知识产品版本');
    setSubmitting(true);
    try {
      const project = await api<Project>('/writing/projects', { method: 'POST', body: { code: form.code, name: form.name, scenario_package_version_id: form.scenario_version_id, knowledge_product_release_id: form.release_id } });
      onCreated(project);
    } catch (reason) {
      onError(reason instanceof ApiError ? reason.message : '创建方案任务失败');
    } finally { setSubmitting(false); }
  };

  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="create-title"><div className="dialog-head"><div><span className="eyebrow">第一步</span><h2 id="create-title">创建方案任务</h2></div><button type="button" className="icon-button" onClick={onClose}>×</button></div><label>任务名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：积石山县地震应急处置方案" autoFocus /></label><label>任务编码<input value={form.code} onChange={(event) => setForm({ ...form, code: event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '-') })} placeholder="jishishan-earthquake" /></label><label>业务场景<select value={form.scenario_version_id} onChange={(event) => setForm({ ...form, scenario_version_id: event.target.value })}>{packages.filter((item) => item.current_version_id).map((item) => <option key={item.id} value={item.current_version_id}>{item.name}{item.code !== 'earthquake-response-plan' ? '（模板待业务确认）' : ''}</option>)}</select></label>{selectedScenario && <p className="field-help">{selectedScenario.description || '场景包锁定输入、规则、公式、工具和输出结构。'}</p>}<label>知识产品<select value={form.product_id} onChange={(event) => void selectProduct(event.target.value)}>{products.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>知识产品版本<select value={form.release_id} onChange={(event) => setForm({ ...form, release_id: event.target.value })}><option value="">请选择已发布版本</option>{releases.filter((item) => item.status === 'published').map((item) => <option key={item.id} value={item.id}>Release {item.version}</option>)}</select></label><div className="dialog-actions"><button type="button" className="secondary" onClick={onClose}>取消</button><button type="button" className="primary" disabled={submitting} onClick={() => void submit()}>{submitting ? '创建中…' : '创建并进入任务'}</button></div></section></div>;
}

function statusLabel(value: string) { return ({ draft: '准备中', preparing: '数据准备', ready: '可推演', reasoning: '推演中', writing: '撰写中', reviewing: '审校中', published: '已发布' } as Record<string,string>)[value] || value; }
function sourceLabel(value: string) { return ({ official_brief: '官方简报', policy_document: '预案原文', database_query: '实时数据库', computation: '确定性测算', semantica_inference: '规则推演', model_extraction: '模型抽取', manual_input: '人工补充', manual_override: '人工覆盖' } as Record<string,string>)[value] || value; }
function formatValue(value: Record<string, unknown>) {
  if (typeof value.boolean === 'boolean') return value.boolean ? '成立' : '不成立';
  return String(value.number ?? value.text ?? value.value ?? '—');
}
