/** Single-line table text with the complete value or its raw code available on hover. */
export function TableText({ children, title = children }: { children: string; title?: string }) {
  return <span className="table-text" title={title}>{children}</span>;
}
