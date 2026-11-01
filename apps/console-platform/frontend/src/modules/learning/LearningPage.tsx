import { Banner, Button, Input, Select, Tag, Typography } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';

import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { EntityLink } from '../../components/common/EntityLink';
import { ListActionButton } from '../../components/common/ListActionButton';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { ExperienceDetailSideSheet } from './ExperienceDetailSideSheet';
import { FamilyDetailSideSheet } from './FamilyDetailSideSheet';
import { listFamilies } from './services/learning';
import type { FamilyItem } from './types';

export function LearningPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<FamilyItem[]>([]);
  const [total, setTotal] = useState(0);
  const [unclassified, setUnclassified] = useState(0);
  const [source, setSource] = useState<string | undefined>();
  const [query, setQuery] = useState('');
  const [failed, setFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const family = params.get('family');
  const experience = params.get('experience');
  const parsed = experience?.includes(':')
    ? { userId: experience.slice(0, experience.indexOf(':')), key: experience.slice(experience.indexOf(':') + 1) }
    : null;

  useEffect(() => {
    listFamilies({ source, q: query || undefined, page, page_size: pageSize, window_days: 7 })
      .then((data) => {
        setItems(data.items);
        setTotal(data.total);
        setUnclassified(data.unclassified_runs);
      })
      .catch(() => setFailed(true));
  }, [source, query, page, pageSize, reloadKey]);

  function openFamily(value: string) {
    const next = new URLSearchParams(params);
    next.set('family', value);
    setParams(next);
  }

  function openExperience(userId: string, key: string) {
    const next = new URLSearchParams(params);
    next.set('experience', `${userId}:${key}`);
    setParams(next);
  }

  return (
    <>
      <PageHeader
        title={t('nav.learning')}
        description={t('learning.family.subtitle')}
      />
      <PageSection>
        <ModuleToolbar
          actions={
            <Typography.Text type="tertiary">
              {t('learning.family.unclassified', { count: unclassified })}
            </Typography.Text>
          }
          search={
            <>
              <Input
                style={{ width: 260 }}
                value={query}
                placeholder={t('learning.family.search')}
                onChange={setQuery}
              />
              <Select
                style={{ width: 140 }}
                showClear
                placeholder={t('learning.family.source')}
                optionList={[
                  { value: 'skill', label: 'skill' },
                  { value: 'agent', label: 'agent' },
                  { value: 'unknown', label: t('learning.family.unknown') }
                ]}
                value={source}
                onChange={(value) => {
                  setSource(value as string | undefined);
                  setPage(1);
                }}
              />
              <ListActionButton
                action="refresh"
                onClick={() => {
                  setFailed(false);
                  setReloadKey((value) => value + 1);
                }}
              />
            </>
          }
        />
        {failed ? (
          <Banner type="danger" description={t('learning.errors.load')} />
        ) : null}
      <RemoteTable
        scroll={{ x: '100%' }}
        dataSource={items}
        rowKey="family"
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        columns={[
          {
            title: t('learning.family.name'),
            dataIndex: 'family',
            render: (value: string) => (
              <EntityLink onClick={() => openFamily(value)}>{value}</EntityLink>
            )
          },
          {
            title: t('learning.family.source'),
            dataIndex: 'source',
            render: (value: string) => <Tag>{value}</Tag>
          },
          { title: t('learning.family.runs'), dataIndex: 'run_count' },
          {
            title: t('learning.columns.state'),
            dataIndex: 'states',
            render: (states: FamilyItem['states']) =>
              `${states.ACTIVE}/${states.TRIAL}/${states.PROPOSED}`
          },
          {
            title: t('learning.family.passRate'),
            dataIndex: 'pass_rate',
            render: (value: FamilyItem['pass_rate']) => `${value.passed}/${value.total}`
          },
          {
            title: t('learning.columns.actions'),
            key: 'actions',
            fixed: 'right',
            width: 120,
            render: (_: unknown, item: FamilyItem) => (
              <Button theme="borderless" onClick={() => openFamily(item.family)}>
                {t('learning.actions.view')}
              </Button>
            )
          }
        ]}
      />
      <FamilyDetailSideSheet
        family={family}
        onClose={() => {
          const next = new URLSearchParams(params);
          next.delete('family');
          setParams(next);
        }}
        onOpenExperience={openExperience}
      />
        <ExperienceDetailSideSheet
          userId={parsed?.userId ?? ''}
          memoryKey={parsed?.key ?? null}
          onClose={() => {
            const next = new URLSearchParams(params);
            next.delete('experience');
            setParams(next);
          }}
          onMutated={() => undefined}
        />
      </PageSection>
    </>
  );
}
