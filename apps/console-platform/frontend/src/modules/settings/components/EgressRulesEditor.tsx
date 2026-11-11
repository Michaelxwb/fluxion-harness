import { Button, Card, Input, InputNumber, Select, Switch } from '@douyinfe/semi-ui';
import { IconDelete, IconPlus } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';
import { useEffect, useMemo, useState, type ReactNode } from 'react';

import { listPlatforms, type PlatformItem } from '../../project-platform/services/platforms';

type Rule = {
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
  const [platforms, setPlatforms] = useState<PlatformItem[]>([]);
  const [platformsLoading, setPlatformsLoading] = useState(true);
  const rules = readRules(value);
  useEffect(() => {
    let active = true;
    setPlatformsLoading(true);
    void listPlatforms({ page_size: 100, enabled: true })
      .then((page) => {
        if (active) setPlatforms(page.items);
      })
      .catch(() => {
        if (active) setPlatforms([]);
      })
      .finally(() => {
        if (active) setPlatformsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  const platformOptions = useMemo(() => {
    const known = platforms.map((platform) => ({
      value: platform.key,
      label: `${platform.name}（${platform.key}）`
    }));
    const missing = rules
      .map((rule) => rule.target_instance_id)
      .filter((key) => key && !platforms.some((platform) => platform.key === key));
    return [
      ...known,
      ...Array.from(new Set(missing)).map((key) => ({ value: key, label: `${key}（未找到已配置平台）` }))
    ];
  }, [platforms, rules]);
  const update = (next: Rule[]) => onChange(JSON.stringify(next));
  const setRule = (index: number, patch: Partial<Rule>) =>
    update(rules.map((rule, current) => (current === index ? { ...rule, ...patch } : rule)));
  const add = () =>
    update([
      ...rules,
      {
        method: 'GET',
        path_template: '/',
        target_instance_id: '',
        route_id: '',
        enabled: true
      }
    ]);
  const Field = ({ label, hint, children }: { label: string; hint: string; children: ReactNode }) => (
    <label className="egress-rule-field">
      <span className="egress-rule-label">{label}</span>
      <span className="egress-rule-hint">{hint}</span>
      {children}
    </label>
  );
  return (
    <div className="egress-rules-editor" data-testid="egress-rules-editor">
      <div className="egress-rules-help">{t('settings.egress.help')}</div>
      {rules.map((rule, index) => (
        <Card key={`${index}-${rule.route_id}`} className="egress-rule-card" bordered>
          <div className="egress-rule-card-header">
            <div>
              <div className="egress-rule-card-title">{t('settings.egress.ruleTitle', { index: index + 1 })}</div>
              <div className="egress-rule-card-subtitle">{t('settings.egress.ruleSubtitle')}</div>
            </div>
            <div className="egress-rule-card-actions">
              <label className="egress-rule-enabled"><span>{t('settings.egress.enabled')}</span><Switch checked={rule.enabled !== false} disabled={disabled} onChange={(v) => setRule(index, { enabled: v })} /></label>
              <Button theme="borderless" type="danger" icon={<IconDelete />} disabled={disabled} aria-label={t('settings.egress.remove')} onClick={() => update(rules.filter((_, current) => current !== index))} />
            </div>
          </div>
          <div className="egress-rule-grid">
            <Field label={t('settings.egress.method')} hint={t('settings.egress.methodHint')}>
              <Select value={rule.method} disabled={disabled} optionList={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((method) => ({ value: method, label: method }))} onChange={(v) => setRule(index, { method: String(v) })} />
            </Field>
            <Field label={t('settings.egress.path')} hint={t('settings.egress.pathHint')}>
              <Input value={rule.path_template} disabled={disabled} placeholder="/assets/{id}" onChange={(v) => setRule(index, { path_template: v })} />
            </Field>
            <Field label={t('settings.egress.target')} hint={t('settings.egress.targetHint')}>
              <Select
                value={rule.target_instance_id || undefined}
                disabled={disabled || platformsLoading}
                loading={platformsLoading}
                placeholder={t('settings.egress.targetPlaceholder')}
                optionList={platformOptions}
                onChange={(v) => setRule(index, { target_instance_id: String(v) })}
              />
            </Field>
            <Field label={t('settings.egress.route')} hint={t('settings.egress.routeHint')}>
              <Input value={rule.route_id} disabled={disabled} placeholder="GET /assets" onChange={(v) => setRule(index, { route_id: v })} />
            </Field>
            <Field label={t('settings.egress.perMinute')} hint={t('settings.egress.perMinuteHint')}>
              <InputNumber value={rule.window_requests} min={1} disabled={disabled} placeholder={t('settings.egress.unlimited')} onChange={(v) => setRule(index, { window_requests: typeof v === 'number' ? v : undefined, window_seconds: 60 })} />
            </Field>
            <Field label={t('settings.egress.concurrency')} hint={t('settings.egress.concurrencyHint')}>
              <InputNumber value={rule.max_inflight} min={1} disabled={disabled} placeholder={t('settings.egress.unlimited')} onChange={(v) => setRule(index, { max_inflight: typeof v === 'number' ? v : undefined })} />
            </Field>
          </div>
        </Card>
      ))}
      <Button icon={<IconPlus />} disabled={disabled} onClick={add} data-testid="egress-rule-add">
        {t('settings.egress.add')}
      </Button>
    </div>
  );
}
