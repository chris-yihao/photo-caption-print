"""Command-line entry point for the local macOS photo-print workflow."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from photo_caption_print.captions import format_caption
from photo_caption_print.geocode import DEFAULT_ENDPOINT, ReverseGeocoder
from photo_caption_print.layout import (
    DEFAULT_SRGB_PROFILE,
    build_magick_command,
    fit_captions,
    measure_text,
    probe_oriented_dimensions,
    run_render,
)
from photo_caption_print.metadata import run_exiftool
from photo_caption_print.pipeline import BatchPipeline, BatchSummary, ReportError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MACOS_CJK_FONT = Path("/System/Library/Fonts/STHeiti Medium.ttc")
MACOS_HELVETICA = Path("/System/Library/Fonts/Helvetica.ttc")


def _default_font() -> str:
    """Prefer a macOS CJK system font, then retain the Helvetica fallbacks."""
    configured = os.environ.get("PHOTO_CAPTION_PRINT_FONT")
    if configured:
        return configured
    if MACOS_CJK_FONT.is_file():
        return str(MACOS_CJK_FONT)
    return str(MACOS_HELVETICA) if MACOS_HELVETICA.is_file() else "Helvetica"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将照片批量制作成带拍摄信息的冲印文件。")
    parser.add_argument("--base-dir", type=Path, default=PROJECT_ROOT, help="默认用户文件夹的根目录")
    parser.add_argument("--input", type=Path, help="待处理照片文件夹")
    parser.add_argument("--output", type=Path, help="生成的冲印文件夹")
    parser.add_argument("--report", type=Path, help="CSV 处理报告路径")
    parser.add_argument("--overrides", type=Path, help="可选的人工补录 CSV")
    parser.add_argument("--cache", type=Path, help="地点解析缓存 JSON 路径")
    parser.add_argument("--offline", action="store_true", help="只使用本地点解析缓存，不访问网络")
    parser.add_argument("--nominatim-url", default=DEFAULT_ENDPOINT, help="Nominatim reverse API 地址")
    parser.add_argument("--font", default=_default_font(), help="ImageMagick 字体名称或字体文件路径")
    parser.add_argument("--srgb-profile", type=Path, default=DEFAULT_SRGB_PROFILE, help="sRGB ICC 配置文件")
    return parser


def _absolute(path: Path) -> Path:
    """Make a lexical absolute path without dereferencing a symlink."""
    return Path(os.path.abspath(os.path.normpath(os.path.expanduser(str(path)))))


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path | None, Path]:
    base = _absolute(args.base_dir)
    def under_base(path: Path | None, default: Path) -> Path:
        return _absolute(path if path is not None else default)

    input_dir = under_base(args.input, base / "已选照片")
    output_dir = under_base(args.output, base / "打印成品")
    report = under_base(args.report, base / "reports" / "处理报告.csv")
    cache = under_base(args.cache, base / "cache" / "geocoding.json")
    overrides = _absolute(args.overrides) if args.overrides is not None else base / "人工补录.csv"
    return input_dir, output_dir, report, overrides, cache


def _has_symlink_component(path: Path) -> bool:
    """Reject a path if any existing lexical component is a symbolic link."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _validate(args: argparse.Namespace, paths: tuple[Path, Path, Path, Path | None, Path]) -> str | None:
    input_dir, output_dir, report, overrides, cache = paths
    names = (("输入文件夹", input_dir), ("输出文件夹", output_dir), ("处理报告", report), ("地点缓存", cache), ("人工补录 CSV", overrides))
    for label, path in names:
        if _has_symlink_component(path):
            return f"{label}不能使用符号链接：{path}"
    if not input_dir.is_dir():
        return f"输入文件夹不存在或不安全：{input_dir}"
    if args.overrides is not None and not overrides.is_file():
        return f"人工补录 CSV 不存在：{overrides}"
    if not report.name:
        return "处理报告必须是一个 CSV 文件路径。"
    if not cache.name:
        return "地点缓存必须是一个文件路径。"
    endpoint = urlsplit(args.nominatim_url)
    if not args.offline and (endpoint.scheme not in {"http", "https"} or not endpoint.netloc):
        return "Nominatim 地址必须是有效的 http 或 https URL。"
    if not Path(args.srgb_profile).is_file():
        return f"找不到 sRGB ICC 配置文件：{_absolute(args.srgb_profile)}"
    return None


def _dependency_error(which: Callable[[str], str | None]) -> str | None:
    missing = [name for name in ("exiftool", "magick") if not which(name)]
    if not missing:
        return None
    labels = {"exiftool": "ExifTool", "magick": "ImageMagick"}
    return "缺少依赖：" + "、".join(labels[name] for name in missing) + "。请运行 scripts/Install.command，或使用 Homebrew 安装后重试。"


def _default_services() -> dict[str, Any]:
    return {
        "which": shutil.which,
        "geocoder_factory": ReverseGeocoder,
        "pipeline_factory": BatchPipeline,
        "metadata_reader": run_exiftool,
        "dimension_probe": probe_oriented_dimensions,
        "caption_formatter": format_caption,
        "caption_fitter": fit_captions,
        "renderer": run_render,
        "measure_text": measure_text,
    }


def _make_pipeline(args: argparse.Namespace, cache: Path, services: Mapping[str, Any]) -> BatchPipeline:
    geocoder = services["geocoder_factory"](cache, endpoint=args.nominatim_url, offline=args.offline)
    font, profile = args.font, _absolute(args.srgb_profile)

    def fitter(lines: tuple[str, str], geometry: Any) -> Any:
        return services["caption_fitter"](lines, geometry, font, services["measure_text"])

    def renderer(source: Path, output: Path, geometry: Any, captions: Any) -> None:
        command = build_magick_command(source, output, geometry, captions, font, profile_path=profile)
        services["renderer"](command)

    return services["pipeline_factory"](
        metadata_reader=services["metadata_reader"],
        geocoder=geocoder,
        dimension_probe=services["dimension_probe"],
        caption_formatter=services["caption_formatter"],
        caption_fitter=fitter,
        renderer=renderer,
    )


def _print_summary(summary: BatchSummary, report: Path) -> None:
    print(f"成功 {summary.success_count}")
    print(f"警告 {summary.warning_count}")
    print(f"跳过 {summary.skipped_count}")
    print(f"失败 {summary.failed_count}")
    print(f"报告 {report}")


def main(argv: list[str] | None = None, services: Mapping[str, Any] | None = None) -> int:
    """Run one batch and return a shell-friendly status without tracebacks."""
    args = _parser().parse_args(argv)
    active_services = {**_default_services(), **(services or {})}
    try:
        if error := _dependency_error(active_services["which"]):
            print(error, file=sys.stderr)
            return 2
        paths = _paths(args)
        if error := _validate(args, paths):
            print(f"错误：{error}", file=sys.stderr)
            return 2
        input_dir, output_dir, report, overrides, cache = paths
        safe_overrides = overrides if overrides.exists() else None
        summary = _make_pipeline(args, cache, active_services).process_folder(input_dir, output_dir, report, safe_overrides)
        _print_summary(summary, report)
        return 1 if summary.failed_count else 0
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
    except (ValueError, ReportError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"处理失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
