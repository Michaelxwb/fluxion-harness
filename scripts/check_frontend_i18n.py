#!/usr/bin/env python3
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
locale_dir = root / 'apps/console-platform/frontend/src/locales'
zh = json.loads((locale_dir / 'zh-CN.json').read_text(encoding='utf-8'))
en = json.loads((locale_dir / 'en-US.json').read_text(encoding='utf-8'))

zh_keys = set(zh)
en_keys = set(en)
missing_en = sorted(zh_keys - en_keys)
missing_zh = sorted(en_keys - zh_keys)

if missing_en or missing_zh:
    if missing_en:
        print('Missing in en-US:', missing_en)
    if missing_zh:
        print('Missing in zh-CN:', missing_zh)
    raise SystemExit(1)

print(f'i18n keys OK: {len(zh_keys)}')
