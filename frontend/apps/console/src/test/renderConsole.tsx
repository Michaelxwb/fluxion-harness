import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ConsoleApp } from "../App";
import { createInMemoryConsoleApi, type ConsoleSeed } from "../services/inMemoryConsoleApi";
import type { ConsoleView } from "../types/navigation";

interface RenderConsoleOptions {
  readonly initialView?: ConsoleView;
  readonly seed?: ConsoleSeed;
}

export function renderConsole(options: RenderConsoleOptions = {}) {
  const api = createInMemoryConsoleApi(options.seed);
  const user = userEvent.setup();
  const view = render(<ConsoleApp api={api} initialView={options.initialView ?? "resources"} />);

  return { ...view, api, user };
}
