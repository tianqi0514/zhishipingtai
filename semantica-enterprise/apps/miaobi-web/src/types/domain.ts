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
