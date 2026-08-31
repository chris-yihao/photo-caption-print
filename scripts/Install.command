#!/bin/zsh
# User-run macOS installer. It intentionally avoids privileged commands and shell-profile edits.
set -euo pipefail

MAX_SYMLINKS=32

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

if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "此安装程序仅支持 macOS。"
  exit 2
fi
if ! command -v brew >/dev/null 2>&1; then
  print -u2 "未找到 Homebrew。请先访问 https://brew.sh 安装 Homebrew，然后重新双击本文件。"
  exit 2
fi
BREW="$(command -v brew)"
if ! "$BREW" --version >/dev/null 2>&1; then
  print -u2 "Homebrew 无法正常运行或已损坏。请在终端执行 brew update 并修复 Homebrew 后重试。"
  exit 2
fi

print "正在通过 Homebrew 安装 Python、ExifTool 和 ImageMagick…"
if ! "$BREW" install python@3.13 exiftool imagemagick; then
  print -u2 "Homebrew 安装依赖失败。请修复 Homebrew 后重试。"
  exit 2
fi

if ! PYTHON_PREFIX="$($BREW --prefix python@3.13 2>/dev/null)" || [[ -z "$PYTHON_PREFIX" ]]; then
  print -u2 "无法确定 Homebrew Python 3.13 的安装路径。请修复 Homebrew 后重试。"
  exit 2
fi
PYTHON="$PYTHON_PREFIX/bin/python3.13"
if [[ ! -x "$PYTHON" ]]; then
  print -u2 "未找到 Homebrew 安装的 Python 3.13：$PYTHON。请运行 brew update 后重试。"
  exit 2
fi

VENV="$PROJECT_ROOT/.venv"
safe_venv() {
  local venv="$1" bin="$1/bin" python="$1/bin/python" activate="$1/bin/activate"
  if [[ -L "$venv" || ! -d "$venv" || -L "$bin" || ! -d "$bin" || -L "$python" || ! -f "$python" || ! -x "$python" || -L "$activate" || ! -f "$activate" ]]; then
    print -u2 "错误：虚拟环境不安全或不完整：$venv"
    return 1
  fi
}

if [[ -L "$VENV" || ( -e "$VENV" && ! -d "$VENV" ) ]]; then
  print -u2 "错误：虚拟环境不安全或不完整：$VENV"
  print -u2 "请移除 .venv 后重新运行 scripts/Install.command。"
  exit 2
fi
if [[ -e "$VENV" ]]; then
  if ! safe_venv "$VENV"; then
    print -u2 "现有 .venv 不符合安全要求。请移除 .venv 后重新运行 scripts/Install.command。"
    exit 2
  fi
else
  print "正在创建本地 Python 环境…"
  if ! "$PYTHON" -m venv --copies "$VENV"; then
    print -u2 "创建虚拟环境失败。请检查 Python 3.13 安装后重试。"
    exit 2
  fi
  safe_venv "$VENV" || exit 2
fi
if ! "$VENV/bin/python" -m pip install --editable "$PROJECT_ROOT"; then
  print -u2 "安装本地程序失败。请检查虚拟环境后重试。"
  exit 2
fi

print "\n安装检查："
print -n "Python: "; "$VENV/bin/python" --version
if ! EXIF_VERSION="$(exiftool -ver 2>&1)"; then
  print -u2 "无法运行 ExifTool。请重新安装 exiftool 后重试。"
  exit 2
fi
print "ExifTool: $EXIF_VERSION"
if ! MAGICK_VERSION="$(magick -version 2>&1)"; then
  print -u2 "无法运行 ImageMagick。请重新安装 imagemagick 后重试。"
  exit 2
fi
print "ImageMagick: ${MAGICK_VERSION%%$'\n'*}"
if ! FORMAT_LIST="$(magick -list format 2>&1)"; then
  print -u2 "无法读取 ImageMagick 格式列表。请重新安装 imagemagick 后重试。"
  exit 2
fi
if [[ "$FORMAT_LIST" == *HEIC* ]]; then
  print "HEIC：可用"
else
  print -u2 "HEIC：不可用；请运行 brew update && brew upgrade imagemagick 后重试。"
  exit 2
fi
MACOS_CJK_FONT="/System/Library/Fonts/STHeiti Medium.ttc"
MACOS_HELVETICA="/System/Library/Fonts/Helvetica.ttc"
if [[ -n "${PHOTO_CAPTION_PRINT_FONT:-}" ]]; then
  SELECTED_FONT="$PHOTO_CAPTION_PRINT_FONT"
elif [[ -f "$MACOS_CJK_FONT" ]]; then
  SELECTED_FONT="$MACOS_CJK_FONT"
elif [[ -f "$MACOS_HELVETICA" ]]; then
  SELECTED_FONT="$MACOS_HELVETICA"
else
  SELECTED_FONT="Helvetica"
fi
if ! magick -font "$SELECTED_FONT" -pointsize 24 "label:中文" -format "%w" info: >/dev/null 2>&1; then
  print -u2 "字体不可用：$SELECTED_FONT。请使用有效的字体文件路径或 ImageMagick 已注册的字体名称。"
  exit 2
fi
print "字体：可用（$SELECTED_FONT）"
SRGB_PROFILE="${PHOTO_CAPTION_PRINT_SRGB_PROFILE:-/System/Library/ColorSync/Profiles/sRGB Profile.icc}"
if [[ -f "$SRGB_PROFILE" ]]; then
  print "sRGB 配置文件：可用"
else
  print -u2 "找不到 macOS sRGB 配置文件：$SRGB_PROFILE"
  exit 2
fi

chmod +x "$SCRIPT_DIR/Photo Caption Print.command" "$SCRIPT_DIR/Install.command"
print "\n安装完成。将照片放进“已选照片”后，双击 scripts/Photo Caption Print.command。"
