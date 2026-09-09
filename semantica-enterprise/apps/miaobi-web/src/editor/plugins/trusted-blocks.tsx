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
  const element = props.element as Record<string, unknown>;
  const type = String(element.type || '');
  const item = META[type] || { label: '可信内容', tone: 'gray' };
  const freshness = String(element.freshness_status || 'unverified');
  const locked = lockedTrustedBlockTypes.includes(type);
  const openDetails = () => window.dispatchEvent(new CustomEvent('miaobi:open-binding', { detail: { ...element, label: item.label } }));
  if (type === 'knowledge_citation') {
    return (
      <PlateElement {...props} as="span" className={`trusted-block trusted-reference tone-${item.tone} state-${freshness}`}>
        <span className="trusted-content">{children}</span>
        <button type="button" className="trusted-marker" contentEditable={false} onMouseDown={(event) => event.preventDefault()} onClick={openDetails} aria-label={`查看${item.label}依据`} title="在右侧查看完整依据">
          {String(element.citation_label || '依据')}
        </button>
        {freshness !== 'current' && <button type="button" className="trusted-freshness" contentEditable={false} onMouseDown={(event) => event.preventDefault()} onClick={openDetails}>{freshness === 'stale' ? '需更新' : '待核验'}</button>}
      </PlateElement>
    );
  }
  return (
    <PlateElement {...props} className={`trusted-block tone-${item.tone} state-${freshness}`}>
      <span className="trusted-content" contentEditable={locked ? false : undefined}>{children}</span>
      <button type="button" className="trusted-marker" contentEditable={false} onMouseDown={(event) => event.preventDefault()} onClick={openDetails} aria-label={`查看${item.label}依据`} title="在右侧查看完整依据">
        {type === 'knowledge_citation' ? '依据' : type === 'computed_metric' ? '测算' : type === 'inference_conclusion' ? '推演' : item.label}
      </button>
      {freshness !== 'current' && <button type="button" className="trusted-freshness" contentEditable={false} onMouseDown={(event) => event.preventDefault()} onClick={openDetails}>{freshness === 'stale' ? '需更新' : '待核验'}</button>}
    </PlateElement>
  );
}

export const TrustedBlockKit = Object.keys(META).map((key) => createPlatePlugin({
  key,
  node: key === 'knowledge_citation'
    ? { isElement: true, isInline: true, isVoid: true }
    : { isElement: true },
}).withComponent(TrustedBlock));

export const trustedBlockTypes = Object.keys(META);
