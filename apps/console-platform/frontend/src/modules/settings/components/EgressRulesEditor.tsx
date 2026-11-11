import { Button, Card, Input, InputNumber, Switch } from '@douyinfe/semi-ui';
import { IconDelete, IconPlus } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';

type Rule = {
  origin: string;
  method: string;
  path_template: string;
  target_instance_id: string;
  route_id: string;
  max_requests?: number;
  window_requests?: number;
  window_seconds?: number;
  max_inflight?: number;
  enabled?: boolean;
};

function readRules(value: unknown): Rule[] {
  if (typeof value !== 'string') return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function EgressRulesEditor({
  value,
  disabled,
  onChange
}: {
  value: unknown;
  disabled?: boolean;
  onChange(value: string): void;
}) {
  const { t } = useTranslation();
  const rules = readRules(value);
  const update = (next: Rule[]) => onChange(JSON.stringify(next));
  const setRule = (index: number, patch: Partial<Rule>) =>
    update(rules.map((rule, current) => (current === index ? { ...rule, ...patch } : rule)));
  const add = () =>
    update([
      ...rules,
      {
        origin: 'https://',
        method: 'GET',
        path_template: '/',
        target_instance_id: '',
        route_id: '',
        enabled: true
      }
    ]);
  return (
    <div className="egress-rules-editor" data-testid="egress-rules-editor">
      {rules.map((rule, index) => (
        <Card key={`${index}-${rule.route_id}`} className="egress-rule-card" bordered>
          <div className="egress-rule-grid">
            <Input value={rule.origin} disabled={disabled} placeholder="https://api.example.com" onChange={(v) => setRule(index, { origin: v })} />
            <Input value={rule.method} disabled={disabled} placeholder="GET" onChange={(v) => setRule(index, { method: v.toUpperCase() })} />
            <Input value={rule.path_template} disabled={disabled} placeholder="/assets/{id}" onChange={(v) => setRule(index, { path_template: v })} />
            <Input value={rule.target_instance_id} disabled={disabled} placeholder={t('settings.egress.target')} onChange={(v) => setRule(index, { target_instance_id: v })} />
            <Input value={rule.route_id} disabled={disabled} placeholder={t('settings.egress.route')} onChange={(v) => setRule(index, { route_id: v })} />
            <InputNumber value={rule.window_requests} min={1} disabled={disabled} placeholder={t('settings.egress.perMinute')} onChange={(v) => setRule(index, { window_requests: typeof v === 'number' ? v : undefined, window_seconds: 60 })} />
            <InputNumber value={rule.max_inflight} min={1} disabled={disabled} placeholder={t('settings.egress.concurrency')} onChange={(v) => setRule(index, { max_inflight: typeof v === 'number' ? v : undefined })} />
            <Switch checked={rule.enabled !== false} disabled={disabled} onChange={(v) => setRule(index, { enabled: v })} />
            <Button theme="borderless" type="danger" icon={<IconDelete />} disabled={disabled} aria-label={t('settings.egress.remove')} onClick={() => update(rules.filter((_, current) => current !== index))} />
          </div>
        </Card>
      ))}
      <Button icon={<IconPlus />} disabled={disabled} onClick={add} data-testid="egress-rule-add">
        {t('settings.egress.add')}
      </Button>
    </div>
  );
}
