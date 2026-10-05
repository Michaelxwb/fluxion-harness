/**
 * CMP-07：不在此页管理的项与归属说明（前端设计 §3.3）。
 *
 * 纯展示：`notes` 来自 API-01 的 `readonly_notes`（`.env`+重启 / 资源页面 / 代码三类），
 * 每项展示归属标签与说明文案，避免管理员在本页找不到这些设置。
 */

import { useTranslation } from 'react-i18next';

import type { ReadonlyNote } from '../types';

export interface ReadonlyNotesPanelProps {
  notes: ReadonlyNote[];
}

export function ReadonlyNotesPanel({ notes }: ReadonlyNotesPanelProps) {
  const { t } = useTranslation();
  if (notes.length === 0) {
    return null;
  }
  return (
    <div className="settings-readonly" data-testid="settings-readonly">
      <div className="settings-readonly-title">{t('settings.readonly.title')}</div>
      <ul className="settings-readonly-list">
        {notes.map((note) => (
          <li key={note.key} data-testid={`settings-readonly-${note.owner}`}>
            <span className="settings-readonly-label">{t(note.labelKey)}</span>
            <span className="settings-readonly-note">{t(note.noteKey)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
