"""Regenerate navigation indexes from module-owned field and interface tables."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DOCS = ROOT / 'docs'
COMMON = {'id', 'is_deleted', 'create_time', 'update_time'}


def interfaces(source: str) -> list[list[str]]:
    section = source.split('#### 3.4.1 接口清单', 1)
    if len(section) != 2:
        return []
    body = re.split(r'^#### ', section[1], maxsplit=1, flags=re.M)[0]
    result: list[list[str]] = []
    for line in body.splitlines():
        if not re.match(r'^\| [A-Z]+-(?:API|LIB|INT|DATA|CLI)-\d+', line):
            continue
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        result.append(cells[:3] + ['|'.join(cells[3:-1]), cells[-1]])
    return result


def tables(source: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    parts = re.split(r'^#### 表 `([a-z_]+)`', source, flags=re.M)
    for index in range(1, len(parts), 2):
        body = parts[index + 1].split('| 字段名 |', 1)
        if len(body) != 2:
            continue
        names: list[str] = []
        for line in body[1].splitlines()[1:]:
            if not line.startswith('|'):
                break
            name = line.strip('|').split('|')[0].strip()
            if re.fullmatch(r'[a-z][a-z0-9_]*', name) and name not in COMMON:
                names.append(name)
        if names:
            result[parts[index]] = names
    return result


def rewrite_owner_rows(source: str, values: dict[str, str], heading: str) -> str:
    start = source.index(heading)
    end_match = re.search(r'^## ', source[start + len(heading):], re.M)
    end = len(source) if end_match is None else start + len(heading) + end_match.start()
    lines = source[start:end].splitlines()
    for index, line in enumerate(lines):
        if not line.startswith('| '):
            continue
        name = line.strip('|').split('|')[0].strip()
        if name in values:
            lines[index] = f'| {name} | {values[name]} |'
    return source[:start] + '\n'.join(lines).rstrip() + '\n\n' + source[end:]


def regenerate() -> None:
    api_rows: list[str] = []
    db_rows: list[str] = []
    api_owners: dict[str, str] = {}
    db_owners: dict[str, str] = {}
    counts: dict[str, tuple[int, int]] = {}
    for path in sorted((DOCS / '02-模块设计').glob('*/design-*.md')):
        name, source = path.parent.name, path.read_text()
        api, db = interfaces(source), tables(source)
        api_owners[name] = ', '.join(f'`{row[0]}`' for row in api) or '无冻结接口'
        db_owners[name] = ', '.join(f'`{table}`' for table in db) or '无独立表'
        counts[name] = len(db), len(api)
        for identifier, label, kind, method, route in api:
            contract = f'{method} {route}'.strip().replace('|', r'\|')
            api_rows.append(f'| {identifier} | {name} | {kind} | {contract} | {label} |')
        for table, fields in db.items():
            ref = f'../02-模块设计/{name}/{path.name}'
            db_rows.append(f'| {table} | {name} | {", ".join(fields)} | [字段与约束]({ref}) |')
    write_index('09-接口所有权与详细设计索引.md', '## 2. 接口索引', api_rows)
    write_index('08-数据库表所有权与字段索引.md', '## 2. 表所有权', db_rows)
    path = DOCS / '02-模块设计/README.md'
    source = rewrite_owner_rows(path.read_text(), api_owners, '## 3. Interface Owner')
    source = rewrite_owner_rows(source, db_owners, '## 2. DB Owner')
    path.write_text(update_counts(source, counts))


def write_index(name: str, heading: str, rows: list[str]) -> None:
    path = DOCS / '01-架构与规范' / name
    source = path.read_text()
    start = source.index(heading)
    lines = source[start:].splitlines()
    table_start = next(index for index, line in enumerate(lines) if line.startswith('|---'))
    table_end = table_start + 1
    while table_end < len(lines) and lines[table_end].startswith('|'):
        table_end += 1
    rebuilt = lines[:table_start + 1] + rows + lines[table_end:]
    path.write_text(source[:start] + '\n'.join(rebuilt) + '\n')


def update_counts(source: str, counts: dict[str, tuple[int, int]]) -> str:
    lines = source.splitlines()
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        if len(cells) == 6 and cells[0] in counts:
            cells[2], cells[3] = map(str, counts[cells[0]])
            lines[index] = '| ' + ' | '.join(cells) + ' |'
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    regenerate()
