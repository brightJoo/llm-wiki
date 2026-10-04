#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 6 ]]; then
  echo "usage: create_patch.sh <diff-base-ref> <patch-path> <metadata-path> [source-head-ref] [source-base-ref] [classification-path]" >&2
  exit 2
fi

base_ref=$1
patch_path=$2
metadata_path=$3
source_head_ref=${4:-$base_ref}
source_base_ref=${5:-}
classification_path=${6:-}
engine_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

git rev-parse --verify "${base_ref}^{commit}" >/dev/null
git rev-parse --verify "${source_head_ref}^{commit}" >/dev/null
mkdir -p "$(dirname "$patch_path")" "$(dirname "$metadata_path")"

temporary_index=$(mktemp "${TMPDIR:-/tmp}/llm-wiki-index.XXXXXX")
rm -f "$temporary_index"
cleanup() {
  # Remove only the temporary index allocated by this invocation.
  rm -f "$temporary_index"
}
trap cleanup EXIT

export GIT_INDEX_FILE=$temporary_index
git read-tree "$base_ref"
if [[ -e docs/wiki ]] || git ls-tree -d --name-only "$base_ref" docs/wiki | grep -q .; then
  git add -A -- docs/wiki
fi
git diff --cached --no-ext-diff --no-textconv --binary "$base_ref" -- docs/wiki > "$patch_path"

manifest_args=()
if [[ -n "$source_base_ref" ]]; then
  manifest_args+=(--source-base-ref "$source_base_ref")
fi
if [[ -n "$classification_path" ]]; then
  manifest_args+=(--classification "$classification_path")
fi
python3 "$engine_dir/artifact_manifest.py" create --repo . \
  --patch "$patch_path" --metadata "$metadata_path" \
  --diff-base-ref "$base_ref" --source-head-ref "$source_head_ref" "${manifest_args[@]}"
