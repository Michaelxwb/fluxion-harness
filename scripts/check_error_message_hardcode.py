#!/usr/bin/env python3
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
violations = []
patterns = [
    re.compile(r'HTTPException\s*\([^\n]*detail\s*=\s*["\']'),
    re.compile(r'AppError\s*\([^,\n]+,\s*["\']'),
    re.compile(r'["\']msg["\']\s*:\s*["\'][^"\']+["\']'),
]

for base in [root / 'apps', root / 'packages']:
    for path in base.rglob('*.py'):
        text = path.read_text(encoding='utf-8')
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in patterns):
                violations.append(f'{path.relative_to(root)}:{lineno}: {line.strip()}')

if violations:
    print('\n'.join(violations))
    raise SystemExit(1)
print('backend error-message hardcode check OK')
