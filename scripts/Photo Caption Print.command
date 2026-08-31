#!/bin/zsh
# Double-click launcher.  All paths are anchored to this file, never Finder's cwd.
set -euo pipefail

MAX_SYMLINKS=32

pause_on_error() {
  local exit_status=$?
  if (( exit_status != 0 )) && [[ -t 0 ]]; then
    print -u2 "处理未完成（退出码 $exit_status）。按回车键关闭此窗口…"
    read -r || true
  fi
  return "$exit_status"
}
trap pause_on_error EXIT

resolve_script_path() {
  local source="$1" source_dir link_target
  local -i link_count=0
  typeset -A visited
  while [[ -L "$source" ]]; do
    if (( link_count >= MAX_SYMLINKS )); then
      print -u2 "错误：符号链接循环或层级过深。"
      return 2
    fi
    if [[ -n "${visited[$source]-}" ]]; then
      print -u2 "错误：符号链接循环。"
      return 2
    fi
    visited[$source]=1
    source_dir="$(cd -P "$(dirname "$source")" && pwd)"
    link_target="$(readlink "$source")"
    [[ "$link_target" = /* ]] || link_target="$source_dir/$link_target"
    source="$link_target"
    link_count=$((link_count + 1))
  done
  print -r -- "$source"
}

SOURCE_PATH="$(resolve_script_path "${(%):-%N}")"
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE_PATH")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"

safe_directory() {
  local directory="$1"
  if [[ -L "$directory" ]]; then
    print -u2 "错误：拒绝使用符号链接目录：$directory"
    return 1
  fi
  if [[ -e "$directory" ]]; then
    [[ -d "$directory" ]] || { print -u2 "错误：路径不是目录：$directory"; return 1; }
  else
    mkdir -m 700 "$directory"
  fi
}

safe_venv() {
  local venv="$1" bin="$1/bin" python="$1/bin/python" activate="$1/bin/activate"
  if [[ -L "$venv" || ! -d "$venv" || -L "$bin" || ! -d "$bin" || -L "$python" || ! -f "$python" || ! -x "$python" || -L "$activate" || ! -f "$activate" ]]; then
    print -u2 "错误：虚拟环境不安全或不完整。请先双击 scripts/Install.command 重新安装。"
    return 1
  fi
}

INPUT_DIR="$PROJECT_ROOT/已选照片"
OUTPUT_DIR="$PROJECT_ROOT/打印成品"
CACHE_DIR="$PROJECT_ROOT/cache"
REPORTS_DIR="$PROJECT_ROOT/reports"
OVERRIDES="$PROJECT_ROOT/人工补录.csv"
REPORT="$REPORTS_DIR/处理报告.csv"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
ACTIVATE="$PROJECT_ROOT/.venv/bin/activate"

for directory in "$INPUT_DIR" "$OUTPUT_DIR" "$CACHE_DIR" "$REPORTS_DIR"; do
  safe_directory "$directory"
done

if ! safe_venv "$PROJECT_ROOT/.venv"; then
  exit 2
fi
source "$ACTIVATE"

arguments=(
  --base-dir "$PROJECT_ROOT"
  --input "$INPUT_DIR"
  --output "$OUTPUT_DIR"
  --report "$REPORT"
  --cache "$CACHE_DIR/geocoding.json"
)
if [[ -f "$OVERRIDES" && ! -L "$OVERRIDES" ]]; then
  arguments+=(--overrides "$OVERRIDES")
fi

exit_code=0
if "$PYTHON" -m photo_caption_print.cli "${arguments[@]}"; then
  print "处理完成。报告：$REPORT"
else
  exit_code=$?
  print -u2 "处理未完成（退出码 $exit_code）。"
fi
exit "$exit_code"
