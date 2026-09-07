import React from "react";

/** 打字机指示器：三个跳动圆点（助手思考中，无内容时替代光标）。 */
export function TypingIndicator() {
  return (
    <span className="typing-indicator" aria-label="对方正在输入">
      <span className="typing-dot" />
      <span className="typing-dot" />
      <span className="typing-dot" />
    </span>
  );
}
