import type { ReactNode } from 'react';
import { createPlatePlugin, PlateElement, type PlateElementProps } from 'platejs/react';

const META: Record<string, { label: string; tone: string }> = {
  knowledge_citation: { label: '知识引用', tone: 'blue' },
  verified_fact: { label: '已核验事实', tone: 'green' },
  computed_metric: { label: '确定性测算', tone: 'violet' },
  inference_conclusion: { label: '规则推演结论', tone: 'amber' },
  manual_assumption: { label: '人工假设', tone: 'gray' },
  decision_gate: { label: '待确认事项', tone: 'orange' },
  alternative_plan: { label: '备选方案', tone: 'indigo' },
  action_task: { label: '行动任务', tone: 'cyan' },
  data_table: { label: '数据表', tone: 'teal' },
  geo_route: { label: '调度路线', tone: 'purple' },
  risk_warning: { label: '风险提示', tone: 'red' },
};

export const lockedTrustedBlockTypes = ['computed_metric', 'inference_conclusion'];

function TrustedBlock({ children, ...props }: PlateElementProps & { children?: ReactNode }) {
  const type = String((props.element as Record<string, unknown>).type || '');
  const item = META[type] || { label: '可信内容', tone: 'gray' };
  const freshness = String((props.element as Record<string, unknown>).freshness_status || 'unverified');
  const locked = lockedTrustedBlockTypes.includes(type);
  return (
    <PlateElement {...props} className={`trusted-block tone-${item.tone} state-${freshness}`}>
      <span className="trusted-label" contentEditable={false}>{item.label}</span>
      <div className="trusted-content" contentEditable={locked ? false : undefined}>{children}</div>
      <span className="trusted-state" contentEditable={false}>
        {freshness === 'current' ? '依据有效' : freshness === 'stale' ? '依据已变化' : '待绑定依据'}
      </span>
    </PlateElement>
  );
}

export const TrustedBlockKit = Object.keys(META).map((key) =>
  createPlatePlugin({ key, node: { isElement: true } }).withComponent(TrustedBlock)
);

export const trustedBlockTypes = Object.keys(META);
