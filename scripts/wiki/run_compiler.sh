#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: run_compiler.sh <prompt-path> <result-path>" >&2
  exit 2
fi

prompt_path=$1
result_path=$2
claude_bin=${CLAUDE_BIN:-claude}
max_turns=${CLAUDE_MAX_TURNS:-8}
allowed_tools=${CLAUDE_ALLOWED_TOOLS:-Read,Grep,Glob,Edit,Write}

if [[ ! -f "$prompt_path" ]]; then
  echo "run-compiler: prompt file does not exist" >&2
  exit 2
fi
if [[ ! "$max_turns" =~ ^[1-9][0-9]*$ ]]; then
  echo "run-compiler: CLAUDE_MAX_TURNS must be a positive integer" >&2
  exit 2
fi

result_dir=$(dirname "$result_path")
mkdir -p "$result_dir"
temporary_result=$(mktemp "$result_dir/.claude-result.XXXXXX")
temporary_error=$(mktemp "$result_dir/.claude-error.XXXXXX")

cleanup() {
  rm -f "$temporary_result" "$temporary_error"
}
trap cleanup EXIT

if ! "$claude_bin" \
  -p \
  --output-format json \
  --max-turns "$max_turns" \
  --allowedTools "$allowed_tools" \
  < "$prompt_path" \
  > "$temporary_result" \
  2> "$temporary_error"; then
  echo "run-compiler: claude exited with an error" >&2
  exit 1
fi

if ! python3 - "$temporary_result" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
if not isinstance(value, dict):
    raise SystemExit(1)
if value.get("is_error") is True:
    raise SystemExit(1)
PY
then
  echo "run-compiler: claude returned an invalid result" >&2
  exit 1
fi

mv "$temporary_result" "$result_path"

