export type PlateNode = Record<string, unknown> & { children?: PlateNode[]; text?: string };

export type ScenarioVersion = {
  id: string;
  version: number;
  status: string;
  chapter_template: { chapters?: Array<{ key: string; title: string }> };
  config: { business_validation?: string; minimum_plan_count?: number; default_plan_count?: number };
};

export type ScenarioPackage = {
  id: string;
  code: string;
  name: string;
  disaster_type: string;
  description: string;
  status: string;
  current_version_id?: string;
  versions?: ScenarioVersion[];
};

export type Project = {
  id: string;
  code: string;
  name: string;
  status: string;
  scenario_package_version_id: string;
  knowledge_product_release_id: string;
  role?: string;
  facts?: number;
  pending_gates?: number;
  config?: Record<string, unknown>;
  input_contract?: {
    required: string[];
    properties: Record<string, { type?: string; title?: string; unit?: string; minimum?: number; maximum?: number }>;
    chapters: Array<{ key: string; title: string }>;
  };
};

export type KnowledgeContextSpace = {
  id: string;
  name: string;
  code: string;
  knowledge_release_id: string;
  knowledge_release_number: number;
  published_at?: string;
  status: string;
  document_count: number;
  chunk_count: number;
  entity_count: number;
  fact_count: number;
  graph_available: boolean;
  vector_available: boolean;
};

export type KnowledgeContext = {
  product: { id: string; name: string; code: string };
  release: {
    id: string;
    version: number;
    checksum: string;
    published_at?: string;
    status: string;
    is_latest: boolean;
  };
  spaces: KnowledgeContextSpace[];
  snapshot_locked: boolean;
  document_count: number;
  chunk_count: number;
  entity_count: number;
  fact_count: number;
};

export type WritingDocument = {
  id: string;
  project_id: string;
  title: string;
  status: string;
  current_version?: {
    id: string;
    version: number;
    content: PlateNode[];
    content_hash: string;
    change_summary?: string;
  };
};

export type WritingGenerationRun = {
  id: string;
  project_id: string;
  document_id: string;
  status: 'queued' | 'running' | 'awaiting_agent' | 'agent_running' | 'completed' | 'agent_failed' | 'quality_failed' | 'cancelled';
  stage: string;
  progress: number;
  toolbox_result?: {
    criteria?: { items?: Fact[] };
    reasoning?: { conclusions?: Fact[]; requires_human_confirmation?: boolean };
    computations?: Array<ComputationRun & { generated_fact?: Fact }>;
    plans?: AlternativePlan[];
    selected_plan_id?: string;
  };
  quality_report?: {
    ok?: boolean;
    issues?: Array<{ code: string; severity: string; message: string }>;
    metrics?: Record<string, unknown>;
  };
  error_message?: string;
  document?: WritingDocument;
  agent_session?: WritingAgentSession;
};

export type WritingInputChange = {
  id: string;
  project_id: string;
  document_id: string;
  status: 'preview' | 'applied' | 'cancelled' | 'superseded';
  changes: Array<{ fact_key: string; label: string; old_value: Record<string, unknown>; new_value: Record<string, unknown>; unit?: string }>;
  impact: {
    calculations?: Array<{ result_key: string; label: string; old_value: number; new_value: number; unit?: string }>;
    report_blocks?: Array<{ block_id: string; section: string }>;
    unaffected_results?: Array<{ result_key: string; label: string; value: number; unit?: string }>;
    automatic_overwrite?: boolean;
  };
};

export type CollaborationAccess = {
  token: string;
  room: string;
  url: string;
  expires_at: string;
  role: 'viewer' | 'commenter' | 'editor' | 'reviewer' | 'publisher' | 'owner';
  read_only: boolean;
  user: { id: string; name: string };
};

export type WritingComment = {
  id: string;
  document_id: string;
  thread_id: string;
  parent_id?: string;
  block_id?: string;
  content: string;
  status: 'open' | 'resolved';
  created_at: string;
  author: { id: string; name: string };
};

export type Fact = {
  id: string;
  fact_key: string;
  label: string;
  value: Record<string, unknown>;
  unit?: string;
  source_type: string;
  verification_status: string;
  freshness_status: string;
  fact_type: string;
  source_id?: string;
  source_version?: string;
  source_locator?: Record<string, unknown>;
  version: number;
};

export type ComputationRun = {
  id: string;
  status: string;
  inputs: Record<string, unknown>;
  result: {
    operation: string;
    value: number;
    dependencies?: Record<string, string>;
    output_fact?: { fact_key?: string; label?: string; unit?: string };
  };
  input_fact_ids: string[];
  checksum: string;
  created_at: string;
};

export type DecisionGate = {
  id: string;
  gate_key: string;
  name: string;
  required: boolean;
  status: string;
};

export type ExportJob = {
  id: string;
  output_format: 'docx' | 'evidence_docx' | 'pdf' | 'json' | 'xlsx' | 'geojson';
  status: string;
  progress: number;
  checksum?: string;
  created_at: string;
  manifest?: { filename?: string; document_version?: number };
  error_message?: string;
};

export type AlternativePlan = {
  id: string;
  plan_key: string;
  name: string;
  status: string;
  result: { route?: { path: string[]; minutes: number; risk: number }; resource_allocation?: unknown[] };
  unresolved_gaps: Array<{ resource: string; gap: number; unit: string }>;
};

export type KnowledgeResult = {
  rank: number;
  chunk_id: string;
  document_id: string;
  version_id: string;
  title: string;
  text?: string;
  snippet?: string;
  page_number?: number;
  structural_path?: string;
  channels: string[];
  fused_score: number;
  rerank_score?: number;
  historical_snapshot?: boolean;
};

export type KnowledgeSearchResponse = {
  query_id: string;
  knowledge_product_release_id: string;
  snapshot_locked: boolean;
  items: KnowledgeResult[];
  warnings: string[];
  trace_summary: Record<string, unknown>;
};

export type AgentMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  status: string;
  error_message?: string;
  citations?: Array<{
    citation_number: number;
    chunk_id: string;
    query_run_id?: string;
    rank: number;
    snapshot?: KnowledgeResult;
  }>;
};

export type AgentEvent = {
  id?: string;
  sequence?: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

export type WritingAgentSession = {
  id: string;
  project_id: string;
  document_id?: string;
  conversation_id: string;
  status: string;
  conversation?: { messages?: AgentMessage[]; events?: AgentEvent[] };
};
