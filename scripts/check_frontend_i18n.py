#!/usr/bin/env python3
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
locale_dir = root / 'apps/console-platform/frontend/src/locales'


def flatten(data, prefix=''):
    flat = {}
    for key, value in data.items():
        full_key = f'{prefix}.{key}' if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, full_key))
        else:
            flat[full_key] = value
    return flat


def compare_locale_files(zh_path, en_path):
    zh = flatten(json.loads(Path(zh_path).read_text(encoding='utf-8')))
    en = flatten(json.loads(Path(en_path).read_text(encoding='utf-8')))

    problems = []
    missing_en = sorted(set(zh) - set(en))
    missing_zh = sorted(set(en) - set(zh))
    if missing_en:
        problems.append(f'Missing in en-US: {missing_en}')
    if missing_zh:
        problems.append(f'Missing in zh-CN: {missing_zh}')
    for locale, flat in (('zh-CN', zh), ('en-US', en)):
        empty = sorted(key for key, value in flat.items() if isinstance(value, str) and not value.strip())
        if empty:
            problems.append(f'Empty in {locale}: {empty}')
    return problems, len(zh)


def main():
    problems, key_count = compare_locale_files(locale_dir / 'zh-CN.json', locale_dir / 'en-US.json')
    if problems:
        print('\n'.join(problems))
        raise SystemExit(1)

    print(f'i18n keys OK: {key_count}')


if __name__ == '__main__':
    main()
