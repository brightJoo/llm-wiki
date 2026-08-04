#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "usage: create_patch.sh <diff-base-ref> <patch-path> <metadata-path> [source-base-ref]" >&2
  exit 2
fi

base_ref=$1
patch_path=$2
metadata_path=$3
source_base_ref=${4:-$base_ref}

git rev-parse --verify "${base_ref}^{commit}" >/dev/null
git rev-parse --verify "${source_base_ref}^{commit}" >/dev/null
mkdir -p "$(dirname "$patch_path")" "$(dirname "$metadata_path")"

temporary_index=$(mktemp "${TMPDIR:-/tmp}/llm-wiki-index.XXXXXX")
rm -f "$temporary_index"
cleanup() {
  rm -f "$temporary_index"
}
trap cleanup EXIT

export GIT_INDEX_FILE=$temporary_index
git read-tree "$base_ref"
if [[ -e docs/wiki ]] || git ls-tree -d --name-only "$base_ref" docs/wiki | grep -q .; then
  git add -A -- docs/wiki
fi
git diff --cached --no-ext-diff --no-textconv --binary "$base_ref" -- docs/wiki > "$patch_path"

python3 - "$patch_path" "$metadata_path" "$source_base_ref" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

patch_path = Path(sys.argv[1])
metadata_path = Path(sys.argv[2])
patch = patch_path.read_bytes()
metadata = {
    "version": 1,
    "changed": bool(patch),
    "bytes": len(patch),
    "sha256": hashlib.sha256(patch).hexdigest(),
    "base_ref": sys.argv[3],
}
metadata_path.write_text(
    json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
