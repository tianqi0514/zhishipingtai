import { useEffect, useState } from 'react';
import { CheckCircle2, Plus, Trash2 } from 'lucide-react';
import { api } from '../api';

type InputSetting = {
  key: string;
  label: string;
  data_type: 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array';
  unit?: string | null;
  required: boolean;
  confirmation_required: boolean;
  source_guidance: string;
  minimum?: number | null;
  maximum?: number | null;
};

type SectionSetting = {
  key: string;
  title: string;
  purpose: string;
  generation_mode: 'agent' | 'toolbox' | 'mixed' | 'manual';
  required_inputs: string[];
  toolbox_outputs: string[];
  citation_required: boolean;
};

type BusinessConfig = {
  inputs: InputSetting[];
  sections: SectionSetting[];
  toolbox: {
    reasoning_enabled: boolean;
    rule_set_ids: string[];
    calculation_enabled: boolean;
    formula_ids: string[];
    target_sections: Record<string, string[]>;
  };
  writing_policy: {
    missing_input_action: 'block' | 'warn';
    unverified_fact_action: 'block' | 'warn';
    require_citations: boolean;
    allow_manual_override: boolean;
  };
  output: { title_pattern: string; allowed_formats: Array<'docx' | 'pdf' | 'json' | 'xlsx' | 'geojson'> };
  decision_gates: Array<{ key: string; name: string; required: boolean }>;
  comparison_dimensions: Array<Record<string, unknown>>;
  minimum_plan_count: number;
  default_plan_count: number;
  activate: boolean;
};

type ConfigResponse = {
  package: { name: string };
  version: { version: number; status: string };
  config: BusinessConfig;
  setting_effects: Array<{ setting: string; runtime_effect: string }>;
};

function keyFrom(prefix: string, index: number) {
  return `${prefix}_${index + 1}`;
}

const TOOLBOX_OUTPUTS: Array<[string, string]> = [
  ['disaster_grade', '灾害等级结论'],
  ['rescue_gap', '搜救人员缺口'],
  ['county_bed_gap', '县域床位缺口'],
  ['all_area_bed_gap', '全域床位缺口'],
  ['tents_gap', '帐篷缺口'],
  ['selected_plan', '采用方案'],
];

export function ScenarioConfigDialog({ packageId, onClose, onSaved, onError }: {
  packageId: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [data, setData] = useState<ConfigResponse | null>(null);
  const [section, setSection] = useState<'inputs' | 'sections' | 'toolbox' | 'publish'>('inputs');
  const [saving, setSaving] = useState(false);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);

  useEffect(() => {
    api<ConfigResponse>(`/writing/scenario-packages/${packageId}/business-config`)
      .then(setData)
      .catch((reason) => onError(reason instanceof Error ? reason.message : '场景配置加载失败'));
  }, [packageId]);

  const update = (next: BusinessConfig) => setData((current) => current ? { ...current, config: next } : current);
  const save = async () => {
    if (!data || saving) return;
    setSaving(true);
    try {
      const payload = { ...data.config, activate: true };
      await api(`/writing/scenario-packages/${packageId}/business-config/validate`, { method: 'POST', body: payload });
      const result = await api<{ version: { version: number } }>(`/writing/scenario-packages/${packageId}/business-config`, { method: 'PUT', body: payload });
      setSavedVersion(result.version.version);
      await onSaved();
    } catch (reason) { onError(reason instanceof Error ? reason.message : '场景配置保存失败'); }
    finally { setSaving(false); }
  };
  if (!data) return <div className="dialog-backdrop"><section className="dialog scenario-config-dialog"><p>正在加载场景配置…</p></section></div>;
  const config = data.config;
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}>
    <section className="dialog scenario-config-dialog" role="dialog" aria-modal="true" aria-label="妙笔场景配置">
      <div className="dialog-head"><div><span className="eyebrow">{data.package.name} · 当前 V{data.version.version}</span><h2>写作场景配置</h2></div><button type="button" className="icon-button" disabled={saving} onClick={onClose}>×</button></div>
      <div className="scenario-config-note">这里只保留会真实影响报告生成、推演计算、审校或导出的配置。保存会发布新版本，不会静默改变已有报告。</div>
      <nav className="scenario-config-tabs">{([['inputs','报告输入'],['sections','报告结构'],['toolbox','推演与写作'],['publish','审校与导出']] as const).map(([key,label]) => <button type="button" key={key} className={section === key ? 'active' : ''} onClick={() => setSection(key)}>{label}</button>)}</nav>
      <div className="scenario-config-body">
        {section === 'inputs' && <div className="config-list"><div className="config-section-head"><div><h3>生成前需要确认什么</h3><p>必填且需要确认的输入缺失时，报告生成会被真实阻止。</p></div><button type="button" className="secondary compact" onClick={() => update({ ...config, inputs: [...config.inputs, { key: keyFrom('input', config.inputs.length), label: '新输入项', data_type: 'string', required: true, confirmation_required: true, source_guidance: '' }] })}><Plus size={14} />添加输入</button></div>{config.inputs.map((item,index) => <article className="config-row input-config-row" key={item.key}><input aria-label="输入项名称" value={item.label} onChange={(event) => update({ ...config, inputs: config.inputs.map((row,i) => i === index ? { ...row, label: event.target.value } : row) })} /><select aria-label="输入项类型" value={item.data_type} onChange={(event) => update({ ...config, inputs: config.inputs.map((row,i) => i === index ? { ...row, data_type: event.target.value as InputSetting['data_type'] } : row) })}><option value="string">文本</option><option value="number">数值</option><option value="integer">整数</option><option value="boolean">是/否</option><option value="object">结构化对象</option><option value="array">列表</option></select><input aria-label="单位" placeholder="单位" value={item.unit || ''} onChange={(event) => update({ ...config, inputs: config.inputs.map((row,i) => i === index ? { ...row, unit: event.target.value } : row) })} /><label><input type="checkbox" checked={item.required} onChange={(event) => update({ ...config, inputs: config.inputs.map((row,i) => i === index ? { ...row, required: event.target.checked } : row) })} />必填</label><label><input type="checkbox" checked={item.confirmation_required} onChange={(event) => update({ ...config, inputs: config.inputs.map((row,i) => i === index ? { ...row, confirmation_required: event.target.checked } : row) })} />人工确认</label><button type="button" className="icon-button danger-icon" title="删除输入项" onClick={() => update({ ...config, inputs: config.inputs.filter((_,i) => i !== index) })}><Trash2 size={15} /></button></article>)}</div>}
        {section === 'sections' && <div className="config-list"><div className="config-section-head"><div><h3>报告包含哪些章节</h3><p>章节用途会成为 Agent 的写作目标；生成方式决定是否允许自动写作。</p></div><button type="button" className="secondary compact" onClick={() => update({ ...config, sections: [...config.sections, { key: keyFrom('section', config.sections.length), title: '新章节', purpose: '说明本章节需要回答的业务问题。', generation_mode: 'agent', required_inputs: [], toolbox_outputs: [], citation_required: true }] })}><Plus size={14} />添加章节</button></div>{config.sections.map((item,index) => <article className="config-row section-config-row" key={item.key}><div><input aria-label="章节标题" value={item.title} onChange={(event) => update({ ...config, sections: config.sections.map((row,i) => i === index ? { ...row, title: event.target.value } : row) })} /><textarea aria-label="章节用途" value={item.purpose} onChange={(event) => update({ ...config, sections: config.sections.map((row,i) => i === index ? { ...row, purpose: event.target.value } : row) })} /></div><select aria-label="生成方式" value={item.generation_mode} onChange={(event) => update({ ...config, sections: config.sections.map((row,i) => i === index ? { ...row, generation_mode: event.target.value as SectionSetting['generation_mode'] } : row) })}><option value="agent">Agent 写作</option><option value="toolbox">推演结果生成</option><option value="mixed">推演结果 + Agent</option><option value="manual">人工撰写</option></select><label><input type="checkbox" checked={item.citation_required} onChange={(event) => update({ ...config, sections: config.sections.map((row,i) => i === index ? { ...row, citation_required: event.target.checked } : row) })} />必须有依据</label><button type="button" className="icon-button danger-icon" title="删除章节" onClick={() => update({ ...config, sections: config.sections.filter((_,i) => i !== index) })}><Trash2 size={15} /></button></article>)}</div>}
        {section === 'toolbox' && <div className="config-toolbox-layout"><div className="config-cards"><label className="config-toggle-card"><input type="checkbox" checked={config.toolbox.reasoning_enabled} onChange={(event) => update({ ...config, toolbox: { ...config.toolbox, reasoning_enabled: event.target.checked } })} /><span><b>运行规则推演</b><small>生成报告前执行已绑定规则；关闭后不会产生正式推演结论。</small></span></label><label className="config-toggle-card"><input type="checkbox" checked={config.toolbox.calculation_enabled} onChange={(event) => update({ ...config, toolbox: { ...config.toolbox, calculation_enabled: event.target.checked } })} /><span><b>运行确定性计算</b><small>根据已确认输入执行公式；关闭后不会生成测算值。</small></span></label><label className="config-toggle-card"><input type="checkbox" checked={config.writing_policy.allow_manual_override} onChange={(event) => update({ ...config, writing_policy: { ...config.writing_policy, allow_manual_override: event.target.checked } })} /><span><b>允许人工修正输入</b><small>关闭后，前端不再显示修正入口，后端也会拒绝覆盖请求。</small></span></label></div><section className="toolbox-targets"><div className="config-section-head"><div><h3>结果放到报告哪里</h3><p>计算和推演完成后，服务端会把可信结果块插入指定章节。</p></div></div>{TOOLBOX_OUTPUTS.map(([outputKey,label]) => <label key={outputKey}><span>{label}</span><select value={(config.toolbox.target_sections[outputKey] || [])[0] || ''} onChange={(event) => update({ ...config, toolbox: { ...config.toolbox, target_sections: { ...config.toolbox.target_sections, [outputKey]: event.target.value ? [event.target.value] : [] } } })}><option value="">按系统默认落位</option>{config.sections.map((item) => <option key={item.key} value={item.key}>{item.title}</option>)}</select></label>)}</section></div>}
        {section === 'publish' && <div className="config-publish-grid"><section><h3>生成与发布约束</h3><label>报告标题格式<input value={config.output.title_pattern} onChange={(event) => update({ ...config, output: { ...config.output, title_pattern: event.target.value } })} /><small>使用 {'{project_name}'} 代表方案任务名称。</small></label><label>缺少必填输入时<select value={config.writing_policy.missing_input_action} onChange={(event) => update({ ...config, writing_policy: { ...config.writing_policy, missing_input_action: event.target.value as 'block' | 'warn' } })}><option value="block">阻止生成</option><option value="warn">允许生成并警告</option></select></label><label>输入尚未确认时<select value={config.writing_policy.unverified_fact_action} onChange={(event) => update({ ...config, writing_policy: { ...config.writing_policy, unverified_fact_action: event.target.value as 'block' | 'warn' } })}><option value="block">阻止生成</option><option value="warn">允许生成并警告</option></select></label><label className="config-check"><input type="checkbox" checked={config.writing_policy.require_citations} onChange={(event) => update({ ...config, writing_policy: { ...config.writing_policy, require_citations: event.target.checked } })} />发布前检查关键章节引用</label></section><section><h3>可用导出格式</h3><div className="format-checks">{(['docx','pdf','json','xlsx','geojson'] as const).map((format) => <label key={format}><input type="checkbox" checked={config.output.allowed_formats.includes(format)} onChange={(event) => update({ ...config, output: { ...config.output, allowed_formats: event.target.checked ? [...config.output.allowed_formats, format] : config.output.allowed_formats.filter((item) => item !== format) } })} />{format.toUpperCase()}</label>)}</div><h3>人工确认节点</h3>{config.decision_gates.map((gate,index) => <div className="gate-config-row" key={gate.key}><input value={gate.name} aria-label="确认节点名称" onChange={(event) => update({ ...config, decision_gates: config.decision_gates.map((row,i) => i === index ? { ...row, name: event.target.value } : row) })} /><label><input type="checkbox" checked={gate.required} onChange={(event) => update({ ...config, decision_gates: config.decision_gates.map((row,i) => i === index ? { ...row, required: event.target.checked } : row) })} />发布前必须完成</label></div>)}</section></div>}
      </div>
      {savedVersion && <div className="scenario-config-saved"><CheckCircle2 size={16} />已发布 V{savedVersion}，新建方案任务将使用该版本；已有任务继续使用原版本。</div>}
      <div className="dialog-actions"><button type="button" className="secondary" disabled={saving} onClick={onClose}>关闭</button><button type="button" className="primary" disabled={saving || !config.inputs.length || !config.sections.length || !config.output.allowed_formats.length} onClick={() => void save()}>{saving ? '检查并发布中…' : '保存并发布新版本'}</button></div>
    </section>
  </div>;
}
