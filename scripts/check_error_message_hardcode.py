#!/usr/bin/env python3
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]

catalog_text = (root / 'config/api-messages.yaml').read_text(encoding='utf-8')
catalog_codes = set(
    re.findall(
        r'^\s{2}["\']?([A-Za-z0-9_]+)["\']?\s*:\s*$',
        catalog_text.split('codes:', 1)[-1],
        re.MULTILINE,
    )
)

http_detail = re.compile(r'HTTPException\s*\([^)]*?detail\s*=\s*f?["\']', re.DOTALL)
app_error_literal = re.compile(r'AppError\s*\(\s*(["\'])(?P<code>[A-Za-z0-9_]+)\1')
hardcoded_msg = re.compile(r'["\']msg["\']\s*:\s*["\'][^"\']+["\']')

violations = []
for base in [root / 'apps', root / 'packages']:
    for path in base.rglob('*.py'):
        text = path.read_text(encoding='utf-8')
        matches = []
        for pattern in (http_detail, hardcoded_msg):
            matches.extend((match.start(), match.group(0)) for match in pattern.finditer(text))
        for match in app_error_literal.finditer(text):
            if match.group('code') not in catalog_codes:
                matches.append((match.start(), match.group(0)))
        for start, snippet in sorted(matches):
            lineno = text.count('\n', 0, start) + 1
            violations.append(f'{path.relative_to(root)}:{lineno}: {" ".join(snippet.split())}')

if violations:
    print('\n'.join(violations))
    raise SystemExit(1)
print('backend error-message hardcode check OK')
