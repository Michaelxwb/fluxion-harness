import { Pagination, Select } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { listExperiences } from './services/learning';
import { ExperienceDetailSideSheet } from './ExperienceDetailSideSheet';
import { ExperienceList } from './ExperienceList';
import type { ActivationState, ExperienceCounts, ExperienceItem } from './types';

const STATES: ActivationState[] = ['PROPOSED', 'TRIAL', 'ACTIVE', 'RETIRED'];
const EMPTY: ExperienceCounts = { PROPOSED: 0, TRIAL: 0, ACTIVE: 0, RETIRED: 0, total: 0 };

export function ExperienceTab(props: { userId: string }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<ExperienceItem[]>([]);
  const [counts, setCounts] = useState<ExperienceCounts>(EMPTY);
  const [state, setState] = useState<ActivationState | undefined>();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [selected, setSelected] = useState<ExperienceItem | null>(null);

  async function reload() {
    const data = await listExperiences({
      user_id: props.userId,
      state,
      page,
      page_size: pageSize
    });
    setItems(data.items);
    setCounts(data.counts);
  }

  useEffect(() => {
    void reload();
  }, [props.userId, state, page, pageSize]);

  return (
    <div>
      <Select
        showClear
        placeholder={t('learning.columns.state')}
        optionList={STATES.map((value) => ({ value, label: t(`learning.state.${value}`) }))}
        value={state}
        onChange={(value) => {
          setState(value as ActivationState | undefined);
          setPage(1);
        }}
      />
      <div>{t('learning.counts.total', { count: counts.total })}</div>
      <ExperienceList
        items={items}
        onOpen={(item) => setSelected(item)}
      />
      <Pagination
        currentPage={page}
        pageSize={pageSize}
        total={counts.total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
      />
      <ExperienceDetailSideSheet
        userId={props.userId}
        memoryKey={selected?.memory_key ?? null}
        onClose={() => setSelected(null)}
        onMutated={() => void reload()}
      />
    </div>
  );
}
