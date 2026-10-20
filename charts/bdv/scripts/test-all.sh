#!/usr/bin/env bash
set -Eeuo pipefail
CHART="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export HELM="${HELM:-$(command -v helm)}"
python3 -m unittest discover -s "$CHART/scripts" -p 'test_*.py'
python3 - "$CHART" <<'PY'
import ast, json, sys
from pathlib import Path
root = Path(sys.argv[1])
for folder in ('scripts', 'files'):
    for path in (root / folder).glob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
json.loads((root / 'values.schema.json').read_text())
print('PASS: Python and JSON syntax')
PY
for file in "$CHART"/scripts/*.sh "$CHART"/files/*.sh "$CHART"/images/*/*.sh; do bash -n "$file"; done
echo 'PASS: shell syntax. No cluster changes performed.'
