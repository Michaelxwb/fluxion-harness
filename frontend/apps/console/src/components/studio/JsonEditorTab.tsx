/** C403 JsonEditorTab（TASK-012 / CMP-09）：JSON 高级模式（现有 DSL textarea 迁移）。 */
import { TextArea, Typography } from "@douyinfe/semi-ui";

interface JsonEditorTabProps {
  readonly specText: string;
  readonly onChange: (value: string) => void;
}

export function JsonEditorTab({ specText, onChange }: JsonEditorTabProps) {
  return (
    <div className="json-editor-tab">
      <TextArea
        aria-label="工作流 DSL JSON"
        className="workflow-dsl"
        onChange={onChange}
        value={specText}
      />
      <JsonSyntaxHint specText={specText} />
    </div>
  );
}

/** JSON 语法行内提示：解析失败即显示错误，不再静默吞。 */
function JsonSyntaxHint({ specText }: { readonly specText: string }) {
  try {
    JSON.parse(specText);
    return null;
  } catch (cause) {
    return (
      <Typography.Text aria-label="JSON 语法错误" type="danger">
        {`JSON 解析失败：${cause instanceof Error ? cause.message : "未知错误"}`}
      </Typography.Text>
    );
  }
}
