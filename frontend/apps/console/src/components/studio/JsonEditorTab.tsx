/** C403 JsonEditorTab（TASK-012 / CMP-09）：JSON 高级模式（现有 DSL textarea 迁移）。 */
import { TextArea } from "@douyinfe/semi-ui";

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
    </div>
  );
}
