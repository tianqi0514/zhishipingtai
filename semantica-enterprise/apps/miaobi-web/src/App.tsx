import { useEffect, useMemo, useRef, useState } from 'react';
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
import { MiaobiEditor } from './editor/MiaobiEditor';
import type { AgentMessage, AlternativePlan, Fact, KnowledgeResult, KnowledgeSearchResponse, PlateNode, Project, ScenarioPackage, WritingAgentSession, WritingDocument } from './types/domain';

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

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string>('');
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>('overview');
  const [facts, setFacts] = useState<Fact[]>([]);
  const [plans, setPlans] = useState<AlternativePlan[]>([]);
  const [documents, setDocuments] = useState<WritingDocument[]>([]);
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
    const [detail, factRows, planRows, documentRows] = await Promise.all([
      api<Project>(`/writing/projects/${id}`),
      api<Fact[]>(`/writing/projects/${id}/facts`),
      api<AlternativePlan[]>(`/writing/projects/${id}/plans`),
      api<WritingDocument[]>(`/writing/projects/${id}/documents`),
    ]);
    setProject(detail);
    setFacts(factRows);
    setPlans(planRows);
    setDocuments(documentRows);
    setDocument(documentRows.length ? await api<WritingDocument>(`/writing/documents/${documentRows[0].id}`) : null);
  };

  useEffect(() => {
    Promise.all([api<User>('/me'), loadProjects()])
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
            {tab === 'facts' && <Facts facts={facts} />}
            {tab === 'reasoning' && <Reasoning project={selected} facts={facts} onChanged={() => loadProjectDetails(selected.id)} onError={setError} />}
            {tab === 'plans' && <Plans projectId={selected.id} rows={plans} onChanged={setPlans} onError={setError} />}
            {tab === 'writing' && (
              <div className="writing-layout">
                <section className="outline-pane"><b>文稿目录</b>{document ? <Outline content={document.current_version?.content || []} /> : <p>创建文稿后自动生成目录。</p>}<div className="missing-box"><AlertTriangle size={16} /><span>缺失项会在这里提示，不会由模型静默补齐。</span></div></section>
                <section className="document-pane">
                  {document ? <MiaobiEditor key={document.id} document={document} onDirtyChange={setDirty} onSaved={(saved) => setDocument(saved)} insertionRequest={insertionRequest} onInserted={() => setInsertionRequest(null)} /> : <EmptyAction title="还没有文稿" detail="从锁定的知识产品版本创建第一份方案初稿。" action="创建文稿" onClick={() => void createDocument()} />}
                </section>
                <aside className="assistant-pane">
                  <div className="assistant-tabs">
                    {([['assistant','妙笔助手'],['evidence','引用依据'],['calculation','计算与推演'],['review','审校问题']] as const).map(([key,label]) => <button type="button" key={key} className={assistantTab === key ? 'active' : ''} onClick={() => setAssistantTab(key)}>{label}</button>)}
                  </div>
                  <AssistantPanel tab={assistantTab} project={selected} document={document} facts={facts} plans={plans} onInsert={setInsertionRequest} onError={setError} />
                </aside>
              </div>
            )}
            {tab === 'review' && <Review document={document} onError={setError} />}
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

function Facts({ facts }: { facts: Fact[] }) {
  const [query, setQuery] = useState('');
  const [pendingOnly, setPendingOnly] = useState(false);
  if (!facts.length) return <EmptyAction title="尚未形成项目事实" detail="接入灾情简报、预案、表格或数据库后，抽取结果会进入待核验事实清单。" action="前往知识资产" onClick={() => { window.location.href = '/#assets'; }} />;
  const visible = facts.filter((fact) => {
    if (pendingOnly && fact.verification_status === 'verified') return false;
    const text = `${fact.label} ${fact.fact_key} ${formatValue(fact.value)}`.toLowerCase();
    return text.includes(query.trim().toLowerCase());
  });
  return <div className="content-card"><div className="card-toolbar"><div className="search"><Search size={16} /><input placeholder="搜索事实" value={query} onChange={(event) => setQuery(event.target.value)} /></div><button type="button" className={pendingOnly ? 'primary compact' : 'secondary'} onClick={() => setPendingOnly((value) => !value)}>{pendingOnly ? '显示全部事实' : '只看待确认'}</button></div><table><thead><tr><th>事实</th><th>当前值</th><th>来源</th><th>状态</th></tr></thead><tbody>{visible.map((fact) => <tr key={fact.id}><td><b>{fact.label}</b><small>{fact.fact_key}</small></td><td>{formatValue(fact.value)} {fact.unit || ''}</td><td>{sourceLabel(fact.source_type)}</td><td><span className={`status ${fact.verification_status}`}>{fact.verification_status === 'verified' ? '已核验' : '待确认'}</span></td></tr>)}</tbody></table>{!visible.length && <div className="empty-table">没有符合当前条件的事实</div>}</div>;
}

function Reasoning({ project, facts, onChanged, onError }: { project: Project; facts: Fact[]; onChanged: () => Promise<void>; onError: (message: string) => void }) {
  const [running, setRunning] = useState<'criteria' | 'reason' | ''>('');
  const criteria = facts.filter((item) => item.fact_key.startsWith('criterion_'));
  const conclusion = facts.find((item) => item.fact_key === 'disaster_grade');
  const run = async (step: 'criteria' | 'reason') => {
    if (running) return;
    setRunning(step);
    try {
      await api(step === 'criteria' ? `/writing/projects/${project.id}/criteria/evaluate` : `/writing/projects/${project.id}/reason`, { method: 'POST', body: step === 'reason' ? { mode: 'preview' } : undefined });
      await onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : '规则推演失败'); }
    finally { setRunning(''); }
  };
  return <div className="two-columns"><section className="content-card"><span className="eyebrow">业务推演</span><h2>从已核验事实形成可解释结论</h2><div className="reason-flow"><span>确定性判据</span><ChevronRight /><span>语义规则推演</span><ChevronRight /><span>证据链</span><ChevronRight /><span>人工确认</span></div><div className="notice"><ShieldCheck /><div><b>模型不负责正式结论</b><p>数值条件先由确定性判据服务计算，再由规则推演引擎基于事实形成结论。</p></div></div><div className="reason-actions"><button type="button" className="secondary" disabled={!!running} onClick={() => void run('criteria')}>{running === 'criteria' ? '判据计算中…' : criteria.length ? '重新计算判据' : '计算等级判据'}</button><button type="button" className="primary" disabled={!!running || criteria.length < 2} onClick={() => void run('reason')}>{running === 'reason' ? '推演中…' : '预览推演结论'}</button></div></section><section className="content-card"><h3>执行结果</h3><div className="timeline"><p><i className={criteria.length >= 2 ? 'done' : ''} />确定性判据 {criteria.length >= 2 ? '已完成' : '待执行'}</p><p><i className={conclusion ? 'done' : ''} />规则推演 {conclusion ? '已完成' : '待执行'}</p><p><i className={conclusion?.verification_status === 'verified' ? 'done' : ''} />人工确认 {conclusion ? (conclusion.verification_status === 'verified' ? '已确认' : '待确认') : '待推演'}</p></div>{criteria.map((item) => <div className="reason-result" key={item.id}><b>{item.label}</b><span>{item.value.boolean ? '成立' : '不成立'}</span></div>)}{conclusion && <div className="reason-conclusion"><small>推演结论</small><b>{formatValue(conclusion.value)}</b><span>需要业务人员确认后才能作为正式方案依据</span></div>}</section></div>;
}

function Plans({ projectId, rows, onChanged, onError }: { projectId: string; rows: AlternativePlan[]; onChanged: (rows: AlternativePlan[]) => void; onError: (message: string) => void }) {
  const [selecting, setSelecting] = useState('');
  if (!rows.length) return <EmptyAction title="尚未生成备选方案" detail="先完成事实确认、规则推演和资源计算，再用不同优化目标真实求解。" />;
  const select = async (plan: AlternativePlan) => {
    if (plan.status === 'selected' || selecting) return;
    setSelecting(plan.id);
    try {
      await api(`/writing/projects/${projectId}/plans/${plan.id}/select`, { method: 'POST', body: { reason: '业务人员在方案比较页确认' } });
      onChanged(rows.map((item) => ({ ...item, status: item.id === plan.id ? 'selected' : 'candidate' })));
    } catch (reason) { onError(reason instanceof Error ? reason.message : '选择方案失败'); }
    finally { setSelecting(''); }
  };
  return <div className="plan-grid">{rows.map((plan) => <article className={plan.status === 'selected' ? 'plan-card selected' : 'plan-card'} key={plan.id}><span className="status">{plan.status === 'selected' ? '已选择' : '待比较'}</span><h2>{plan.name}</h2><p>{plan.result.route?.path?.join(' → ') || '暂无路线'}</p><dl><div><dt>预计耗时</dt><dd>{plan.result.route?.minutes ?? '—'} 分钟</dd></div><div><dt>路线风险</dt><dd>{plan.result.route?.risk ?? '—'}</dd></div><div><dt>未解决缺口</dt><dd>{plan.unresolved_gaps.length} 项</dd></div></dl><button type="button" disabled={plan.status === 'selected' || !!selecting} className={plan.status === 'selected' ? 'secondary' : 'primary'} onClick={() => void select(plan)}>{plan.status === 'selected' ? '当前方案' : selecting === plan.id ? '确认中…' : '选择方案'}</button></article>)}</div>;
}

function Review({ document, onError }: { document: WritingDocument | null; onError: (message: string) => void }) {
  const [checking, setChecking] = useState(false);
  const [issues, setIssues] = useState<Array<{ code: string; message: string }>>([]);
  const validate = async () => {
    if (!document) return;
    setChecking(true);
    try {
      const result = await api<{ issues: Array<{ code: string; message: string }> }>(`/writing/documents/${document.id}/validate`, { method: 'POST', body: { for_publish: true } });
      setIssues(result.issues || []);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '审校失败'); }
    finally { setChecking(false); }
  };
  return <div className="two-columns"><section className="content-card"><h2>发布前检查</h2>{document ? <div className="check-list"><p><CheckCircle2 />文稿已创建</p><p><AlertTriangle />必须完成全部人工确认节点</p><p><AlertTriangle />可信块不得存在失效或未核验依据</p></div> : <p>请先创建文稿。</p>}<button type="button" className="primary" disabled={!document || checking} onClick={() => void validate()}>{checking ? '检查中…' : '执行审校'}</button>{issues.length > 0 && <ul className="issue-list">{issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul>}{document && !checking && issues.length === 0 && <p className="field-help">点击“执行审校”后显示真实检查结果。</p>}</section><section className="content-card"><h2>正式导出</h2><p>导出服务接入后才会开放格式选择；当前页面不提供无效下载按钮。</p><span className="status draft">导出服务建设中</span></section></div>;
}

function AssistantPanel({ tab, project, document, facts, plans, onInsert, onError }: { tab: string; project: Project; document: WritingDocument | null; facts: Fact[]; plans: AlternativePlan[]; onInsert: (node: PlateNode) => void; onError: (message: string) => void }) {
  if (tab === 'evidence') return <EvidencePanel project={project} document={document} onInsert={onInsert} onError={onError} />;
  if (tab === 'calculation') return <div className="assistant-content"><h3>计算与推演</h3><p>已核验事实 {facts.filter((item) => item.verification_status === 'verified').length} 条，备选方案 {plans.length} 套。</p><div className="empty-mini">选择测算值或推演结论查看输入、公式、规则和证据。</div></div>;
  if (tab === 'review') return <div className="assistant-content"><h3>审校问题</h3><div className="empty-mini">执行审校后按严重程度列出事实、引用、结构和表述问题。</div></div>;
  return <WritingAssistant project={project} document={document} onInsert={onInsert} onError={onError} />;
}

function WritingAssistant({ project, document, onInsert, onError }: { project: Project; document: WritingDocument | null; onInsert: (node: PlateNode) => void; onError: (message: string) => void }) {
  const [session, setSession] = useState<WritingAgentSession | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [input, setInput] = useState('请根据当前场景包和已核验事实，为我生成方案目录草稿。');
  const [running, setRunning] = useState(false);
  const [stage, setStage] = useState('等待指令');
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    setSession(null);
    setMessages([]);
    api<WritingAgentSession>(`/writing/projects/${project.id}/agent-sessions`, {
      method: 'POST', body: { document_id: document?.id || null },
    }).then((value) => {
      setSession(value);
      setMessages(value.conversation?.messages || []);
    }).catch((reason) => onError(reason instanceof Error ? reason.message : '妙笔助手会话加载失败'));
    return () => abortRef.current?.abort();
  }, [project.id, document?.id]);

  const send = async () => {
    if (!session || !input.trim() || running) return;
    const prompt = input.trim();
    setInput('');
    setRunning(true);
    setStage('正在连接知识 Agent');
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: 'user', content: prompt, status: 'completed' }, { id: 'streaming', role: 'assistant', content: '', status: 'generating' }]);
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
          if (event === 'answer_delta') {
            setMessages((current) => current.map((item) => item.id === 'streaming' ? { ...item, content: item.content + String(payload.text || '') } : item));
            setStage('正在生成修订建议');
          } else if (event === 'tool_started' || event === 'writing_stage_started') {
            setStage(writingStage(String(payload.name || '')));
          } else if (event === 'turn_completed') {
            setStage('已完成');
            setMessages((current) => current.map((item) => item.id === 'streaming' ? { ...item, status: 'completed' } : item));
          } else if (event === 'turn_failed') {
            setStage('生成失败');
            setMessages((current) => current.map((item) => item.id === 'streaming' ? { ...item, status: 'failed', error_message: String(payload.message || payload.reason || '生成失败') } : item));
          } else if (event === 'turn_cancelled') {
            setStage('已停止');
            setMessages((current) => current.map((item) => item.id === 'streaming' ? { ...item, status: 'cancelled' } : item));
          }
        }
      }
      const restored = await api<WritingAgentSession>(`/writing/agent-sessions/${session.id}`);
      setSession(restored);
      setMessages(restored.conversation?.messages || []);
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
    setRunning(false);
    setStage('已停止');
  };

  const latest = [...messages].reverse().find((item) => item.role === 'assistant' && item.content.trim());
  const insertSuggestion = () => {
    if (!latest) return;
    onInsert({ id: crypto.randomUUID(), type: 'p', suggestion: true, suggestion_status: 'pending', children: [{ text: latest.content }] });
  };

  return <div className="assistant-content writing-agent"><div className="assistant-title"><h3>妙笔助手</h3><span className={running ? 'agent-status running' : 'agent-status'}>{stage}</span></div><div className="agent-messages">{messages.filter((item) => item.content || item.status !== 'completed').map((item, index) => <article key={`${item.id}-${index}`} className={item.role}><b>{item.role === 'user' ? '我' : '妙笔'}</b><p>{item.content || (item.status === 'generating' ? '正在组织内容…' : item.error_message || '未生成内容')}</p>{item.status !== 'completed' && <small>{item.status === 'generating' ? '生成中' : item.status === 'cancelled' ? '已停止' : '失败'}</small>}</article>)}{!messages.length && <div className="empty-mini">助手将调用真实知识、推演和计算工具，输出只作为待确认的修订建议。</div>}</div>{latest && !running && <button type="button" className="insert-suggestion" onClick={insertSuggestion}>插入为修订建议</button>}<div className="agent-input"><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder="说明希望撰写或修改的内容" /><button type="button" disabled={!session || (!running && !input.trim())} onClick={() => running ? void stop() : void send()}>{running ? <Square size={16} /> : <Send size={16} />}{running ? '停止' : '发送'}</button></div></div>;
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
    const block: PlateNode = {
      id: crypto.randomUUID(),
      type: 'knowledge_citation',
      source_title: item.title,
      source_locator: { page_number: item.page_number, structural_path: item.structural_path },
      freshness_status: 'current',
      children: [{ text: `${item.title}${item.page_number ? `（第 ${item.page_number} 页）` : ''}：${item.snippet || item.text || ''}` }],
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
          verification_status: 'verified',
          freshness_status: 'current',
          metadata: { rank: item.rank, channels: item.channels, fused_score: item.fused_score },
        },
      });
      onInsert(block);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '插入引用失败'); }
    finally { setInserting(''); }
  };

  return <div className="assistant-content"><h3>引用依据</h3><p>检索范围固定为当前方案任务锁定的知识产品版本。</p><div className="evidence-search"><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void search(); }} placeholder="检索本章所需依据" /><button type="button" disabled={!query.trim() || searching} onClick={() => void search()}>{searching ? '检索中…' : '检索'}</button></div>{result?.warnings?.map((warning) => <div className="warning-mini" key={warning}>{warning}</div>)}<div className="evidence-list">{result?.items.map((item) => <article key={item.chunk_id}><div><span className="rank">{item.rank}</span><b>{item.title}</b></div><p>{item.snippet || item.text || '无摘要'}</p><small>{item.page_number ? `第 ${item.page_number} 页 · ` : ''}{item.channels.join(' / ')} · 融合分 {Number(item.fused_score || 0).toFixed(4)}</small><button type="button" disabled={!document || !!inserting} onClick={() => void insert(item)}>{!document ? '请先创建文稿' : inserting === item.chunk_id ? '插入中…' : '插入正文'}</button></article>)}{result && !result.items.length && <div className="empty-mini">当前锁定版本中没有检索到依据。</div>}</div></div>;
}

async function sha256(value: unknown) {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest)).map((item) => item.toString(16).padStart(2, '0')).join('');
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
function formatValue(value: Record<string, unknown>) { return String(value.number ?? value.text ?? value.value ?? JSON.stringify(value)); }
