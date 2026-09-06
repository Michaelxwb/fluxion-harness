import type { ReactNode } from "react";

import { IconMore, IconSearch } from "@douyinfe/semi-icons";
import { Button, Card, Dropdown, Input, Pagination, Spin, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "./ErrorBanner";

/**
 * 标准列表布局（§5 硬性规范）：左上主操作、右上过滤+筛选+模糊搜索、
 * 中间 Semi Table、右下总数 + Pagination 单套分页。
 * Table 管理页一律 pagination={false}，分页交给 StandardListFooter。
 */

interface StandardListToolbarProps {
  /** 左上：主要新增/连接/添加按钮（1-2 个）。 */
  readonly primary: ReactNode;
  /** 右上：过滤/筛选 Select 组。 */
  readonly filters?: ReactNode;
  /** 右上：模糊搜索输入。 */
  readonly search?: ReactNode;
}

export function StandardListToolbar({ filters, primary, search }: StandardListToolbarProps) {
  return (
    <div className="standard-list-toolbar">
      <div className="standard-list-toolbar__left">{primary}</div>
      <div className="standard-list-toolbar__right">
        {filters}
        {search}
      </div>
    </div>
  );
}

interface StandardListFooterProps {
  readonly onPageChange: (page: number) => void;
  readonly page: number;
  readonly pageSize: number;
  readonly total: number;
}

export function StandardListFooter({ onPageChange, page, pageSize, total }: StandardListFooterProps) {
  return (
    <div className="standard-list-footer">
      <Typography.Text type="tertiary">共 {total} 条</Typography.Text>
      <Pagination
        currentPage={page}
        onPageChange={onPageChange}
        pageSize={pageSize}
        total={total}
      />
    </div>
  );
}

export interface RowAction {
  /** 稳定唯一 key（列表渲染禁用数组下标）。 */
  readonly key: string;
  readonly content: ReactNode;
  readonly onClick: () => void;
}

interface RowActionsProps {
  /** 高频操作：最多直出 1-2 个（§5 规则 9）。 */
  readonly immediate?: readonly RowAction[];
  /** 其余动作：收进 Dropdown（§5 规则 10）。 */
  readonly more?: readonly RowAction[];
}

export function RowActions({ immediate, more }: RowActionsProps) {
  // 单一 keyed 数组 child：避免裸 map 数组与条件元素混排触发 React key 校验告警
  const actions: ReactNode[] = (immediate ?? []).map((action) => (
    <Button key={action.key} onClick={action.onClick} size="small" theme="light">
      {action.content}
    </Button>
  ));
  if (more && more.length > 0) {
    actions.push(
      <Dropdown
        key="row-actions-more"
        render={
          <Dropdown.Menu>
            {more.map((action) => (
              <Dropdown.Item key={action.key} onClick={action.onClick}>
                {action.content}
              </Dropdown.Item>
            ))}
          </Dropdown.Menu>
        }
        trigger="click"
      >
        <Button
          aria-label="更多操作"
          data-testid="row-actions-more"
          icon={<IconMore />}
          size="small"
          theme="borderless"
        />
      </Dropdown>
    );
  }

  return <div className="row-actions">{actions}</div>;
}

interface StandardListSearchProps {
  readonly onChange: (value: string) => void;
  readonly placeholder?: string;
  readonly value: string;
}

export function StandardListSearch({ onChange, placeholder = "搜索", value }: StandardListSearchProps) {
  return (
    <Input
      onChange={onChange}
      placeholder={placeholder}
      prefix={<IconSearch />}
      showClear
      // 统一宽度：与右上过滤 Select 同行排布、置于最后（§5 标准列表布局）
      style={{ width: 240 }}
      value={value}
    />
  );
}

interface StandardListCardProps {
  /** 空态文案；empty=true 且未提供时用默认文案。 */
  readonly emptyDescription?: string;
  readonly empty?: boolean;
  readonly error?: string | null;
  readonly footer?: ReactNode;
  readonly loading?: boolean;
  readonly onRetry?: () => void;
  readonly title?: ReactNode;
  readonly toolbar?: ReactNode;
  readonly children: ReactNode;
}

/** 标准列表容器：优先级 error > loading > empty > 内容（§5 规则 12 标准态）。 */
export function StandardListCard({
  children,
  empty,
  emptyDescription,
  error,
  footer,
  loading,
  onRetry,
  title,
  toolbar
}: StandardListCardProps) {
  let body: ReactNode = children;
  if (error) {
    body = <ErrorBanner message={error} onRetry={onRetry} />;
  } else if (loading) {
    body = (
      <div className="standard-list-state">
        <Spin size="large" />
      </div>
    );
  } else if (empty) {
    // 空态仍渲染表格本体：Semi Table 空数据自带表头 + 空提示行（产品要求
    // 空状态保留表头）；emptyDescription 经 Table 的 empty 插槽由调用方呈现，
    // 此处仅作 aria 提示兜底。
    body = (
      <div aria-label={emptyDescription ?? "暂无数据"} role="status">
        {children}
      </div>
    );
  }

  return (
    <Card className="standard-list-card" title={title}>
      {toolbar}
      <div className="standard-list-body">{body}</div>
      {footer}
    </Card>
  );
}
