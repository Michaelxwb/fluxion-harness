import "@douyinfe/semi-ui/react19-adapter";

import React from "react";
import { createRoot } from "react-dom/client";
import "@douyinfe/semi-ui/dist/css/semi.min.css";

import { ChatApp } from "./App";
import { accessTokenFromHash, createHttpChatApi } from "./services/httpChatApi";

const root = document.getElementById("root");

if (!root) {
  throw new Error("root element not found");
}

createRoot(root).render(
  <React.StrictMode>
    <ChatApp api={createHttpChatApi(accessTokenFromHash(window.location.hash))} />
  </React.StrictMode>
);
