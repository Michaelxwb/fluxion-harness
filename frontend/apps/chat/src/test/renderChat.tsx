import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ChatApp } from "../App";
import { createInMemoryChatApi } from "../services/inMemoryChatApi";

export function renderChat() {
  const api = createInMemoryChatApi({ bindCode: "WEB-CODE", platformUserId: "user-a" });
  const user = userEvent.setup();
  const view = render(<ChatApp api={api} />);
  return { ...view, api, user };
}
