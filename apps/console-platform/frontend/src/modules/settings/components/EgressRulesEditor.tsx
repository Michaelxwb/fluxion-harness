import { Button, Input, InputNumber, Modal, Select, Switch, Table, Tag } from '@douyinfe/semi-ui';
import { IconDelete, IconEdit, IconPlus } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';
import { useEffect, useMemo, useState, type ReactNode } from 'react';

import { listPlatforms, type PlatformItem } from '../../project-platform/services/platforms';

type Rule = {
  method: string;
  path_template: string;
  target_instance_id: string;
  max_requests?: number;
  window_requests?: number;
  window_seconds?: number;
  max_inflight?: number;
  enabled?: boolean;
};

const EMPTY_RULE: Rule = { method: 'GET', path_template: '/', target_instance_id: '', enabled: true };

function readRules(value: unknown): Rule[] {
  if (typeof value !== 'string') return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
function Field({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return <label className="egress-rule-field"><span className="egress-rule-label">{label}</span><span className="egress-rule-hint">{hint}</span>{children}</label>;
}
export function EgressRulesEditor({ value, disabled, onChange }: { value: unknown; disabled?: boolean; onChange(value: string): void }) {
  const { t } = useTranslation();
  const [platforms, setPlatforms] = useState<PlatformItem[]>([]);
  const [platformsError, setPlatformsError] = useState(false);
  const [reload, setReload] = useState(0);
  const [platformsLoading, setPlatformsLoading] = useState(true);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [draft, setDraft] = useState<Rule>(EMPTY_RULE);
  const rules = readRules(value);

  useEffect(() => {
    let active = true;
    setPlatformsLoading(true);
    setPlatformsError(false);
    const load = async () => {
      const first = await listPlatforms({ page: 1, page_size: 100, enabled: true });
      const items = [...first.items];
      for (let page = 2; page <= Math.ceil(first.total / first.page_size); page += 1) {
        const next = await listPlatforms({ page, page_size: first.page_size, enabled: true });
        items.push(...next.items);
      }
      return items;
    };
    void load().then((items) => { if (active) setPlatforms(items); }).catch(() => { if (active) { setPlatforms([]); setPlatformsError(true); } }).finally(() => { if (active) setPlatformsLoading(false); });
    return () => { active = false; };
  }, [reload]);

  const platformOptions = useMemo(() => {
    const known = platforms.map((platform) => ({ value: platform.key, label: `${platform.name} (${platform.key})`, disabled: !platform.resolver_config.base_url }));
    const missing = rules.map((rule) => rule.target_instance_id).filter((key) => key && !platforms.some((platform) => platform.key === key));
    return [...known, ...Array.from(new Set(missing)).map((key) => ({ value: key, label: t('settings.egress.platformMissing', { key }), disabled: true }))];
  }, [platforms, rules, t]);

  const writeRules = (next: Rule[]) => onChange(JSON.stringify(next.map((rule) => ({ ...rule, route_id: `${rule.method} ${rule.path_template}` }))));
  const openNew = () => { setDraft({ ...EMPTY_RULE }); setEditingIndex(-1); };
  const openEdit = (index: number) => { setDraft({ ...rules[index] }); setEditingIndex(index); };
  const closeEditor = () => setEditingIndex(null);
  const saveDraft = () => {
    const next = [...rules];
    if (editingIndex === -1) next.push(draft); else if (editingIndex !== null) next[editingIndex] = draft;
    writeRules(next);
    closeEditor();
  };
  const base = platforms.find((item) => item.key === draft.target_instance_id)?.resolver_config.base_url;
  const preview = typeof base === 'string' ? base.replace(/\/+$/, '') + draft.path_template : t('settings.egress.selectPlatformFirst');

  return <div className="egress-rules-editor" data-testid="egress-rules-editor">
    <div className="egress-rules-help">{t('settings.egress.help')}</div>
    {platformsError && <div role="alert" className="egress-rules-error">{t('settings.egress.platformLoadFailed')} <Button onClick={() => setReload((v) => v + 1)}>{t('settings.egress.retry')}</Button></div>}
    <div className="egress-rules-toolbar">
      <span className="egress-rules-count">{t('settings.egress.ruleCount', { count: rules.length })}</span>
      <Button theme="solid" icon={<IconPlus />} disabled={disabled} onClick={openNew} data-testid="egress-rule-add">{t('settings.egress.add')}</Button>
    </div>
    <Table dataSource={rules.map((rule, index) => ({ ...rule, key: index, index }))} pagination={false} empty={t('settings.egress.empty')} className="egress-rules-table">
      <Table.Column title={t('settings.egress.target')} dataIndex="target_instance_id" render={(key) => platforms.find((platform) => platform.key === key)?.name ?? key} />
      <Table.Column title={t('settings.egress.method')} dataIndex="method" />
      <Table.Column title={t('settings.egress.path')} dataIndex="path_template" />
      <Table.Column title={t('settings.egress.perMinute')} dataIndex="window_requests" render={(value) => value ?? t('settings.egress.unlimited')} />
      <Table.Column title={t('settings.egress.concurrency')} dataIndex="max_inflight" render={(value) => value ?? t('settings.egress.unlimited')} />
      <Table.Column title={t('settings.egress.enabled')} dataIndex="enabled" render={(enabled) => <Tag color={enabled === false ? 'grey' : 'green'}>{enabled === false ? t('settings.egress.disabled') : t('settings.egress.active')}</Tag>} />
      <Table.Column title={t('settings.egress.actions')} render={(_, row: { index: number }) => <div className="egress-rule-row-actions"><Button theme="borderless" icon={<IconEdit />} disabled={disabled} aria-label={t('settings.egress.edit')} onClick={() => openEdit(row.index)} /><Button theme="borderless" type="danger" icon={<IconDelete />} disabled={disabled} aria-label={t('settings.egress.remove')} onClick={() => writeRules(rules.filter((_, index) => index !== row.index))} /></div>} />
    </Table>
    <Modal visible={editingIndex !== null} title={editingIndex === -1 ? t('settings.egress.add') : t('settings.egress.edit')} onCancel={closeEditor} onOk={saveDraft} okText={t('common.save')} cancelText={t('common.cancel')} width={720} className="egress-rule-modal">
      <div className="egress-rule-form">
        <Field label={t('settings.egress.target')} hint={t('settings.egress.targetHint')}><Select value={draft.target_instance_id || undefined} disabled={platformsLoading} loading={platformsLoading} placeholder={t('settings.egress.targetPlaceholder')} optionList={platformOptions} onChange={(v) => setDraft({ ...draft, target_instance_id: String(v) })} /></Field>
        <Field label={t('settings.egress.method')} hint={t('settings.egress.methodHint')}><Select value={draft.method} optionList={['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].map((method) => ({ value: method, label: method }))} onChange={(v) => setDraft({ ...draft, method: String(v) })} /></Field>
        <Field label={t('settings.egress.path')} hint={t('settings.egress.pathHint')}><Input value={draft.path_template} placeholder="/assets/{id}" onChange={(v) => setDraft({ ...draft, path_template: v })} /></Field>
        <Field label={t('settings.egress.perMinute')} hint={t('settings.egress.perMinuteHint')}><InputNumber value={draft.window_requests} min={1} placeholder={t('settings.egress.unlimited')} onChange={(v) => setDraft({ ...draft, window_requests: typeof v === 'number' ? v : undefined, window_seconds: typeof v === 'number' ? 60 : undefined })} /></Field>
        <Field label={t('settings.egress.concurrency')} hint={t('settings.egress.concurrencyHint')}><InputNumber value={draft.max_inflight} min={1} placeholder={t('settings.egress.unlimited')} onChange={(v) => setDraft({ ...draft, max_inflight: typeof v === 'number' ? v : undefined })} /></Field>
        <label className="egress-rule-enabled"><span>{t('settings.egress.enabled')}</span><Switch checked={draft.enabled !== false} onChange={(enabled) => setDraft({ ...draft, enabled })} /></label>
      </div>
      <div className="egress-rules-help" data-testid="egress-url-preview">{t('settings.egress.preview')}: {preview}</div>
    </Modal>
  </div>;
}
