import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ConsoleApp } from "../App";
import { createInMemoryConsoleApi, type ConsoleSeed } from "../services/inMemoryConsoleApi";
import type { ConsoleApi } from "../types/console";
import type { ConsoleView } from "../types/navigation";

interface RenderConsoleOptions {
  readonly initialView?: ConsoleView;
  readonly seed?: ConsoleSeed;
  /** 覆盖 in-memory API（四态/故障注入测试用）。 */
  readonly api?: ConsoleApi;
}

export function renderConsole(options: RenderConsoleOptions = {}) {
  const api = options.api ?? createInMemoryConsoleApi(options.seed);
  const user = userEvent.setup();
  const view = render(
    <ConsoleApp api={api} initialView={options.initialView ?? "resources"} />
  );

  return { ...view, api, user };
}
