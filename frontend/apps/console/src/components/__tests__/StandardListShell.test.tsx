import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  RowActions,
  StandardListCard,
  StandardListFooter,
  StandardListSearch,
  StandardListToolbar
} from "../StandardListShell";

const sampleRows = [
  { key: "1", name: "客服助手" },
  { key: "2", name: "IT 助手" }
];

/** §5/§6 标准列表布局：左上主操作、右上过滤+搜索、中间 Semi Table、右下总数+Pagination。 */
describe("StandardListShell 布局契约 (F-S-01)", () => {
  it("F-S-01: toolbar 把主操作分发到左侧插槽、过滤与搜索到右侧插槽", () => {
    render(
      <StandardListToolbar
        primary={<button type="button">新建智能体</button>}
        filters={
          <>
            <select aria-label="状态" />
            <select aria-label="模型" />
          </>
        }
        search={<input aria-label="搜索智能体" />}
      />
    );

    const toolbar = document.querySelector(".standard-list-toolbar");
    expect(toolbar).not.toBeNull();
    const left = toolbar?.querySelector(".standard-list-toolbar__left");
    const right = toolbar?.querySelector(".standard-list-toolbar__right");
    expect(left).not.toBeNull();
    expect(right).not.toBeNull();
    expect(within(left as HTMLElement).getByText("新建智能体")).not.toBeNull();
    expect(within(right as HTMLElement).getByLabelText("状态")).not.toBeNull();
    expect(within(right as HTMLElement).getByLabelText("模型")).not.toBeNull();
    expect(within(right as HTMLElement).getByLabelText("搜索智能体")).not.toBeNull();
  });

  it("F-S-01: footer 渲染总数文本与单套 Semi Pagination，无独立上一页/下一页按钮", () => {
    const onPageChange = vi.fn();
    const { container } = render(
      <StandardListFooter total={128} page={2} pageSize={10} onPageChange={onPageChange} />
    );

    const footer = container.querySelector(".standard-list-footer");
    expect(footer).not.toBeNull();
    expect(within(footer as HTMLElement).getByText("共 128 条")).not.toBeNull();
    // 单套分页：footer 内存在 Semi Pagination（DOM 为 ul.semi-page）
    expect((footer as HTMLElement).querySelector(".semi-page")).not.toBeNull();
    // §5 规则 7：禁止「上一页/下一页 + Pagination」两套分页控件并存
    const buttons = within(footer as HTMLElement).queryAllByRole("button");
    const pagerLabels = buttons.filter((node) => /^(上一页|下一页)$/.test(node.textContent ?? ""));
    expect(pagerLabels).toHaveLength(0);
    expect((footer as HTMLElement).className).toContain("standard-list-footer");
  });

  it("F-S-01: footer Pagination 翻页回调生效", async () => {
    const user = userEvent.setup();
    const onPageChange = vi.fn();
    render(<StandardListFooter total={30} page={1} pageSize={10} onPageChange={onPageChange} />);
    // Semi Pagination 页码项为 li.semi-page-item（文本即页码）
    await user.click(screen.getByText("3"));
    expect(onPageChange).toHaveBeenCalledWith(3);
  });

  it("F-S-01: RowActions 直出高频操作、低频操作收进 Dropdown 触发器", () => {
    const onEdit = vi.fn();
    const onPublish = vi.fn();
    render(
      <RowActions
        immediate={[
          { key: "edit", content: "编辑", onClick: onEdit },
          { key: "publish", content: "发布", onClick: onPublish }
        ]}
        more={[{ key: "delete", content: "删除", onClick: vi.fn() }]}
      />
    );

    expect(screen.getByRole("button", { name: "编辑" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "发布" })).not.toBeNull();
    // 低频操作不直出（收进 Dropdown，触发器存在）
    expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
    expect(screen.getByTestId("row-actions-more")).not.toBeNull();
  });

  it("F-S-01: loading / error / empty 标准态互斥切换", () => {
    const { rerender } = render(
      <StandardListCard loading={true}>
        <table>
          <tbody>
            {sampleRows.map((row) => (
              <tr key={row.key}>
                <td>{row.name}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </StandardListCard>
    );
    // loading：Semi Spin 存在，表格不渲染
    expect(document.querySelector(".semi-spin")).not.toBeNull();
    expect(screen.queryByText("客服助手")).toBeNull();

    rerender(
      <StandardListCard error="加载失败">
        <table>
          <tbody />
        </table>
      </StandardListCard>
    );
    // error：ErrorBanner 文案
    expect(screen.getByText("加载失败")).not.toBeNull();

    rerender(
      <StandardListCard empty={true} emptyDescription="暂无智能体">
        <table>
          <thead>
            <tr>
              <th>名称</th>
            </tr>
          </thead>
          <tbody />
        </table>
      </StandardListCard>
    );
    // empty：表格本体保留（空状态也有表头——产品要求），aria 兜底提示存在
    expect(screen.getByRole("columnheader", { name: "名称" })).not.toBeNull();
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "暂无智能体");
    expect(screen.queryByText("客服助手")).toBeNull();
  });

  it("F-S-01: StandardListSearch 受控输入回调", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<StandardListSearch value="" onChange={onChange} placeholder="搜索凭据" />);
    const input = screen.getByPlaceholderText("搜索凭据");
    await user.type(input, "a");
    // Semi Input onChange 签名为 (value, event)
    expect(onChange).toHaveBeenCalledWith("a", expect.anything());
  });
});
