from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import stat
import subprocess
import sys

import pytest

from photo_caption_print.geocode import GeocodeResult
from photo_caption_print.models import ProcessResult
from photo_caption_print.pipeline import BatchSummary, ReportError


@dataclass
class FakePipeline:
    summary: BatchSummary | Exception
    calls: list[tuple[Path, Path, Path, Path | None]]

    def process_folder(self, input_dir, output_dir, report_path, overrides_path=None):
        self.calls.append((Path(input_dir), Path(output_dir), Path(report_path), None if overrides_path is None else Path(overrides_path)))
        if isinstance(self.summary, BaseException):
            raise self.summary
        return self.summary


def successful_summary() -> BatchSummary:
    return BatchSummary((ProcessResult(Path("photo.jpg"), Path("output.jpg"), "success"),))


def services(pipeline: FakePipeline, *, dependencies: dict[str, str | None] | None = None):
    created_geocoders = []

    def geocoder_factory(cache_path, *, endpoint, offline):
        created_geocoders.append((Path(cache_path), endpoint, offline))
        return object()

    return {
        "which": lambda name: (dependencies or {"exiftool": "/tools/exiftool", "magick": "/tools/magick"})[name],
        "geocoder_factory": geocoder_factory,
        "pipeline_factory": lambda **kwargs: pipeline,
        "metadata_reader": lambda paths: [],
        "dimension_probe": lambda path: (4000, 3000),
        "caption_formatter": lambda metadata: ("", ""),
        "caption_fitter": lambda lines, geometry, font, measure: lines,
        "renderer": lambda source, output, geometry, captions, font, profile: None,
        "measure_text": lambda text, font, size: 1,
        "created_geocoders": created_geocoders,
    }


@pytest.mark.parametrize("dependency", ["exiftool", "magick"])
def test_main_reports_each_missing_required_dependency_in_chinese(tmp_path, capsys, dependency):
    from photo_caption_print.cli import main

    pipeline = FakePipeline(successful_summary(), [])
    result = main(["--base-dir", str(tmp_path)], services(pipeline, dependencies={"exiftool": "/tools/exiftool", "magick": "/tools/magick", dependency: None}))

    assert result == 2
    error = capsys.readouterr().err
    assert {"exiftool": "ExifTool", "magick": "ImageMagick"}[dependency] in error
    assert "请运行" in error
    assert not pipeline.calls


def test_main_uses_base_relative_defaults_and_prints_chinese_summary(tmp_path, capsys):
    from photo_caption_print.cli import main

    base = tmp_path / "带 空格 的项目"
    (base / "已选照片").mkdir(parents=True)
    pipeline = FakePipeline(successful_summary(), [])
    result = main(["--base-dir", str(base)], services(pipeline))

    assert result == 0
    assert pipeline.calls == [(base / "已选照片", base / "打印成品", base / "reports" / "处理报告.csv", None)]
    output = capsys.readouterr().out
    assert "成功 1" in output
    assert "警告 0" in output
    assert "跳过 0" in output
    assert "失败 0" in output
    assert str((base / "reports" / "处理报告.csv").resolve()) in output


def test_main_passes_explicit_paths_offline_and_nominatim_endpoint(tmp_path):
    from photo_caption_print.cli import main

    input_dir = tmp_path / "照片 空间"; input_dir.mkdir()
    output_dir = tmp_path / "成品"
    report = tmp_path / "报告" / "报告.csv"
    overrides = tmp_path / "覆盖.csv"; overrides.write_text("filename,captured_at,location,device\n", encoding="utf-8")
    cache = tmp_path / "缓存" / "geo.json"
    pipeline = FakePipeline(successful_summary(), [])
    injected = services(pipeline)

    result = main([
        "--input", str(input_dir), "--output", str(output_dir), "--report", str(report),
        "--overrides", str(overrides), "--cache", str(cache), "--offline",
        "--nominatim-url", "https://example.test/reverse",
    ], injected)

    assert result == 0
    assert pipeline.calls == [(input_dir.resolve(), output_dir.resolve(), report.resolve(), overrides.resolve())]
    assert injected["created_geocoders"] == [(cache.resolve(), "https://example.test/reverse", True)]


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (BatchSummary((ProcessResult(Path("a.jpg"), None, "failed", error="bad"),)), 1),
        (BatchSummary((ProcessResult(Path("a.jpg"), Path("a-print.jpg"), "warning", warning="low PPI"),)), 0),
    ],
)
def test_main_exit_code_follows_per_photo_failures(tmp_path, summary, expected):
    from photo_caption_print.cli import main

    (tmp_path / "已选照片").mkdir()
    assert main(["--base-dir", str(tmp_path)], services(FakePipeline(summary, []))) == expected


def test_main_treats_configuration_errors_as_usage_errors(tmp_path, capsys):
    from photo_caption_print.cli import main

    (tmp_path / "已选照片").mkdir()
    assert main(["--base-dir", str(tmp_path)], services(FakePipeline(ValueError("bad configuration"), []))) == 2
    assert "错误" in capsys.readouterr().err


def test_main_returns_2_for_report_error_by_documented_cli_contract(tmp_path, capsys):
    """A durable report failure is a setup/actionable error, as Task 7 specifies."""
    from photo_caption_print.cli import main

    (tmp_path / "已选照片").mkdir()
    assert main(["--base-dir", str(tmp_path)], services(FakePipeline(ReportError("report unavailable"), []))) == 2
    assert "错误" in capsys.readouterr().err


def test_main_rejects_missing_input_directory_without_running_pipeline(tmp_path, capsys):
    from photo_caption_print.cli import main

    pipeline = FakePipeline(successful_summary(), [])
    assert main(["--base-dir", str(tmp_path)], services(pipeline)) == 2
    assert "输入文件夹" in capsys.readouterr().err
    assert not pipeline.calls


def test_main_returns_130_for_keyboard_interrupt(tmp_path):
    from photo_caption_print.cli import main

    (tmp_path / "已选照片").mkdir()
    assert main(["--base-dir", str(tmp_path)], services(FakePipeline(KeyboardInterrupt(), []))) == 130


@pytest.mark.parametrize("name", ["已选照片", "打印成品", "reports/处理报告.csv", "cache/geocoding.json", "人工补录.csv"])
def test_main_rejects_symlinked_default_paths_without_following_them(tmp_path, name):
    from photo_caption_print.cli import main

    external = tmp_path / "external"; external.mkdir()
    if name != "已选照片":
        (tmp_path / "已选照片").mkdir()
    link = tmp_path / name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(external, target_is_directory=True)
    pipeline = FakePipeline(successful_summary(), [])

    assert main(["--base-dir", str(tmp_path)], services(pipeline)) == 2
    assert not pipeline.calls


@pytest.mark.parametrize("argument", ["--input", "--output", "--report", "--cache", "--overrides"])
def test_main_rejects_explicit_symlink_paths_lexically(tmp_path, argument):
    from photo_caption_print.cli import main

    input_dir = tmp_path / "input"; input_dir.mkdir()
    external = tmp_path / "external"; external.mkdir()
    linked = tmp_path / "linked"; linked.symlink_to(external, target_is_directory=True)
    args = ["--input", str(input_dir), argument, str(linked)]
    pipeline = FakePipeline(successful_summary(), [])

    assert main(args, services(pipeline)) == 2
    assert not pipeline.calls


def test_make_pipeline_wires_real_adapter_callbacks_and_contains_callback_failures(tmp_path, capsys):
    from photo_caption_print.cli import _make_pipeline, _parser, main
    from photo_caption_print.layout import geometry_for

    profile = tmp_path / "sRGB.icc"; profile.write_bytes(b"profile")
    source = tmp_path / "photo with 空格.jpg"; source.write_bytes(b"source")
    output = tmp_path / "render.jpg"
    rendered: list[list[str]] = []

    class InvokingPipeline:
        def __init__(self, **kwargs): self.kwargs = kwargs
        def process_folder(self, *_args):
            assert list(self.kwargs["metadata_reader"]([source])) == []
            geometry = geometry_for(*self.kwargs["dimension_probe"](source))
            captions = self.kwargs["caption_fitter"](("日期", "地点"), geometry)
            self.kwargs["renderer"](source, output, geometry, captions)
            return successful_summary()

    injected = services(FakePipeline(successful_summary(), []))
    injected.update({
        "pipeline_factory": InvokingPipeline,
        "renderer": lambda command: rendered.append(command),
        "caption_fitter": lambda lines, geometry, font, measure: lines,
        "measure_text": lambda text, font, size: 1,
    })
    args = _parser().parse_args(["--input", str(tmp_path), "--srgb-profile", str(profile)])
    _make_pipeline(args, tmp_path / "cache.json", injected).process_folder(tmp_path, tmp_path / "out", tmp_path / "report.csv")
    assert rendered and rendered[0][0] == "magick"
    assert str(source) in " ".join(rendered[0])

    failing = services(FakePipeline(successful_summary(), []))
    failing.update({"pipeline_factory": InvokingPipeline, "dimension_probe": lambda path: (_ for _ in ()).throw(RuntimeError("probe failed"))})
    assert main(["--input", str(tmp_path), "--srgb-profile", str(profile)], failing) == 1
    assert "处理失败：probe failed" in capsys.readouterr().err


PROJECT_ROOT = Path(__file__).parents[1]


def _copy_script(tmp_path: Path, name: str) -> Path:
    script = tmp_path / "项目 空间" / "scripts" / name
    script.parent.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "scripts" / name, script)
    if name == "Install.command":
        shutil.copy2(PROJECT_ROOT / "scripts" / "Photo Caption Print.command", script.parent / "Photo Caption Print.command")
    return script


def test_shell_scripts_have_strict_mode_and_safe_explicit_commands():
    launcher = (PROJECT_ROOT / "scripts" / "Photo Caption Print.command").read_text(encoding="utf-8")
    installer = (PROJECT_ROOT / "scripts" / "Install.command").read_text(encoding="utf-8")

    for script in (launcher, installer):
        assert "set -euo pipefail" in script
        assert "sudo" not in script
        assert "curl" not in script
        assert ".zshrc" not in script
        assert ".zprofile" not in script
    assert '"$PYTHON" -m photo_caption_print.cli' in launcher
    assert '"$BREW" install python@3.13 exiftool imagemagick' in installer
    assert '"$PYTHON" -m venv --copies "$VENV"' in installer
    assert "请移除 .venv 后重新运行" in installer


def test_copied_venv_has_regular_runtime_and_launcher_accepts_it(tmp_path):
    """macOS venv --copies keeps the strict no-symlink launcher policy usable."""
    if subprocess.run([sys.executable, "-m", "venv", "--help"], capture_output=True, text=True).returncode != 0:
        pytest.skip("interpreter does not provide venv --copies")
    venv = tmp_path / "source-venv"
    created = subprocess.run([sys.executable, "-m", "venv", "--copies", str(venv)], capture_output=True, text=True)
    if created.returncode != 0:
        pytest.skip("interpreter cannot create a copied venv")
    python, activate = venv / "bin" / "python", venv / "bin" / "activate"
    assert python.is_file() and not python.is_symlink()
    assert activate.is_file() and not activate.is_symlink()

    script = _copy_script(tmp_path, "Photo Caption Print.command")
    root = script.parents[1]
    shutil.copytree(venv, root / ".venv", symlinks=True)
    stub = tmp_path / "stub" / "photo_caption_print"; stub.mkdir(parents=True)
    (stub / "__init__.py").write_text("", encoding="utf-8")
    (stub / "cli.py").write_text("print('stub workflow')\n", encoding="utf-8")
    launched = subprocess.run(["zsh", str(script)], env={**os.environ, "PYTHONPATH": str(stub.parent)}, text=True, capture_output=True)
    assert launched.returncode == 0, launched.stderr
    assert "stub workflow" in launched.stdout


def test_launcher_resolves_its_own_root_and_quotes_paths(tmp_path):
    script = _copy_script(tmp_path, "Photo Caption Print.command")
    root = script.parents[1]
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    (python.parent / "activate").write_text("export VIRTUAL_ENV=testing\n", encoding="utf-8")
    args_file = tmp_path / "arguments.txt"
    python.write_text("#!/bin/zsh\nprintf '%s\\n' \"$@\" > \"$FAKE_ARGS\"\n", encoding="utf-8")
    python.chmod(0o755)

    result = subprocess.run(["zsh", str(script)], cwd="/", env={**os.environ, "FAKE_ARGS": str(args_file)}, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert (root / "已选照片").is_dir()
    assert (root / "打印成品").is_dir()
    assert (root / "cache").is_dir()
    assert (root / "reports").is_dir()
    arguments = args_file.read_text(encoding="utf-8").splitlines()
    assert arguments[:3] == ["-m", "photo_caption_print.cli", "--base-dir"]
    assert str(root) in arguments
    assert str(root / "已选照片") in arguments
    assert str(root / "reports" / "处理报告.csv") in arguments


def test_installer_uses_brew_without_touching_shell_profiles(tmp_path):
    script = _copy_script(tmp_path, "Install.command")
    root = script.parents[1]
    bin_dir = tmp_path / "fake-bin"; bin_dir.mkdir()
    brew_log = tmp_path / "brew.log"
    prefix = tmp_path / "brew-prefix" / "python@3.13"
    python = prefix / "bin" / "python3.13"; python.parent.mkdir(parents=True)
    python.write_text(
        "#!/bin/zsh\n"
        "if [[ \"$1\" == \"-m\" && \"$2\" == \"venv\" && \"$3\" == \"--copies\" ]]; then mkdir -p \"$4/bin\"; cp \"$0\" \"$4/bin/python\"; print \"export VIRTUAL_ENV=\\\"$4\\\"\" > \"$4/bin/activate\"; exit 0; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    (bin_dir / "brew").write_text(
        "#!/bin/zsh\nprintf '%s\\n' \"$*\" >> \"$BREW_LOG\"\n"
        "if [[ \"$1\" == \"--version\" ]]; then print Homebrew; exit 0; fi\n"
        f"if [[ \"$1\" == \"--prefix\" ]]; then print {prefix}; exit 0; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    for command, body in {
        "exiftool": "#!/bin/zsh\nprint 13.00\n",
        "magick": "#!/bin/zsh\nif [[ \"$1\" == \"-list\" ]]; then print 'HEIC Helvetica'; else print ImageMagick; fi\n",
    }.items():
        (bin_dir / command).write_text(body, encoding="utf-8")
        (bin_dir / command).chmod(0o755)
    (bin_dir / "brew").chmod(0o755)

    result = subprocess.run(["zsh", str(script)], env={**os.environ, "PATH": f"{bin_dir}:/bin:/usr/bin", "BREW_LOG": str(brew_log)}, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert "install python@3.13 exiftool imagemagick" in brew_log.read_text(encoding="utf-8")
    assert (root / ".venv" / "bin" / "python").is_file()
    assert stat.S_IXUSR & (root / "scripts" / "Install.command").stat().st_mode
    assert stat.S_IXUSR & (root / "scripts" / "Photo Caption Print.command").stat().st_mode
    launcher = subprocess.run(["zsh", str(root / "scripts" / "Photo Caption Print.command")], env={**os.environ, "PATH": f"{bin_dir}:/bin:/usr/bin"}, text=True, capture_output=True)
    assert launcher.returncode == 0, launcher.stderr


@pytest.mark.parametrize(
    ("configured_font", "cjk_available", "helvetica_available", "accepted_font", "expected_status"),
    [
        ("Registered CJK", False, False, "Registered CJK", 0),
        ("Missing Font", False, False, "Registered CJK", 2),
        (None, True, True, "cjk.ttc", 0),
        (None, False, True, "helvetica.ttc", 0),
        (None, False, False, "Helvetica", 0),
    ],
)
def test_installer_validates_the_exact_selected_font_value(
    tmp_path, configured_font, cjk_available, helvetica_available, accepted_font, expected_status
):
    script = _copy_script(tmp_path, "Install.command")
    root = script.parents[1]
    cjk_font, helvetica_font = tmp_path / "cjk.ttc", tmp_path / "helvetica.ttc"
    if cjk_available:
        cjk_font.write_bytes(b"font")
    if helvetica_available:
        helvetica_font.write_bytes(b"font")
    script.write_text(
        script.read_text(encoding="utf-8")
        .replace("/System/Library/Fonts/STHeiti Medium.ttc", str(cjk_font))
        .replace("/System/Library/Fonts/Helvetica.ttc", str(helvetica_font)),
        encoding="utf-8",
    )
    bin_dir, prefix = tmp_path / "bin", tmp_path / "prefix"
    bin_dir.mkdir(); (prefix / "bin").mkdir(parents=True)
    python = prefix / "bin" / "python3.13"
    python.write_text(
        "#!/bin/zsh\n"
        "if [[ \"$1\" == -m && \"$2\" == venv ]]; then mkdir -p \"$4/bin\"; cp \"$0\" \"$4/bin/python\"; print \"export VIRTUAL_ENV=\\\"$4\\\"\" > \"$4/bin/activate\"; fi\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    (bin_dir / "brew").write_text(
        "#!/bin/zsh\n"
        "[[ \"$1\" == --version || \"$1\" == install ]] && exit 0\n"
        f"[[ \"$1\" == --prefix ]] && print {prefix}\n",
        encoding="utf-8",
    )
    (bin_dir / "exiftool").write_text("#!/bin/zsh\nprint 13.00\n", encoding="utf-8")
    (bin_dir / "magick").write_text(
        "#!/bin/zsh\n"
        "if [[ \"$1\" == -version ]]; then print ImageMagick; exit 0; fi\n"
        "if [[ \"$1\" == -list && \"$2\" == format ]]; then print HEIC; exit 0; fi\n"
        f"if [[ \"$1\" == -font && \"$2\" == \"{str(cjk_font) if accepted_font == 'cjk.ttc' else str(helvetica_font) if accepted_font == 'helvetica.ttc' else accepted_font}\" && \"$3\" == -pointsize && \"$4\" == 24 && \"$5\" == label:中文 && \"$6\" == -format && \"$7\" == %w && \"$8\" == info: ]]; then print 42; exit 0; fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    for command in ("brew", "exiftool", "magick"):
        (bin_dir / command).chmod(0o755)
    profile = tmp_path / "profile.icc"; profile.write_bytes(b"icc")
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:/bin:/usr/bin",
        "PHOTO_CAPTION_PRINT_SRGB_PROFILE": str(profile),
    }
    if configured_font is not None:
        environment["PHOTO_CAPTION_PRINT_FONT"] = configured_font

    result = subprocess.run(["zsh", str(script)], env=environment, text=True, capture_output=True)

    assert result.returncode == expected_status, (result.stdout, result.stderr)


@pytest.mark.parametrize("component", [".venv", ".venv/bin", ".venv/bin/python", ".venv/bin/activate"])
def test_launcher_rejects_every_symlinked_venv_component_without_external_execution(tmp_path, component):
    script = _copy_script(tmp_path, "Photo Caption Print.command")
    root = script.parents[1]
    external = tmp_path / "external"; external.mkdir()
    marker = tmp_path / "external-ran"
    python = root / ".venv" / "bin" / "python"; python.parent.mkdir(parents=True)
    python.write_text(f"#!/bin/zsh\ntouch {marker}\n", encoding="utf-8"); python.chmod(0o755)
    (python.parent / "activate").write_text(f"touch {marker}\n", encoding="utf-8")
    target = root / component
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()
    if component.endswith("bin"):
        (external / "python").write_text(f"#!/bin/zsh\ntouch {marker}\n", encoding="utf-8")
        (external / "python").chmod(0o755)
        (external / "activate").write_text(f"touch {marker}\n", encoding="utf-8")
    elif component.endswith("python"):
        (external / "python").write_text(f"#!/bin/zsh\ntouch {marker}\n", encoding="utf-8")
        (external / "python").chmod(0o755)
        target.parent.mkdir(parents=True, exist_ok=True)
    elif component.endswith("activate"):
        (external / "activate").write_text(f"touch {marker}\n", encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
    elif component == ".venv":
        (external / "bin").mkdir()
        (external / "bin" / "python").write_text(f"#!/bin/zsh\ntouch {marker}\n", encoding="utf-8")
        (external / "bin" / "python").chmod(0o755)
        (external / "bin" / "activate").write_text(f"touch {marker}\n", encoding="utf-8")
    target.symlink_to(external if component in {".venv", ".venv/bin"} else external / target.name, target_is_directory=component in {".venv", ".venv/bin"})

    result = subprocess.run(["zsh", str(script)], text=True, capture_output=True)

    assert result.returncode == 2
    assert "虚拟环境" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("component", [".venv", ".venv/bin", ".venv/bin/python", ".venv/bin/activate", "partial"])
def test_installer_rejects_unsafe_or_partial_venv_before_external_execution(tmp_path, component):
    script = _copy_script(tmp_path, "Install.command")
    root = script.parents[1]
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    prefix = tmp_path / "prefix"; (prefix / "bin").mkdir(parents=True)
    marker = tmp_path / "external-ran"
    (prefix / "bin" / "python3.13").write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
    (prefix / "bin" / "python3.13").chmod(0o755)
    (bin_dir / "brew").write_text(f"#!/bin/zsh\n[[ \"$1\" == --version ]] && exit 0\n[[ \"$1\" == install ]] && exit 0\n[[ \"$1\" == --prefix ]] && print {prefix}\n", encoding="utf-8")
    (bin_dir / "brew").chmod(0o755)
    venv_bin = root / ".venv" / "bin"; venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text(f"#!/bin/zsh\ntouch {marker}\n", encoding="utf-8"); (venv_bin / "python").chmod(0o755)
    (venv_bin / "activate").write_text(f"touch {marker}\n", encoding="utf-8")
    external = tmp_path / "external"; external.mkdir()
    target = root / ".venv" / "bin" if component == ".venv/bin" else root / component
    if component == "partial":
        (venv_bin / "python").unlink(); (venv_bin / "activate").unlink()
    else:
        if target.is_dir(): shutil.rmtree(target)
        else: target.unlink()
        if component in {".venv", ".venv/bin"}:
            (external / "bin").mkdir(exist_ok=True)
            payload = external / "bin" / "python"; payload.write_text(f"#!/bin/zsh\ntouch {marker}\n", encoding="utf-8"); payload.chmod(0o755)
            (external / "bin" / "activate").write_text(f"touch {marker}\n", encoding="utf-8")
            target.symlink_to(external if component == ".venv" else external / "bin", target_is_directory=True)
        else:
            payload = external / target.name; payload.write_text(f"#!/bin/zsh\ntouch {marker}\n", encoding="utf-8"); payload.chmod(0o755)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(payload)

    result = subprocess.run(["zsh", str(script)], env={**os.environ, "PATH": f"{bin_dir}:/bin:/usr/bin"}, text=True, capture_output=True)

    assert result.returncode == 2
    assert "虚拟环境" in result.stderr
    assert not marker.exists()


def _fake_launcher_environment(root: Path, tmp_path: Path, *, exit_code: int = 0) -> Path:
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    activate = python.parent / "activate"
    activate.write_text(f'export VIRTUAL_ENV="{root / ".venv"}"\nexport PATH="$VIRTUAL_ENV/bin:$PATH"\n', encoding="utf-8")
    marker = tmp_path / "launcher-env.txt"
    python.write_text(f"#!/bin/zsh\nprintf '%s\\n' \"$VIRTUAL_ENV\" > {marker}\nexit {exit_code}\n", encoding="utf-8")
    python.chmod(0o755)
    return marker


def test_launcher_activates_venv_and_handles_symlinked_invocation(tmp_path):
    script = _copy_script(tmp_path, "Photo Caption Print.command")
    root = script.parents[1]
    marker = _fake_launcher_environment(root, tmp_path)
    linked = script.with_name("启动.command"); linked.symlink_to(script.name)

    result = subprocess.run(["zsh", str(linked)], cwd="/", text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").strip() == str(root / ".venv")


def test_launcher_rejects_symlinked_user_directory_and_missing_venv_uses_exit_trap(tmp_path):
    script = _copy_script(tmp_path, "Photo Caption Print.command")
    root = script.parents[1]
    external = tmp_path / "external"; external.mkdir()
    (root / "已选照片").symlink_to(external, target_is_directory=True)

    unsafe = subprocess.run(["zsh", str(script)], text=True, capture_output=True)
    assert unsafe.returncode != 0
    assert "符号链接" in unsafe.stderr

    (root / "已选照片").unlink()
    missing = subprocess.run(["zsh", str(script)], text=True, capture_output=True)
    assert missing.returncode == 2
    assert "Install.command" in missing.stderr
    content = script.read_text(encoding="utf-8")
    assert "trap" in content and "EXIT" in content


def test_launcher_passes_regular_override_but_skips_symlink_override(tmp_path):
    script = _copy_script(tmp_path, "Photo Caption Print.command")
    root = script.parents[1]
    marker = _fake_launcher_environment(root, tmp_path)
    arguments = tmp_path / "arguments.txt"
    (root / ".venv" / "bin" / "python").write_text(
        f"#!/bin/zsh\nprintf '%s\\n' \"$@\" > {arguments}\nprintf '%s\\n' \"$VIRTUAL_ENV\" > {marker}\n",
        encoding="utf-8",
    )
    (root / ".venv" / "bin" / "python").chmod(0o755)
    override = root / "人工补录.csv"; override.write_text("filename,captured_at,location,device\n", encoding="utf-8")

    assert subprocess.run(["zsh", str(script)], text=True, capture_output=True).returncode == 0
    assert "--overrides" in arguments.read_text(encoding="utf-8").splitlines()
    override.unlink(); override.symlink_to(tmp_path / "outside.csv")
    assert subprocess.run(["zsh", str(script)], text=True, capture_output=True).returncode == 0
    assert "--overrides" not in arguments.read_text(encoding="utf-8").splitlines()


def test_scripts_bound_symlink_resolution_and_installer_avoids_pipe_grep_head():
    launcher = (PROJECT_ROOT / "scripts" / "Photo Caption Print.command").read_text(encoding="utf-8")
    installer = (PROJECT_ROOT / "scripts" / "Install.command").read_text(encoding="utf-8")
    for script in (launcher, installer):
        assert "MAX_SYMLINKS" in script
        assert "符号链接循环" in script
    assert "magick -list format |" not in installer
    assert "magick -list font |" not in installer
    assert "magick -version |" not in installer
    assert "PHOTO_CAPTION_PRINT_SRGB_PROFILE" in installer
    assert 'MACOS_CJK_FONT="/System/Library/Fonts/STHeiti Medium.ttc"' in installer
    assert 'SELECTED_FONT="$PHOTO_CAPTION_PRINT_FONT"' in installer
    assert 'SELECTED_FONT="$MACOS_CJK_FONT"' in installer
    assert "/System/Library/Fonts/STHeiti Medium.ttc" in installer
    assert "/System/Library/Fonts/Helvetica.ttc" in installer
    assert 'magick -font "$SELECTED_FONT" -pointsize 24 "label:中文" -format "%w" info:' in installer


def test_cli_defaults_to_the_macos_cjk_font_file_when_available(monkeypatch):
    from photo_caption_print import cli

    monkeypatch.setattr(cli.Path, "is_file", lambda path: str(path) == "/System/Library/Fonts/STHeiti Medium.ttc")

    assert cli._default_font() == "/System/Library/Fonts/STHeiti Medium.ttc"


def test_cli_falls_back_to_the_macos_helvetica_font_file_when_cjk_font_is_unavailable(monkeypatch):
    from photo_caption_print import cli

    monkeypatch.setattr(cli.Path, "is_file", lambda path: str(path) == "/System/Library/Fonts/Helvetica.ttc")

    assert cli._default_font() == "/System/Library/Fonts/Helvetica.ttc"


def test_cli_falls_back_to_the_helvetica_font_name_when_macos_font_files_are_unavailable(monkeypatch):
    from photo_caption_print import cli

    monkeypatch.setattr(cli.Path, "is_file", lambda path: False)

    assert cli._default_font() == "Helvetica"


@pytest.mark.parametrize("script_name", ["Photo Caption Print.command", "Install.command"])
def test_scripts_detect_a_symlink_cycle_when_readlink_repeats_the_invocation(tmp_path, script_name):
    script = _copy_script(tmp_path, script_name)
    loop = script.with_name("loop.command"); loop.symlink_to(script.name)
    bin_dir = tmp_path / "fake-bin"; bin_dir.mkdir()
    (bin_dir / "readlink").write_text("#!/bin/zsh\nprint -- \"$LOOP_PATH\"\n", encoding="utf-8")
    (bin_dir / "readlink").chmod(0o755)

    result = subprocess.run(["zsh", str(loop)], env={**os.environ, "PATH": f"{bin_dir}:/bin:/usr/bin", "LOOP_PATH": str(loop)}, text=True, capture_output=True, timeout=5)

    assert result.returncode == 2
    assert "错误：符号链接循环。" in result.stderr


@pytest.mark.parametrize("mode", ["missing_brew", "broken_brew", "install_failure", "prefix_failure", "prefix_empty", "missing_python", "partial_venv", "exiftool_failure", "magick_version_failure", "format_failure", "font_failure", "no_heic", "no_font", "missing_profile", "unsafe_venv"])
def test_installer_reports_all_setup_failures_without_privileged_actions(tmp_path, mode):
    script = _copy_script(tmp_path, "Install.command")
    root = script.parents[1]
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    prefix = tmp_path / "prefix"; (prefix / "bin").mkdir(parents=True)
    profile = tmp_path / "profile.icc"; profile.write_bytes(b"icc")
    brew = bin_dir / "brew"
    brew.write_text(
        "#!/bin/zsh\n"
        f"if [[ \"$1\" == \"--version\" ]]; then [[ \"{mode}\" == broken_brew ]] && exit 1; print Homebrew; exit 0; fi\n"
        f"if [[ \"$1\" == install ]]; then [[ \"{mode}\" == install_failure ]] && exit 1; exit 0; fi\n"
        f"if [[ \"$1\" == --prefix ]]; then [[ \"{mode}\" == prefix_failure ]] && exit 1; [[ \"{mode}\" == prefix_empty ]] && exit 0; print {prefix}; exit 0; fi\n",
        encoding="utf-8",
    )
    brew.chmod(0o755)
    python = prefix / "bin" / "python3.13"
    if mode != "missing_python":
        python.write_text("#!/bin/zsh\nif [[ \"$2\" == venv && \"$3\" == --copies ]]; then mkdir -p \"$4/bin\"; cp \"$0\" \"$4/bin/python\"; print \"export VIRTUAL_ENV=\\\"$4\\\"\" > \"$4/bin/activate\"; fi\n", encoding="utf-8")
        python.chmod(0o755)
    for command, body in {
        "exiftool": f"#!/bin/zsh\n[[ \"{mode}\" == exiftool_failure ]] && exit 1\nprint 1\n",
        "magick": f"#!/bin/zsh\nif [[ \"$1\" == -list && \"$2\" == format ]]; then [[ \"{mode}\" == format_failure ]] && exit 1; [[ \"{mode}\" == no_heic ]] && print JPEG || print HEIC; exit 0; fi\nif [[ \"$1\" == -font ]]; then [[ \"{mode}\" == font_failure || \"{mode}\" == no_font ]] && exit 1; print 42; exit 0; fi\n[[ \"{mode}\" == magick_version_failure ]] && exit 1\nprint ImageMagick\n",
    }.items():
        path = bin_dir / command; path.write_text(body, encoding="utf-8"); path.chmod(0o755)
    if mode == "unsafe_venv":
        (root / ".venv").symlink_to(tmp_path / "outside")
    if mode == "partial_venv":
        (root / ".venv" / "bin").mkdir(parents=True)
    path = "/bin:/usr/bin" if mode == "missing_brew" else f"{bin_dir}:/bin:/usr/bin"
    env = {
        **os.environ,
        "PATH": path,
        "PHOTO_CAPTION_PRINT_FONT": str(tmp_path / "absent-font.ttc"),
        "PHOTO_CAPTION_PRINT_SRGB_PROFILE": str(profile if mode != "missing_profile" else tmp_path / "absent.icc"),
    }

    result = subprocess.run(["zsh", str(script)], env=env, text=True, capture_output=True)

    assert result.returncode == 2, (mode, result.stdout, result.stderr)
