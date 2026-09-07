import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export interface MarkdownMessageProps {
  readonly content: string;
}

/** 助手消息 Markdown 渲染：标题/粗体/列表/代码块/表格。
 * react-markdown 默认转义 HTML，不执行脚本。 */
export function MarkdownMessage({ content }: MarkdownMessageProps) {
  return (
    <div className="markdown-message">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}
