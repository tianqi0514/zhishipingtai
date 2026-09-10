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
  Trash2,
  Upload,
} from 'lucide-react';
import { api, ApiError } from './api';
import { cleanEvidenceText } from './evidence';
import { sha256 } from './hash';
import { createClientId } from './ids';
import { createFrameDeltaBuffer } from './streaming';
import type { AgentEvent, AgentMessage, AlternativePlan, ComputationRun, DecisionGate, ExportJob, Fact, KnowledgeContext, KnowledgeResult, KnowledgeSearchResponse, PlateNode, Project, ProjectMaterial, ProjectMaterialCandidate, ScenarioPackage, WritingAgentSession, WritingDocument, WritingGenerationRun, WritingInputChange } from './types/domain';
import type { MarkdownSuggestionInsertion } from './editor/MiaobiEditor';
import { ScenarioConfigDialog } from './components/ScenarioConfigDialog';
import { ChapterEvidencePanel, ParagraphEvidenceList } from './components/ChapterEvidence';

type Product = { id: string; name: string; code: string };
type Release = { id: string; version: number; status: string };
type User = { id: string; display_name: string; is_admin: boolean };
type Tab = 'task' | 'writing' | 'overview' | 'facts' | 'reasoning' | 'plans' | 'review';
type EditorInsertion = PlateNode | PlateNode[] | MarkdownSuggestionInsertion;

const tabs: Array<{ key: Tab; label: string; icon: typeof LayoutDashboard }> = [
  { key: 'task', label: '方案任务', icon: LayoutDashboard },
  { key: 'writing', label: '报告编辑', icon: PenLine },
];

const MiaobiEditor = lazy(() => import('./editor/MiaobiEditor').then((module) => ({ default: module.MiaobiEditor })));

export function generationStageLabel(run: WritingGenerationRun | null, hasDocument: boolean): string {
  if (!run) return hasDocument ? '报告已生成' : '等待开始';
  if (run.status === 'completed') return '报告已生成';
  if (run.status === 'cancelled') return '本次生成已停止，可重新开始';
  if (run.status === 'agent_failed' || run.status === 'quality_failed') return '上次生成失败，可重新生成';
  if (run.status === 'queued' || run.status === 'running') return '正在核验输入并运行推演工具箱';
  return '正在依据知识生成报告正文';
}

export function displaySourceVersion(value: unknown): string {
  const text = String(value ?? '').trim();
  if (!text) return '';
  if (/^v?\d+$/i.test(text)) return `V${text.replace(/^v/i, '')}`;
  if (/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(text)) return '固定知识版本';
  return text;
}

export function displayStructuralPath(value: unknown): string {
  const text = String(value ?? '').trim();
  const paragraph = text.match(/^paragraphs\/(\d+)$/i);
  if (paragraph) return `正文第 ${Number(paragraph[1]) + 1} 段`;
  const page = text.match(/^pages?\/(\d+)$/i);
  if (page) return `第 ${Number(page[1]) + 1} 页`;
  return text;
}

async function runAgentTextEdit(documentId: string, request: { action: string; originalText: string; blockId?: string; instruction?: string }) {
  const created = await api<{ id: string }>(`/writing/documents/${documentId}/agent-edits`, {
    method: 'POST',
    body: { action: request.action, original_text: request.originalText, block_id: request.blockId || null, instruction: request.instruction || '' },
  });
  const response = await fetch(`/api/v1/writing/agent-edits/${created.id}/agent`, { method: 'POST', credentials: 'same-origin' });
  if (!response.ok || !response.body) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(String(detail?.detail || '修改建议生成失败'));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let streamDone = false;
  while (!streamDone) {
    const chunk = await reader.read();
    streamDone = chunk.done;
    buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !streamDone });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';
    for (const frame of frames) {
      const event = frame.split('\n').find((line) => line.startsWith('event:'))?.slice(6).trim();
      const raw = frame.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
      const data = JSON.parse(raw || '{}') as Record<string, unknown>;
      if (event === 'turn_failed') throw new Error(String(data.message || data.reason || '修改建议生成失败'));
      if (event === 'turn_cancelled') throw new Error('修改建议已取消');
    }
  }
  const finished = await api<{ id: string; suggested_text: string }>(`/writing/agent-edits/${created.id}/finalize`, { method: 'POST' });
  return { id: finished.id, text: finished.suggested_text };
}

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string>('');
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>('task');
  const [facts, setFacts] = useState<Fact[]>([]);
  const [plans, setPlans] = useState<AlternativePlan[]>([]);
  const [documents, setDocuments] = useState<WritingDocument[]>([]);
  const [computations, setComputations] = useState<ComputationRun[]>([]);
  const [gates, setGates] = useState<DecisionGate[]>([]);
  const [knowledgeContext, setKnowledgeContext] = useState<KnowledgeContext | null>(null);
  const [materials, setMaterials] = useState<ProjectMaterial[]>([]);
  const [document, setDocument] = useState<WritingDocument | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [assistantTab, setAssistantTab] = useState<'assistant' | 'evidence' | 'calculation' | 'review'>('assistant');
  const [insertionRequest, setInsertionRequest] = useState<EditorInsertion | null>(null);
  const [selectedBinding, setSelectedBinding] = useState<Record<string, unknown> | null>(null);
  const [scenarioConfigOpen, setScenarioConfigOpen] = useState(false);

  const selected = projects.find((item) => item.id === projectId) || null;

  const loadProjects = async () => {
    const rows = await api<Project[]>('/writing/projects');
    setProjects(rows);
    const stored = sessionStorage.getItem('miaobi-project');
    const next = rows.some((row) => row.id === stored) ? stored! : rows[0]?.id || '';
    setProjectId(next);
  };

  const detailRequest = useRef(0);
  const loadProjectDetails = async (id: string) => {
    const request = ++detailRequest.current;
    const [detail, factRows, planRows, documentRows, computationRows, gateRows, context, materialRows] = await Promise.all([
      api<Project>(`/writing/projects/${id}`),
      api<Fact[]>(`/writing/projects/${id}/facts`),
      api<AlternativePlan[]>(`/writing/projects/${id}/plans`),
      api<WritingDocument[]>(`/writing/projects/${id}/documents`),
      api<ComputationRun[]>(`/writing/projects/${id}/computations`),
      api<DecisionGate[]>(`/writing/projects/${id}/decision-gates`),
      api<KnowledgeContext>(`/writing/projects/${id}/knowledge-context`),
      api<ProjectMaterial[]>(`/writing/projects/${id}/materials`),
    ]);
    const fullDocument = documentRows.length ? await api<WritingDocument>(`/writing/documents/${documentRows[0].id}`) : null;
    if (request !== detailRequest.current) return;
    setProject(detail);
    setFacts(factRows);
    setPlans(planRows);
    setDocuments(documentRows);
    setComputations(computationRows);
    setGates(gateRows);
    setKnowledgeContext(context);
    setMaterials(materialRows);
    setDocument(fullDocument);
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
      setKnowledgeContext(null);
      setMaterials([]);
      return;
    }
    setProject(null); setDocument(null); setSelectedBinding(null); setError('');
    sessionStorage.setItem('miaobi-project', projectId);
    loadProjectDetails(projectId).catch((reason) => setError(reason instanceof Error ? reason.message : '方案任务加载失败'));
    return () => { detailRequest.current += 1; };
  }, [projectId]);

  useEffect(() => {
    const openBinding = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail || {};
      setSelectedBinding(detail);
      setAssistantTab('evidence');
    };
    window.addEventListener('miaobi:open-binding', openBinding);
    return () => window.removeEventListener('miaobi:open-binding', openBinding);
  }, []);

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
        <div className="top-actions"><span className="save-state"><Save size={15} />{dirty ? '有未保存修改' : '内容已保存'}</span>{user?.is_admin && project?.scenario?.package_id && <button type="button" className="secondary" onClick={() => setScenarioConfigOpen(true)}><Settings2 size={16} />场景配置</button>}<span className="avatar">{user?.display_name?.slice(0, 1) || '用'}</span></div>
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
            {tab === 'task' && project && <TaskWorkspace key={project.id} project={project || selected} materials={materials} facts={facts} computations={computations} plans={plans} document={document} knowledgeContext={knowledgeContext} onChanged={() => loadProjectDetails(selected.id)} onOpenEditor={() => setTab('writing')} onError={setError} />}
            {tab === 'writing' && (
              <div className="writing-layout">
                <section className="outline-pane"><b>文稿目录</b>{document ? <Outline content={document.current_version?.content || []} /> : <p>创建文稿后自动生成目录。</p>}<div className="missing-box"><AlertTriangle size={16} /><span>缺失项会在这里提示，不会由模型静默补齐。</span></div></section>
                <section className="document-pane">
                  {document ? <Suspense fallback={<div className="editor-shell editor-loading">正在加载完整文稿编辑器…</div>}><MiaobiEditor key={document.id} document={document} onDirtyChange={setDirty} onSaved={(saved) => setDocument(saved)} onRequestSource={setAssistantTab} onAgentEdit={(request) => runAgentTextEdit(document.id, request)} onAgentEditDecision={(editId, decision) => api(`/writing/agent-edits/${editId}/decision`, { method: 'POST', body: { decision } })} onAgentActivity={() => setAssistantTab('assistant')} insertionRequest={insertionRequest} onInserted={() => setInsertionRequest(null)} /></Suspense> : <EmptyAction title="还没有文稿" detail="先在“方案任务”中确认输入并点击“开始生成报告”。" action="前往方案任务" onClick={() => setTab('task')} />}
                </section>
                <aside className="assistant-pane">
                  {knowledgeContext && <button type="button" className="writing-knowledge-baseline" onClick={() => setTab('task')} title="查看当前文稿使用的传神智库知识版本"><BookOpenCheck size={16} /><span><small>当前知识基线</small><b>{knowledgeContext.product.name} · V{knowledgeContext.release.version}</b></span><em>{knowledgeContext.task_material_count ? `${knowledgeContext.task_material_count} 份任务材料` : `${knowledgeContext.document_count} 项知识资产`}</em><ChevronRight size={15} /></button>}
                  <div className="assistant-tabs">
                    {([['assistant','妙笔助手'],['evidence','来源与计算'],['review','审校发布']] as const).map(([key,label]) => <button type="button" key={key} className={assistantTab === key ? 'active' : ''} onClick={() => setAssistantTab(key)}>{label}</button>)}
                  </div>
                  <AssistantPanel tab={assistantTab} project={selected} document={document} facts={facts} plans={plans} computations={computations} gates={gates} selectedBinding={selectedBinding} onInsert={setInsertionRequest} onChanged={() => loadProjectDetails(selected.id)} onError={setError} />
                </aside>
              </div>
            )}
          </>
        )}
      </main>
      {error && <div className="toast" role="alert">{error}<button type="button" onClick={() => setError('')}>×</button></div>}
      {createOpen && <CreateProjectDialog onClose={() => setCreateOpen(false)} onCreated={async (row) => { setCreateOpen(false); await loadProjects(); setProjectId(row.id); setTab('task'); }} onError={setError} />}
      {scenarioConfigOpen && project?.scenario?.package_id && <ScenarioConfigDialog packageId={project.scenario.package_id} versionId={project.scenario.version_id} onClose={() => setScenarioConfigOpen(false)} onSaved={() => loadProjectDetails(project.id)} onError={setError} />}
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

function TaskWorkspace({ project, materials, facts, computations, plans, document, knowledgeContext, onChanged, onOpenEditor, onError }: {
  project: Project;
  materials: ProjectMaterial[];
  facts: Fact[];
  computations: ComputationRun[];
  plans: AlternativePlan[];
  document: WritingDocument | null;
  knowledgeContext: KnowledgeContext | null;
  onChanged: () => Promise<void>;
  onOpenEditor: () => void;
  onError: (message: string) => void;
}) {
  const [stage, setStage] = useState<'inputs' | 'toolbox' | 'report'>('inputs');
  const requiredKeys = project.input_contract?.required || [];
  const requiredFacts = requiredKeys.length ? facts.filter((item) => requiredKeys.includes(item.fact_key)) : facts.filter((item) => !['deterministic_computation', 'semantica_inference'].includes(item.fact_type));
  const readyKeys = (requiredKeys.length ? requiredKeys : requiredFacts.map((item) => item.fact_key)).filter((key) => {
    const fact = requiredFacts.find((item) => item.fact_key === key);
    const confirmationRequired = project.input_contract?.properties?.[key]?.confirmation_required !== false;
    return Boolean(fact && ['current', 'manual_override'].includes(fact.freshness_status) && (!confirmationRequired || fact.verification_status === 'verified'));
  });
  const verified = readyKeys.length;
  const ready = materials.length > 0 && readyKeys.length === (requiredKeys.length || requiredFacts.length);
  const pending = Math.max(0, (requiredKeys.length || requiredFacts.length) - verified);
  const nextLabel = !materials.length ? '请先选择报告材料' : ready ? '输入已确认，下一步' : `还有 ${pending} 项待确认`;
  const reportExists = Boolean(document?.current_version && document.current_version.change_summary !== '创建报告草稿');
  const latest = new Map<string, ComputationRun>();
  computations.forEach((item) => {
    const key = item.result.output_fact?.fact_key;
    if (key && !latest.has(key)) latest.set(key, item);
  });
  const steps = [
    { key: 'inputs' as const, index: 1, title: '输入确认', detail: !materials.length ? '等待选择材料' : ready ? `${verified} 项关键输入已确认` : `${pending} 项待处理`, done: ready },
    { key: 'toolbox' as const, index: 2, title: '分析计算', detail: latest.size ? `${latest.size} 项结果` : '系统自动推演与测算', done: latest.size > 0 },
    { key: 'report' as const, index: 3, title: '报告编辑', detail: reportExists ? '报告草稿已生成' : '等待完成正文生成', done: reportExists },
  ];
  return <div className="task-workspace">
    <section className="workflow-strip" aria-label="报告生成流程">
      {steps.map((item) => <button type="button" key={item.key} className={stage === item.key ? 'active' : ''} onClick={() => item.key === 'report' && reportExists ? onOpenEditor() : setStage(item.key)}>
        <span className={item.done ? 'flow-number done' : 'flow-number'}>{item.done ? <CheckCircle2 size={17} /> : item.index}</span><span><b>{item.title}</b><small>{item.detail}</small></span>
      </button>)}
    </section>
    {stage === 'inputs' && <>
      <section className="task-intro-card"><div><span className="eyebrow">第一步</span><h2>准备材料并确认报告输入</h2><p>先明确这份报告使用哪些业务材料，再核对从材料中提取的关键输入。</p></div><div className="task-intro-actions"><button type="button" className="primary" disabled={!ready} onClick={() => setStage('toolbox')}>{nextLabel}<ChevronRight size={16} /></button></div></section>
      {knowledgeContext && <section className="compact-knowledge-baseline"><BookOpenCheck size={18} /><div><b>{knowledgeContext.product.name} · V{knowledgeContext.release.version}</b><small>{knowledgeContext.task_material_count ? `${knowledgeContext.task_material_count} 份已选业务材料` : '尚未选择任务材料'} · {knowledgeContext.chunk_count} 个已发布知识片段</small></div><span className={`status ${knowledgeContext.release.is_latest ? 'verified' : 'pending'}`}>{knowledgeContext.release.is_latest ? '当前版本' : '有新版本'}</span></section>}
      <MaterialsPanel project={project} materials={materials} knowledgeContext={knowledgeContext} onChanged={onChanged} onError={onError} />
      <Facts project={project} facts={facts} requiredKeys={requiredKeys} document={document} onChanged={onChanged} onError={onError} />
    </>}
    {stage === 'toolbox' && <ChapterEvidencePanel projectId={project.id} onChanged={onChanged} />}
    {stage === 'toolbox' && <ReportGenerationPanel project={project} facts={facts} computations={computations} plans={plans} document={document} ready={ready} onChanged={onChanged} onOpenEditor={onOpenEditor} onBack={() => setStage('inputs')} onError={onError} />}
    {stage === 'report' && (reportExists ? <section className="report-ready-card"><CheckCircle2 /><div><h2>报告已经生成</h2><p>继续修改正文并核对右侧依据。</p></div><button type="button" className="primary" onClick={onOpenEditor}>打开报告编辑器<ChevronRight size={16} /></button></section> : <EmptyAction title="报告尚未生成" detail="请在分析计算中查看生成状态或重试。" action="返回分析计算" onClick={() => setStage('toolbox')} />)}
  </div>;
}

const MATERIAL_ROLE_LABELS: Record<ProjectMaterial['material_role'], string> = {
  policy_basis: '政策与制度依据',
  task_data: '本次任务数据',
  reference: '写作参考材料',
  attachment: '报告附件',
};

function MaterialsPanel({ project, materials, knowledgeContext, onChanged, onError }: {
  project: Project;
  materials: ProjectMaterial[];
  knowledgeContext: KnowledgeContext | null;
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<ProjectMaterialCandidate[]>([]);
  const [selectedVersion, setSelectedVersion] = useState('');
  const [role, setRole] = useState<ProjectMaterial['material_role']>('reference');
  const [submitting, setSubmitting] = useState(false);
  const uploadSpaceId = materials[0]?.document.space_id || knowledgeContext?.spaces[0]?.id || '';
  const prepareZhikuUpload = () => {
    sessionStorage.setItem('miaobi-project', project.id);
    localStorage.setItem('miaobi.pendingProject', project.id);
    if (uploadSpaceId) localStorage.setItem('chuanshen.pendingSpace', uploadSpaceId);
  };
  const loadCandidates = async () => {
    try {
      const rows = await api<ProjectMaterialCandidate[]>(`/writing/projects/${project.id}/material-candidates`);
      setCandidates(rows);
      setSelectedVersion(rows.find((item) => !item.already_linked && ['processed', 'published', 'ready'].includes(item.processing_status))?.version_id || '');
      setOpen(true);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '材料列表加载失败'); }
  };
  const add = async () => {
    const candidate = candidates.find((item) => item.version_id === selectedVersion);
    if (!candidate || submitting) return;
    setSubmitting(true);
    try {
      await api(`/writing/projects/${project.id}/materials`, {
        method: 'POST',
        body: { document_id: candidate.document_id, version_id: candidate.version_id, material_role: role, usage_scope: 'task_only' },
      });
      setOpen(false);
      await onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : '加入材料失败'); }
    finally { setSubmitting(false); }
  };
  const updateRole = async (material: ProjectMaterial, materialRole: ProjectMaterial['material_role']) => {
    try {
      await api(`/writing/projects/${project.id}/materials/${material.id}`, { method: 'PUT', body: { material_role: materialRole } });
      await onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : '材料角色更新失败'); }
  };
  const remove = async (material: ProjectMaterial) => {
    if (!window.confirm(`从当前任务移除“${material.document.title}”？智库中的原文不会删除。`)) return;
    try {
      await api(`/writing/projects/${project.id}/materials/${material.id}`, { method: 'DELETE' });
      await onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : '移除材料失败'); }
  };
  return <>
    <section className="content-card project-materials">
      <div className="card-toolbar"><div><span className="eyebrow">本报告使用的材料</span><h2>{materials.length ? `已选择 ${materials.length} 份` : '尚未选择材料'}</h2></div><div className="task-intro-actions"><a className="secondary" href="/#documents" onClick={prepareZhikuUpload} title={knowledgeContext?.spaces[0] ? `上传到 ${knowledgeContext.spaces[0].name}` : '上传到传神智库'}><Upload size={15} />上传到智库</a><button type="button" className="primary compact" onClick={() => void loadCandidates()}>从智库选择</button></div></div>
      {materials.length ? <div className="material-list">{materials.map((material) => <article key={material.id}><FileText size={20} /><div><b>{material.document.title}</b><small>{material.version.filename} · 固定 V{material.version.version_number}{material.current_document_version ? '' : ' · 历史版本'}</small></div><select aria-label={`设置${material.document.title}的材料角色`} value={material.material_role} onChange={(event) => void updateRole(material, event.target.value as ProjectMaterial['material_role'])}>{Object.entries(MATERIAL_ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><button type="button" className="icon-button danger-icon" title="从任务移除" onClick={() => void remove(material)}><Trash2 size={16} /></button></article>)}</div> : <div className="material-empty"><FileText /><div><b>先选择本次报告真正使用的材料</b><span>资料上传、解析和治理仍在传神智库完成；加入后，妙笔检索会限定在这些固定版本内。</span></div></div>}
    </section>
    {open && <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) setOpen(false); }}><section className="dialog compact-dialog" role="dialog" aria-modal="true" aria-label="从智库选择材料"><div className="dialog-head"><div><span className="eyebrow">固定材料版本</span><h2>从智库选择</h2></div><button type="button" className="icon-button" disabled={submitting} onClick={() => setOpen(false)}>×</button></div><label>材料<select autoFocus value={selectedVersion} onChange={(event) => setSelectedVersion(event.target.value)}><option value="">请选择已完成加工的材料</option>{candidates.map((item) => <option key={item.version_id} value={item.version_id} disabled={item.already_linked || !['processed', 'published', 'ready'].includes(item.processing_status)}>{item.title} · V{item.version_number}{item.already_linked ? '（已加入）' : !['processed', 'published', 'ready'].includes(item.processing_status) ? '（加工中）' : ''}</option>)}</select></label><label>在报告中的用途<select value={role} onChange={(event) => setRole(event.target.value as ProjectMaterial['material_role'])}>{Object.entries(MATERIAL_ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><p className="field-help">选择的是文档固定版本。后续智库出现新版本时，系统不会静默替换本报告依据。</p>{!candidates.length && <p className="field-help warning-text">当前知识产品没有可选择的文档，请先到传神智库上传并发布知识版本。</p>}<div className="dialog-actions"><button type="button" className="secondary" disabled={submitting} onClick={() => setOpen(false)}>取消</button><button type="button" className="primary" disabled={submitting || !selectedVersion} onClick={() => void add()}>{submitting ? '加入中…' : '加入当前任务'}</button></div></section></div>}
  </>;
}

function ReportGenerationPanel({ project, facts, computations, plans, document, ready, onChanged, onOpenEditor, onBack, onError }: {
  project: Project;
  facts: Fact[];
  computations: ComputationRun[];
  plans: AlternativePlan[];
  document: WritingDocument | null;
  ready: boolean;
  onChanged: () => Promise<void>;
  onOpenEditor: () => void;
  onBack: () => void;
  onError: (message: string) => void;
}) {
  const [run, setRun] = useState<WritingGenerationRun | null>(null);
  const [running, setRunning] = useState(false);
  const [stageLabel, setStageLabel] = useState('等待开始');
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    api<WritingGenerationRun[]>(`/writing/projects/${project.id}/generation-runs`)
      .then((rows) => {
        const latest = rows[0] || null;
        setRun(latest);
        setStageLabel(generationStageLabel(latest, Boolean(document)));
      })
      .catch(() => {
        setRun(null);
        setStageLabel(generationStageLabel(null, Boolean(document)));
      });
    return () => abortRef.current?.abort();
  }, [document?.id, project.id]);

  const start = async () => {
    if (!ready || running) return;
    setRunning(true);
    setStageLabel('正在核验输入并运行推演工具箱');
    try {
      const created = await api<WritingGenerationRun>(`/writing/projects/${project.id}/generate-report`, {
        method: 'POST', body: { document_id: document?.id || null },
      });
      setRun(created);
      setStageLabel('正在依据知识生成报告正文');
      const controller = new AbortController();
      abortRef.current = controller;
      const response = await fetch(`/api/v1/writing/generation-runs/${created.id}/agent`, {
        method: 'POST', credentials: 'same-origin', signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(String(detail?.detail || '报告生成 Agent 启动失败'));
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let finished = false;
      while (!finished) {
        const chunk = await reader.read();
        finished = chunk.done;
        buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !finished });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';
        for (const frame of frames) {
          const event = frame.split('\n').find((line) => line.startsWith('event:'))?.slice(6).trim() || '';
          const raw = frame.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
          const data = JSON.parse(raw || '{}') as Record<string, unknown>;
          if (event === 'tool_started') setStageLabel(writingStage(String(data.name || data.tool || '')));
          if (event === 'retrieval_started') setStageLabel('正在查找报告依据');
          if (event === 'answer_delta') setStageLabel('正在组织正式报告正文');
          if (event === 'turn_failed') throw new Error(String(data.message || data.reason || '报告正文生成失败'));
          if (event === 'turn_cancelled') throw new Error('报告生成已停止');
        }
      }
      setStageLabel('正在执行报告质量检查');
      const completed = await api<WritingGenerationRun>(`/writing/generation-runs/${created.id}/finalize`, { method: 'POST' });
      setRun(completed);
      await onChanged();
      setStageLabel('报告已生成');
      onOpenEditor();
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === 'AbortError')) onError(reason instanceof Error ? reason.message : '报告生成失败');
    } finally {
      abortRef.current = null;
      setRunning(false);
    }
  };
  const stop = async () => {
    if (!run || !running) return;
    await api(`/writing/generation-runs/${run.id}/cancel`, { method: 'POST' }).catch(() => undefined);
    abortRef.current?.abort();
    setRunning(false);
    setStageLabel('本次生成已停止，可重新开始');
  };
  const progress = run?.progress || (document?.current_version?.change_summary !== '创建报告草稿' && document ? 100 : 0);
  const latest = new Map<string, ComputationRun>();
  computations.forEach((item) => {
    const key = item.result.output_fact?.fact_key;
    if (key && !latest.has(key)) latest.set(key, item);
  });
  const selectedPlan = plans.find((item) => item.status === 'selected');
  const conclusion = facts.find((item) => item.fact_type === 'semantica_inference' && item.freshness_status === 'current');
  return <div className="toolbox-page">
    <section className="task-intro-card"><div><span className="eyebrow">第二步</span><h2>系统自动完成分析计算并生成报告</h2><p>一次执行规则推演、确定性测算、方案比较、知识检索和正文生成；数值与结论由服务端写入指定章节。</p></div><div className="task-intro-actions"><button type="button" className="secondary" disabled={running} onClick={onBack}>返回检查输入</button><button type="button" className="primary generation-button" disabled={!ready || running} onClick={() => void start()}><Sparkles size={17} />{running ? stageLabel : document ? '重新生成报告' : '开始生成报告'}</button>{running && <button type="button" className="danger-soft" onClick={() => void stop()}><Square size={15} />停止</button>}</div></section>
    {(running || run) && <section className="generation-progress"><div><b>{stageLabel}</b><span>{progress}%</span></div><progress max="100" value={progress} /><small>进度来自真实后端任务与 Agent 事件，不使用固定动画伪造阶段。</small></section>}
    <section className="toolbox-summary-grid">
      <article><span>输入数据</span><b>{facts.filter((item) => item.verification_status === 'verified').length}/{facts.length}</b><small>只有已确认输入参与计算</small></article>
      <article><span>规则结论</span><b>{conclusion ? formatValue(conclusion.value) : '待生成'}</b><small>由规则推演引擎生成</small></article>
      <article><span>确定性结果</span><b>{latest.size ? `${latest.size} 项` : '待计算'}</b><small>数值不能由模型自由改写</small></article>
      <article><span>采用方案</span><b>{selectedPlan?.name || '待求解'}</b><small>基于真实约束和优化目标</small></article>
    </section>
    {latest.size > 0 && <section className="content-card"><div className="card-toolbar"><div><span className="eyebrow">推演工具箱结果</span><h2>自动进入报告的权威内容</h2></div><span className="status verified">已完成</span></div><div className="calculation-grid">{Array.from(latest.values()).map((item) => <article key={item.id}><small>{item.result.output_fact?.label || item.result.operation}</small><b>{item.result.value} {item.result.output_fact?.unit || ''}</b><span>由已确认输入自动计算</span></article>)}</div>{selectedPlan && <div className="selected-plan-summary"><b>{selectedPlan.name}</b><span>{selectedPlan.result.route?.path?.join(' → ') || '方案约束已计算'}</span></div>}</section>}
    {run?.status === 'quality_failed' && <section className="quality-failed"><AlertTriangle /><div><b>报告没有通过质量门，未覆盖当前正文</b><p>{run.error_message || '请查看章节、引用或篇幅问题后重试。'}</p><ul>{run.quality_report?.issues?.map((item) => <li key={item.code}>{item.message}</li>)}</ul></div></section>}
  </div>;
}

function Overview({ project, facts, plans, documents, knowledgeContext, onChanged, onContinue }: { project: Project; facts: Fact[]; plans: AlternativePlan[]; documents: WritingDocument[]; knowledgeContext: KnowledgeContext | null; onChanged: () => Promise<void>; onContinue: (tab: Tab) => void }) {
  const verified = facts.filter((item) => item.verification_status === 'verified').length;
  const [rebasing, setRebasing] = useState(false);
  const steps: Array<{ title: string; detail: string; done: boolean; target: Tab }> = [
    { title: '准备数据与事实', detail: `${facts.length} 条事实，${verified} 条已核验`, done: facts.length > 0 && verified === facts.length, target: 'facts' },
    { title: '规则与计算推演', detail: '形成等级、任务与资源缺口依据', done: plans.length > 0, target: 'reasoning' },
    { title: '比较备选方案', detail: `${plans.length} 套真实算法方案`, done: plans.length >= 2, target: 'plans' },
    { title: '撰写与审校', detail: `${documents.length} 份文稿`, done: documents.length > 0, target: 'writing' },
  ];
  const openSpace = (spaceId: string, hash: string) => {
    localStorage.setItem('chuanshen.pendingSpace', spaceId);
    window.location.href = `/${hash}`;
  };
  const rebase = async () => {
    if (!knowledgeContext || knowledgeContext.release.is_latest || rebasing) return;
    if (!window.confirm('切换到最新知识版本后，正文中已有依据将标记为需要核验。继续吗？')) return;
    setRebasing(true);
    try {
      const releases = await api<Array<{ id: string; version: number; status: string }>>(`/knowledge-products/${knowledgeContext.product.id}/releases`);
      const latest = releases.filter((item) => item.status === 'published').sort((left, right) => right.version - left.version)[0];
      if (!latest) throw new Error('当前知识产品没有可用的已发布版本');
      await api(`/writing/projects/${project.id}/knowledge-release`, { method: 'POST', body: { knowledge_product_release_id: latest.id, reason: '在妙笔任务概览中采用最新知识基线' } });
      await onChanged();
    } finally { setRebasing(false); }
  };
  return <div className="overview-grid">
    <section className="hero-card"><div><span className="eyebrow">当前进度</span><h2>{steps.find((item) => !item.done)?.title || '可以进入审校发布'}</h2><p>系统不会替您跳过缺失事实、口径冲突和人工确认。</p></div><button type="button" className="primary" onClick={() => onContinue(steps.find((item) => !item.done)?.target || 'review')}>继续处理<ChevronRight size={17} /></button></section>
    {knowledgeContext && <section className="knowledge-context-card" aria-label="传神智库知识基线">
      <div className="knowledge-context-head"><div><span className="eyebrow">来自传神智库</span><h2>{knowledgeContext.product.name} · Release V{knowledgeContext.release.version}</h2><p>妙笔检索、推演和正文依据固定使用这一版知识，生成过程不会被后台新数据静默改变。</p></div><span className={`status ${knowledgeContext.release.is_latest ? 'verified' : 'pending'}`}>{knowledgeContext.release.is_latest ? '当前最新版' : '有新版本'}</span></div>
      <div className="knowledge-context-metrics"><Metric label="来源文档" value={String(knowledgeContext.document_count)} /><Metric label="知识片段" value={String(knowledgeContext.chunk_count)} /><Metric label="图谱对象" value={String(knowledgeContext.entity_count)} /><Metric label="知识关系" value={String(knowledgeContext.fact_count)} /></div>
      <div className="knowledge-space-list">{knowledgeContext.spaces.map((space) => <article key={space.id}><div><b>{space.name}</b><small>知识版本 V{space.knowledge_release_number} · {space.document_count} 份文档 · {space.chunk_count} 个片段</small></div><div className="knowledge-channel-tags"><span className={space.vector_available ? 'ready' : 'missing'}>全文/向量{space.vector_available ? '可用' : '未发布'}</span><span className={space.graph_available ? 'ready' : 'missing'}>图谱{space.graph_available ? '可用' : '未发布'}</span></div><div className="knowledge-context-actions"><button type="button" onClick={() => openSpace(space.id, '#documents')}>查看材料</button><button type="button" onClick={() => openSpace(space.id, '#graph')}>查看图谱</button></div></article>)}</div>
      <div className="knowledge-context-foot"><span>材料接入与治理在传神智库完成；妙笔消费已发布的知识版本，并把引用、测算和推演结果写入正文。</span><div><a className="secondary" href="/#knowledge-products">管理知识产品</a>{!knowledgeContext.release.is_latest && <button type="button" className="primary compact" disabled={rebasing} onClick={() => void rebase()}>{rebasing ? '切换中…' : '采用最新知识'}</button>}</div></div>
    </section>}
    <section className="step-list">{steps.map((step, index) => <button type="button" key={step.title} onClick={() => onContinue(step.target)}><span className={step.done ? 'step done' : 'step'}>{step.done ? <CheckCircle2 size={18} /> : index + 1}</span><span><b>{step.title}</b><small>{step.detail}</small></span><ChevronRight size={17} /></button>)}</section>
    <section className="metrics"><Metric label="已核验事实" value={`${verified}/${facts.length}`} /><Metric label="备选方案" value={String(plans.length)} /><Metric label="文稿版本" value={String(documents.length)} /><Metric label="待确认" value={String(project.pending_gates || 0)} /></section>
  </div>;
}

function Facts({ project, facts, requiredKeys = [], document, onChanged, onError }: { project: Project; facts: Fact[]; requiredKeys?: string[]; document?: WritingDocument | null; onChanged: () => Promise<void>; onError: (message: string) => void }) {
  const [query, setQuery] = useState('');
  const [pendingOnly, setPendingOnly] = useState(false);
  const [decision, setDecision] = useState<{ fact: Fact; mode: 'confirm' | 'reject' | 'override' } | null>(null);
  const [reason, setReason] = useState('已核对当前业务材料');
  const [overrideValue, setOverrideValue] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [impactPreview, setImpactPreview] = useState<WritingInputChange | null>(null);
  const [missingInput, setMissingInput] = useState<{ key: string; label: string; type: string; unit?: string; value: string } | null>(null);
  const allowManualOverride = project.scenario?.business_config?.writing_policy.allow_manual_override !== false;
  const inputFacts = requiredKeys.length ? facts.filter((fact) => requiredKeys.includes(fact.fact_key)) : facts.filter((fact) => !['deterministic_computation', 'semantica_inference'].includes(fact.fact_type));
  const missingKeys = requiredKeys.filter((key) => !inputFacts.some((fact) => fact.fact_key === key));
  const visible = inputFacts.filter((fact) => {
    const confirmationRequired = project.input_contract?.properties?.[fact.fact_key]?.confirmation_required !== false;
    if (pendingOnly && (!confirmationRequired || fact.verification_status === 'verified')) return false;
    const text = `${fact.label} ${fact.fact_key} ${formatValue(fact.value)}`.toLowerCase();
    return text.includes(query.trim().toLowerCase());
  });
  const requiresConfirmation = (fact: Fact) => project.input_contract?.properties?.[fact.fact_key]?.confirmation_required !== false;
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
      if (decision.mode === 'override' && document && newValue) {
        const preview = await api<WritingInputChange>(`/writing/projects/${project.id}/input-changes/preview`, {
          method: 'POST',
          body: { document_id: document.id, changes: [{ fact_key: decision.fact.fact_key, new_value: newValue, reason: reason.trim() }] },
        });
        setImpactPreview(preview);
      } else {
        await api(`/writing/projects/${project.id}/facts/${decision.fact.id}/confirm`, {
          method: 'POST', body: { decision: decision.mode, reason: reason.trim(), ...(newValue ? { new_value: newValue } : {}) },
        });
        await onChanged();
      }
      setDecision(null);
    } catch (failure) { onError(failure instanceof Error ? failure.message : '事实处理失败'); }
    finally { setSubmitting(false); }
  };
  const applyImpact = async () => {
    if (!impactPreview || submitting) return;
    setSubmitting(true);
    try {
      await api(`/writing/projects/${project.id}/input-changes/apply`, { method: 'POST', body: { preview_id: impactPreview.id } });
      setImpactPreview(null);
      await onChanged();
    } catch (failure) { onError(failure instanceof Error ? failure.message : '应用输入变化失败'); }
    finally { setSubmitting(false); }
  };
  const cancelImpact = async () => {
    if (!impactPreview || submitting) return;
    setSubmitting(true);
    try { await api(`/writing/projects/${project.id}/input-changes/${impactPreview.id}/cancel`, { method: 'POST' }); }
    finally { setImpactPreview(null); setSubmitting(false); }
  };
  const createMissing = async () => {
    if (!missingInput || submitting || !missingInput.value.trim()) return;
    setSubmitting(true);
    try {
      const numeric = ['number', 'integer'].includes(missingInput.type);
      const parsed = Number(missingInput.value);
      if (numeric && !Number.isFinite(parsed)) throw new Error('请填写有效数值');
      await api(`/writing/projects/${project.id}/facts`, { method: 'POST', body: {
        fact_key: missingInput.key, label: missingInput.label, fact_type: 'manual_input',
        value: numeric ? { number: parsed } : { text: missingInput.value.trim() }, unit: missingInput.unit || null,
        source_type: 'manual_input', source_locator: { reason: '业务人员补充报告输入' }, verification_status: 'unverified',
      } });
      setMissingInput(null);
      await onChanged();
    } catch (failure) { onError(failure instanceof Error ? failure.message : '补充输入失败'); }
    finally { setSubmitting(false); }
  };
  return <><div className="content-card"><div className="card-toolbar"><div className="search"><Search size={16} /><input placeholder="搜索输入项" value={query} onChange={(event) => setQuery(event.target.value)} /></div><div className="task-intro-actions"><a className="secondary" href="/#assets">从材料提取</a><button type="button" className={pendingOnly ? 'primary compact' : 'secondary'} onClick={() => setPendingOnly((value) => !value)}>{pendingOnly ? '显示全部输入' : '只看待确认'}</button></div></div><div className="table-scroll"><table><thead><tr><th>输入项</th><th>当前值</th><th>来源</th><th>版本</th><th>状态</th><th>操作</th></tr></thead><tbody>{visible.map((fact) => <tr key={fact.id}><td><b>{fact.label}</b></td><td>{formatValue(fact.value)} {fact.unit || ''}</td><td>{sourceLabel(fact.source_type)}</td><td>v{fact.version}</td><td><span className={`status ${fact.verification_status}`}>{!requiresConfirmation(fact) ? '无需确认' : fact.verification_status === 'verified' ? '已确认' : fact.verification_status === 'rejected' ? '已驳回' : '待确认'}</span></td><td><div className="row-actions">{requiresConfirmation(fact) && fact.verification_status !== 'verified' && <><button type="button" onClick={() => openDecision(fact, 'confirm')}>确认</button><button type="button" onClick={() => openDecision(fact, 'reject')}>驳回</button></>}{allowManualOverride && <button type="button" onClick={() => openDecision(fact, 'override')}>修正</button>}</div></td></tr>)}{!pendingOnly && missingKeys.map((key) => { const field = project.input_contract?.properties?.[key] || {}; return <tr className="missing-input-row" key={key}><td><b>{field.title || key}</b></td><td>—</td><td>尚未提供</td><td>—</td><td><span className="status missing">缺少</span></td><td><button type="button" onClick={() => setMissingInput({ key, label: field.title || key, type: field.type || 'string', unit: field.unit, value: '' })}>填写</button></td></tr>; })}</tbody></table></div>{!visible.length && !missingKeys.length && <div className="empty-table">没有符合当前条件的输入</div>}</div>{missingInput && <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) setMissingInput(null); }}><section className="dialog compact-dialog" role="dialog" aria-modal="true" aria-label="补充缺失输入"><div className="dialog-head"><div><span className="eyebrow">缺失输入</span><h2>{missingInput.label}</h2></div><button type="button" className="icon-button" disabled={submitting} onClick={() => setMissingInput(null)}>×</button></div><label>当前值<input autoFocus type={['number', 'integer'].includes(missingInput.type) ? 'number' : 'text'} value={missingInput.value} onChange={(event) => setMissingInput({ ...missingInput, value: event.target.value })} /></label><p className="field-help">手工补充后状态为“待确认”，需再次核对后才会参与报告生成。</p><div className="dialog-actions"><button type="button" className="secondary" disabled={submitting} onClick={() => setMissingInput(null)}>取消</button><button type="button" className="primary" disabled={submitting || !missingInput.value.trim()} onClick={() => void createMissing()}>{submitting ? '保存中…' : '保存待确认'}</button></div></section></div>}{decision && <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !submitting) setDecision(null); }}><section className="dialog compact-dialog" role="dialog" aria-modal="true"><div className="dialog-head"><div><span className="eyebrow">输入确认</span><h2>{decision.mode === 'confirm' ? '确认输入' : decision.mode === 'reject' ? '暂不采用' : '修正输入'}</h2></div><button type="button" className="icon-button" disabled={submitting} onClick={() => setDecision(null)}>×</button></div><div className="fact-preview"><b>{decision.fact.label}</b><span>{formatValue(decision.fact.value)} {decision.fact.unit || ''}</span></div>{decision.mode === 'override' && <label>修正后的值<input value={overrideValue} onChange={(event) => setOverrideValue(event.target.value)} autoFocus /></label>}<label>处理理由<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={3} /></label><p className="field-help">{document && decision.mode === 'override' ? '系统会先计算这项变化影响哪些结果和报告章节，确认“应用”后才生效。' : '系统保留原值和操作记录，已确认输入才会参与报告生成。'}</p><div className="dialog-actions"><button type="button" className="secondary" disabled={submitting} onClick={() => setDecision(null)}>取消</button><button type="button" className="primary" disabled={submitting || reason.trim().length < 2 || (decision.mode === 'override' && !overrideValue.trim())} onClick={() => void submit()}>{submitting ? '处理中…' : document && decision.mode === 'override' ? '查看影响' : '确认处理'}</button></div></section></div>}{impactPreview && <div className="dialog-backdrop" role="presentation"><section className="dialog impact-dialog" role="dialog" aria-modal="true"><div className="dialog-head"><div><span className="eyebrow">变更预览</span><h2>确认后才会更新报告</h2></div></div><div className="impact-changes">{impactPreview.changes.map((item) => <div key={item.fact_key}><b>{item.label}</b><span>{formatValue(item.old_value)} → {formatValue(item.new_value)} {item.unit || ''}</span></div>)}</div><h3>受影响的计算</h3>{impactPreview.impact.calculations?.length ? <div className="impact-calculations">{impactPreview.impact.calculations.map((item) => <div key={item.result_key}><b>{item.label}</b><span>{item.old_value} → <strong>{item.new_value}</strong> {item.unit || ''}</span></div>)}</div> : <p className="field-help">没有受影响的确定性计算。</p>}<p className="field-help">影响 {impactPreview.impact.report_blocks?.length || 0} 个段落或测算项：测算值更新，相关正文标记为待核对，不自动改写。</p><div className="dialog-actions"><button type="button" className="secondary" disabled={submitting} onClick={() => void cancelImpact()}>取消</button><button type="button" className="primary" disabled={submitting} onClick={() => void applyImpact()}>{submitting ? '应用中…' : '应用本次变化'}</button></div></section></div>}</>;
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
  const allowedFormats = project.scenario?.business_config?.output.allowed_formats || ['docx', 'pdf', 'json', 'xlsx', 'geojson'];
  return <div className="review-page"><div className="two-columns"><section className="content-card"><h2>发布前检查</h2>{document ? <div className="check-list"><p><CheckCircle2 />文稿已创建并具有不可变版本</p><p><AlertTriangle />{unresolved.length ? `仍有 ${unresolved.length} 个人工确认节点` : '人工确认节点已完成'}</p><p><AlertTriangle />可信块不得存在失效或未核验依据</p></div> : <p>请先创建文稿。</p>}<button type="button" className="primary" disabled={!document || checking} onClick={() => void validate()}>{checking ? '检查中…' : '执行审校'}</button>{issues.length > 0 && <ul className="issue-list">{issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul>}{pendingGates.length > 0 && <div className="gate-review-list">{pendingGates.map((gate) => <div key={gate.id}><span>{gate.name}</span><button type="button" disabled={!!confirming} onClick={() => void confirmGate(gate)}>{confirming === gate.id ? '确认中…' : '确认'}</button></div>)}</div>}{document && !checking && !checked && issues.length === 0 && pendingGates.length === 0 && <p className="field-help">点击“执行审校”核对正文来源和发布条件。</p>}{document && !checking && checked && issues.length === 0 && pendingGates.length === 0 && <p className="review-success"><CheckCircle2 />审校通过，可以生成正式文件。</p>}</section><section className="content-card"><h2>正式导出</h2><p>只显示当前写作场景实际启用的格式。</p><div className="export-actions">{allowedFormats.map((format) => <button type="button" key={format} className={format === 'docx' || format === 'pdf' ? 'primary' : 'secondary'} disabled={!document || !!exporting} onClick={() => void createExport(format)}>{exporting === format ? '生成中…' : format.toUpperCase()}</button>)}</div></section></div><section className="content-card export-history"><h2>导出记录</h2>{exports.length ? <div className="export-list">{exports.map((job) => <div key={job.id}><span className={`status ${job.status}`}>{job.status === 'succeeded' ? '已生成' : job.status === 'failed' ? '失败' : `${job.progress}%`}</span><b>{job.manifest?.filename || job.output_format.toUpperCase()}</b><small>{job.checksum ? `校验值 ${job.checksum.slice(0, 12)}…` : job.error_message || ''}</small>{job.status === 'succeeded' && <a className="secondary" href={`/api/v1/writing/exports/${job.id}/download`}>下载</a>}</div>)}</div> : <div className="empty-mini">完成审校后生成的文件会保留版本、模板和校验记录。</div>}</section></div>;
}

function ReviewSidebar({ project, document, gates, onChanged, onError }: { project: Project; document: WritingDocument | null; gates: DecisionGate[]; onChanged: () => Promise<void>; onError: (message: string) => void }) {
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState(false);
  const [issues, setIssues] = useState<Array<{ code: string; message: string }>>([]);
  const [pending, setPending] = useState<DecisionGate[]>([]);
  const [confirming, setConfirming] = useState('');
  const [exporting, setExporting] = useState('');
  const [exports, setExports] = useState<ExportJob[]>([]);
  useEffect(() => {
    setChecked(false);
    setIssues([]);
    setPending([]);
    if (document) api<ExportJob[]>(`/writing/documents/${document.id}/exports`).then(setExports).catch(() => setExports([]));
  }, [document?.id]);
  const validate = async () => {
    if (!document || checking) return;
    setChecking(true);
    setChecked(false);
    try {
      const result = await api<{ issues: Array<{ code: string; message: string }>; pending_decision_gates: DecisionGate[] }>(`/writing/documents/${document.id}/validate`, { method: 'POST', body: { for_publish: true } });
      setIssues(result.issues || []);
      setPending(result.pending_decision_gates || []);
      setChecked(true);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '报告检查失败'); }
    finally { setChecking(false); }
  };
  const confirm = async (gate: DecisionGate) => {
    if (confirming) return;
    setConfirming(gate.id);
    try {
      await api(`/writing/projects/${project.id}/decision-gates/${gate.id}/records`, { method: 'POST', body: { decision: 'confirm', reason: '业务人员在报告编辑页确认' } });
      await onChanged();
      setPending((rows) => rows.filter((item) => item.id !== gate.id));
      setChecked(false);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '确认失败'); }
    finally { setConfirming(''); }
  };
  const createExport = async (format: ExportJob['output_format']) => {
    if (!document || exporting) return;
    setExporting(format);
    try {
      const job = await api<ExportJob>(`/writing/documents/${document.id}/exports`, { method: 'POST', body: { output_format: format } });
      setExports((rows) => [job, ...rows]);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '导出失败'); }
    finally { setExporting(''); }
  };
  const unresolved = gates.filter((item) => item.required && item.status !== 'confirmed');
  const statusText = issues.length ? `${issues.length} 项问题` : pending.length ? `${pending.length} 项待确认` : checked ? '审校通过' : unresolved.length ? `${unresolved.length} 项待确认` : '待检查';
  const passed = checked && !issues.length && !pending.length && !unresolved.length;
  const allowedFormats = project.scenario?.business_config?.output.allowed_formats || ['docx', 'pdf'];
  return <div className="assistant-content review-sidebar"><div className="assistant-title"><h3>审校与导出</h3><span className={`status ${passed ? 'verified' : 'pending'}`}>{statusText}</span></div><p>正式导出前检查章节、重复内容、可信数值、引用和人工确认。</p><button type="button" className="primary full-button" disabled={!document || checking} onClick={() => void validate()}>{checking ? '检查中…' : checked ? '重新执行质量检查' : '执行生产质量检查'}</button>{issues.length > 0 && <ul className="issue-list">{issues.map((item, index) => <li key={`${item.code}-${index}`}>{item.message}</li>)}</ul>}{passed && <p className="review-success"><CheckCircle2 />审校通过，可以生成正式文件。</p>}{pending.length > 0 && <div className="gate-review-list">{pending.map((gate) => <div key={gate.id}><span>{gate.name}</span><button type="button" disabled={!!confirming} onClick={() => void confirm(gate)}>{confirming === gate.id ? '确认中…' : '确认'}</button></div>)}</div>}<div className="sidebar-export"><b>生成文件</b><small>正式稿只包含业务正文；依据报告单独列出知识版本、输入、计算与引用。</small><div>{allowedFormats.map((format) => <button type="button" key={format} disabled={!document || !!exporting} onClick={() => void createExport(format)}>{exporting === format ? '生成中…' : format.toUpperCase()}</button>)}<button type="button" disabled={!document || !!exporting} onClick={() => void createExport('evidence_docx')}>{exporting === 'evidence_docx' ? '生成中…' : '依据报告'}</button></div></div>{exports.length > 0 && <div className="sidebar-export-history">{exports.slice(0, 8).map((job) => <a key={job.id} href={job.status === 'succeeded' ? `/api/v1/writing/exports/${job.id}/download` : undefined} aria-disabled={job.status !== 'succeeded'}><span>{job.manifest?.filename || job.output_format}</span><small>{job.status === 'succeeded' ? '下载' : job.error_message || `${job.progress}%`}</small></a>)}</div>}</div>;
}

function AssistantPanel({ tab, project, document, facts, plans, computations, gates, selectedBinding, onInsert, onChanged, onError }: { tab: string; project: Project; document: WritingDocument | null; facts: Fact[]; plans: AlternativePlan[]; computations: ComputationRun[]; gates: DecisionGate[]; selectedBinding: Record<string, unknown> | null; onInsert: (node: EditorInsertion) => void; onChanged: () => Promise<void>; onError: (message: string) => void }) {
  if (tab === 'evidence') return <EvidencePanel project={project} document={document} selectedBinding={selectedBinding} onInsert={onInsert} onError={onError} />;
  if (tab === 'calculation') return <CalculationPanel facts={facts} plans={plans} computations={computations} selectedBinding={selectedBinding} />;
  if (tab === 'review') return <ReviewSidebar project={project} document={document} gates={gates} onChanged={onChanged} onError={onError} />;
  return <WritingAssistant project={project} document={document} onInsert={onInsert} onError={onError} />;
}

function CalculationPanel({ facts, plans, computations, selectedBinding }: { facts: Fact[]; plans: AlternativePlan[]; computations: ComputationRun[]; selectedBinding: Record<string, unknown> | null }) {
  const latest = new Map<string, ComputationRun>();
  const factLabels = new Map(facts.map((item) => [item.fact_key, item.label]));
  computations.forEach((item) => {
    const key = item.result.output_fact?.fact_key;
    if (key && !latest.has(key)) latest.set(key, item);
  });
  const selected = plans.find((item) => item.status === 'selected');
  const inferences = facts.filter((item) => item.fact_type === 'semantica_inference' && item.verification_status === 'verified' && item.freshness_status === 'current');
  return <div className="assistant-content"><h3>计算与推演</h3>{selectedBinding && String(selectedBinding.type) !== 'knowledge_citation' && <BindingInspector binding={selectedBinding} />}<p>这些结果由系统在生成报告时自动写入正确章节，无需手工插入。</p>{inferences.length > 0 && <div className="calculation-list inference-list">{inferences.map((fact) => <article key={fact.id}><div><b>{fact.label}</b><strong>{formatValue(fact.value)}</strong></div><small>规则推演 · 已核验 · 点击正文标记可查看完整前提</small></article>)}</div>}<div className="calculation-list">{Array.from(latest.values()).map((run) => <article key={run.id}><div><b>{run.result.output_fact?.label || run.result.operation}</b><strong>{run.result.value} {run.result.output_fact?.unit || ''}</strong></div><small>{Object.values(run.result.dependencies || {}).map((key) => factLabels.get(String(key)) || '已核验输入').join('、')} · 自动计算</small></article>)}</div>{selected && <div className="selected-plan-mini"><span className="eyebrow">报告采用方案</span><b>{selected.name}</b><p>{selected.result.route?.path?.join(' → ')}</p></div>}{!inferences.length && !latest.size && <div className="empty-mini">返回“方案任务”，点击“开始生成报告”后自动形成推演和计算结果。</div>}</div>;
}

function WritingAssistant({ project, document, onInsert, onError }: { project: Project; document: WritingDocument | null; onInsert: (node: EditorInsertion) => void; onError: (message: string) => void }) {
  const [session, setSession] = useState<WritingAgentSession | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [input, setInput] = useState('请根据当前场景包和已核验事实，为我生成方案目录草稿。');
  const [running, setRunning] = useState(false);
  const [stage, setStage] = useState('等待指令');
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [insertingSuggestion, setInsertingSuggestion] = useState(false);
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
  const insertSuggestion = async () => {
    if (!latest || !document || insertingSuggestion) return;
    setInsertingSuggestion(true);
    try {
      const citations = new Map((latest.citations || []).map((item) => [item.citation_number, item]));
      const referenced: PlateNode[] = [];
      for (const match of latest.content.matchAll(/\[(\d+)\]/g)) {
        const citationNumber = Number(match[1]);
        const citation = citations.get(citationNumber);
        if (!citation?.snapshot) continue;
        const item = citation.snapshot;
        referenced.push({
          id: createClientId(), type: 'knowledge_citation', citation_label: `[${citationNumber}]`,
          source_title: item.title, chunk_id: citation.chunk_id, query_run_id: citation.query_run_id,
          source_id: item.document_id, source_version: item.version_id,
          source_locator: { page_number: item.page_number, structural_path: item.structural_path },
          freshness_status: 'current', children: [{ text: '' }],
        });
      }
      for (const reference of referenced) {
        await api(`/writing/documents/${document.id}/bindings`, {
          method: 'POST',
          body: {
            block_id: reference.id, block_type: reference.type, source_type: 'policy_document',
            source_id: reference.source_id, source_version: reference.source_version,
            knowledge_product_release_id: project.knowledge_product_release_id,
            chunk_id: reference.chunk_id, query_run_id: reference.query_run_id,
            content_hash: await sha256(reference), block_content: reference,
            verification_status: 'verified', freshness_status: 'current',
            metadata: { inserted_from: 'writing_agent', project_id: project.id },
          },
        });
      }
      onInsert({ kind: 'markdown-suggestion', markdown: latest.content, references: referenced });
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : '插入带依据的修订建议失败');
    } finally {
      setInsertingSuggestion(false);
    }
  };

  return <div className="assistant-content writing-agent"><div className="assistant-title"><h3>妙笔助手</h3><div className="agent-title-actions"><span className={running ? 'agent-status running' : 'agent-status'}>{stage}{(running || elapsedSeconds > 0) ? ` · ${elapsedSeconds} 秒` : ''}</span><button type="button" className="text-button" disabled={running} onClick={() => void createSession(true).catch((reason) => onError(reason instanceof Error ? reason.message : '新建对话失败'))}>新对话</button></div></div>{events.length > 0 && <div className="agent-timeline" aria-label="Agent 可核验执行过程">{events.map((item, index) => <div key={`${item.sequence || index}-${item.event_type}`}><i className={['turn_completed','tool_finished','retrieval_ranked'].includes(item.event_type) ? 'done' : ''} /><span>{agentEventLabel(item)}</span><small>{eventDuration(item)}</small></div>)}</div>}<div className="agent-messages">{messages.filter((item) => item.content || item.status !== 'completed').map((item, index) => <article key={`${item.id}-${index}`} className={item.role}><b>{item.role === 'user' ? '我' : '妙笔'}</b><p>{item.content || (item.status === 'generating' ? '正在组织内容…' : item.error_message || '未生成内容')}</p>{item.status !== 'completed' && <small>{item.status === 'generating' ? '生成中' : item.status === 'cancelled' ? '已停止' : '失败'}</small>}</article>)}{!messages.length && <div className="empty-mini">助手将调用真实知识、推演和计算工具，输出只作为待确认的修订建议。</div>}</div>{latest && !running && <button type="button" className="insert-suggestion" disabled={!document || insertingSuggestion} onClick={() => void insertSuggestion()}>{insertingSuggestion ? '正在绑定依据…' : '插入为修订建议'}</button>}<div className="agent-input"><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder="说明希望撰写或修改的内容" /><button type="button" disabled={!session || (!running && !input.trim())} onClick={() => running ? void stop() : void send()}>{running ? <Square size={16} /> : <Send size={16} />}{running ? '停止' : '发送'}</button></div></div>;
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

function EvidencePanel({ project, document, selectedBinding, onInsert, onError }: { project: Project; document: WritingDocument | null; selectedBinding: Record<string, unknown> | null; onInsert: (node: EditorInsertion) => void; onError: (message: string) => void }) {
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [result, setResult] = useState<KnowledgeSearchResponse | null>(null);
  const [inserting, setInserting] = useState('');
  const [fragment, setFragment] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    setFragment(null);
    if (!selectedBinding || String(selectedBinding.type) !== 'knowledge_citation') return;
    const chunkId = String(selectedBinding.chunk_id || '');
    const queryRunId = String(selectedBinding.query_run_id || '');
    if (!chunkId) return;
    const sourcePath = queryRunId ? `/writing/projects/${project.id}/knowledge/fragments/${chunkId}?query_run_id=${encodeURIComponent(queryRunId)}` : `/writing/projects/${project.id}/chapter-evidence/source/${chunkId}`;
    api<Record<string, unknown>>(sourcePath)
      .then(setFragment)
      .catch((reason) => onError(reason instanceof Error ? reason.message : '来源片段加载失败'));
  }, [project.id, selectedBinding]);

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
    const reference: PlateNode = {
      id: createClientId(),
      type: 'knowledge_citation',
      citation_label: `[${item.rank}]`,
      source_title: item.title,
      chunk_id: item.chunk_id,
      query_run_id: result.query_id,
      source_id: item.document_id,
      source_version: item.version_id,
      source_locator: { page_number: item.page_number, structural_path: item.structural_path },
      freshness_status: 'current',
      children: [{ text: '' }],
    };
    const block: PlateNode = { id: createClientId(), type: 'p', children: [{ text: evidenceText ? `${evidenceText} ` : '' }, reference] };
    try {
      await api(`/writing/documents/${document.id}/bindings`, {
        method: 'POST',
        body: {
          block_id: reference.id,
          block_type: reference.type,
          source_type: 'policy_document',
          source_id: item.document_id,
          source_version: item.version_id,
          knowledge_product_release_id: project.knowledge_product_release_id,
          chunk_id: item.chunk_id,
          query_run_id: result.query_id,
          content_hash: await sha256(reference),
          block_content: reference,
          verification_status: 'verified',
          freshness_status: 'current',
          metadata: { rank: item.rank, channels: item.channels, fused_score: item.fused_score },
        },
      });
      onInsert(block);
    } catch (reason) { onError(reason instanceof Error ? reason.message : '插入引用失败'); }
    finally { setInserting(''); }
  };

  return <div className="assistant-content"><h3>引用依据</h3>{document && <ParagraphEvidenceList documentId={document.id} versionId={document.current_version_id || undefined} projectId={project.id} />}{selectedBinding && <BindingInspector binding={selectedBinding} detail={fragment} />}<p>检索范围固定为当前方案任务锁定的知识产品版本。</p><div className="evidence-search"><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void search(); }} placeholder="检索本章所需依据" /><button type="button" disabled={!query.trim() || searching} onClick={() => void search()}>{searching ? '检索中…' : '检索'}</button></div>{result?.warnings?.map((warning) => <div className="warning-mini" key={warning}>{warning}</div>)}<div className="evidence-list">{result?.items.map((item) => <article key={item.chunk_id}><div><span className="rank">{item.rank}</span><b>{item.title}</b></div><p>{cleanEvidenceText(item.snippet || item.text || '无摘要')}</p><small>{item.page_number ? `第 ${item.page_number} 页 · ` : ''}{item.channels.join(' / ')} · 融合分 {Number(item.fused_score || 0).toFixed(4)}</small><button type="button" disabled={!document || !!inserting} onClick={() => void insert(item)}>{!document ? '请先创建文稿' : inserting === item.chunk_id ? '插入中…' : '插入正文'}</button></article>)}{result && !result.items.length && <div className="empty-mini">当前锁定版本中没有检索到依据。</div>}</div></div>;
}

function BindingInspector({ binding, detail }: { binding: Record<string, unknown>; detail?: Record<string, unknown> | null }) {
  const children = Array.isArray(binding.children) ? binding.children as Array<Record<string, unknown>> : [];
  const text = children.map((item) => String(item.text || '')).join('');
  const locator = (detail?.source_span || binding.source_locator || {}) as Record<string, unknown>;
  const evidenceIds = Array.isArray(binding.evidence_ids) ? binding.evidence_ids : [];
  const sourceVersion = displaySourceVersion(detail?.document_version || binding.source_version);
  const structuralPath = displayStructuralPath(detail?.structural_path || locator.structural_path);
  return <section className="binding-inspector" aria-label="当前正文依据"><div className="binding-inspector-title"><span>当前正文标记</span><b>{String(binding.label || '可信内容')}</b></div><p>{String(detail?.text || text || '正在读取完整依据…')}</p><dl><div><dt>来源</dt><dd>{String(detail?.document_title || binding.source_title || sourceLabel(String(binding.type || '')))}</dd></div>{Boolean(detail?.page_number || locator.page_number) && <div><dt>位置</dt><dd>第 {String(detail?.page_number || locator.page_number)} 页</dd></div>}{Boolean(structuralPath) && <div><dt>结构</dt><dd>{structuralPath}</dd></div>}{Boolean(binding.formula) && <div><dt>公式</dt><dd>{binding.formula === 'resource_gap' ? '缺口 = max(0, 需求 − 可用)' : String(binding.formula)}</dd></div>}{Boolean(sourceVersion) && <div><dt>版本</dt><dd>{sourceVersion}</dd></div>}<div><dt>状态</dt><dd>{binding.freshness_status === 'current' ? '依据有效' : '需要核验或更新'}</dd></div>{evidenceIds.length > 0 && <div><dt>前提</dt><dd>{evidenceIds.length} 条已绑定事实</dd></div>}</dl></section>;
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

function statusLabel(value: string) { return ({ draft: '准备中', preparing: '数据准备', ready: '任务已创建', reasoning: '推演中', writing: '撰写中', reviewing: '审校中', published: '已发布' } as Record<string,string>)[value] || value; }
function sourceLabel(value: string) { return ({ official_brief: '官方简报', policy_document: '预案原文', database_query: '实时数据库', computation: '确定性测算', computed_metric: '确定性测算', inference_conclusion: '规则结论', alternative_plan: '已采用方案', knowledge_citation: '来源文档', semantica_inference: '规则推演', model_extraction: '模型抽取', manual_input: '人工补充', manual_override: '人工覆盖' } as Record<string,string>)[value] || value; }
function formatValue(value: Record<string, unknown>) {
  if (typeof value.boolean === 'boolean') return value.boolean ? '成立' : '不成立';
  return String(value.number ?? value.text ?? value.value ?? '—');
}
