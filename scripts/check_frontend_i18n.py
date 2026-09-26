#!/usr/bin/env python3
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
frontend_dir = root / 'apps/console-platform/frontend'
locale_dir = frontend_dir / 'src/locales'
source_dir = frontend_dir / 'src'

# 源码里的字面量文案键引用：t('some.key') / t("some.key")。
# 带 ${} 插值的模板串与变量入参不匹配本正则，属动态键，由模块级 i18n 契约用例覆盖。
T_CALL_RE = re.compile(r"""\bt\(\s*(['"])([A-Za-z0-9_][A-Za-z0-9_.:\-]*)\1""")
SOURCE_SUFFIXES = ('.ts', '.tsx')


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


def referenced_keys(search_dir=None):
    """扫描前端源码，收集 t('字面量键') 形式的键及其引用位置。"""
    references = {}
    for path in sorted((search_dir or source_dir).rglob('*')):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if 'locales' in path.parts:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        for match in T_CALL_RE.finditer(text):
            key = match.group(2)
            if '.' not in key:
                continue
            line = text.count('\n', 0, match.start()) + 1
            try:
                shown = path.relative_to(root)
            except ValueError:
                shown = path
            references.setdefault(key, []).append(f'{shown}:{line}')
    return references


def compare_source_references(zh_path, search_dir=None):
    """代码引用的键必须已定义：只比对两侧键集齐平会漏掉“用了但没写进词条”。"""
    zh_keys = set(flatten(json.loads(Path(zh_path).read_text(encoding='utf-8'))))
    problems = []
    for key, places in sorted(referenced_keys(search_dir).items()):
        if key in zh_keys:
            continue
        problems.append(f'Referenced but not defined: {key} ({", ".join(places[:3])})')
    return problems


def main():
    problems, key_count = compare_locale_files(locale_dir / 'zh-CN.json', locale_dir / 'en-US.json')
    problems.extend(compare_source_references(locale_dir / 'zh-CN.json'))
    if problems:
        print('\n'.join(problems))
        raise SystemExit(1)

    print(f'i18n keys OK: {key_count}')


if __name__ == '__main__':
    main()
