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
};

export type WritingDocument = {
  id: string;
  project_id: string;
  title: string;
  status: string;
  current_version?: { id: string; version: number; content: PlateNode[]; content_hash: string };
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
};

export type WritingAgentSession = {
  id: string;
  project_id: string;
  document_id?: string;
  conversation_id: string;
  status: string;
  conversation?: { messages?: AgentMessage[]; events?: Array<{ event_type: string; payload: Record<string, unknown> }> };
};
