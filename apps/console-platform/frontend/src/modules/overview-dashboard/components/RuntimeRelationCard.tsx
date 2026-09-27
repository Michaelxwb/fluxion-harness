/**
 * 运行关系说明（设计 §3.3 CMP-03 / FEAT-FE-02）：**纯静态**说明，无 props、无取数。
 *
 * 文案全部取自词条（`overview.runtimeRelation.*`）；结构固定为
 * IM → Agent → Runtime → ExecutionRouter → Worker/DB/Gateway，不随数据变化。
 */

import { Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

export function RuntimeRelationCard() {
  const { t } = useTranslation();
  return (
    <div className="overview-relation" data-testid="runtime-relation-card">
      <Typography.Title heading={6}>{t('overview.runtimeRelation.title')}</Typography.Title>
      <Typography.Paragraph>{t('overview.runtimeRelation.description')}</Typography.Paragraph>
    </div>
  );
}
