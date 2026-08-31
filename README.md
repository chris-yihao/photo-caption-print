# Photo Caption Print｜照片信息边框打印工具

[中文说明](#中文说明) · [English](#english)

## 中文说明

### 当前状态

Photo Caption Print 是一个可在 macOS 本地运行的文件夹批处理工具，当前版本已完成核心流程、命令行入口、CSV 报告和双击启动脚本。它把 Apple“照片”导出的照片制作成适合冲印的 6×4 英寸 JPEG，并在白色边框中加入可用的拍摄信息。

### 要求

- macOS（建议使用最新系统）
- [Homebrew](https://brew.sh/)
- Python 3.11 或 3.13
- ExifTool 和 ImageMagick 7：`brew install exiftool imagemagick`

可选安装 [XnView MP](https://www.xnview.com/en/xnviewmp/) 用于浏览、评分和筛选照片；它不是本工具的运行依赖。

### 推荐工作流

1. 在 macOS“照片”中选择照片，导出时保留当前编辑，并保留位置元数据；一次最多先导出最早的 100 张到项目文件夹的 `待筛选照片`（不要直接导入 `已选照片`）。
2. （可选）打开 XnView MP 的 `待筛选照片` 文件夹，切换到缩略图视图；为保留照片加星级、颜色标签或 Rating，按保留评级过滤。
3. 清理并准备 `已选照片` 输入文件夹后，只把筛选出的照片复制/移动进去；不要把未筛选的导出文件留在 `已选照片`，也不要在 XnView MP 中删除 `待筛选照片` 内的原始导出文件。
4. 首次使用双击 `scripts/Install.command` 安装依赖和本地虚拟环境。
5. 双击 `scripts/Photo Caption Print.command`；完成后从 `打印成品` 取 JPEG，并查看 `reports/处理报告.csv`。

安装脚本只在项目目录创建 `.venv`，不会修改 shell 配置文件。也可以手动安装：

```zsh
brew install python@3.13 exiftool imagemagick
python3.13 -m venv --copies .venv
.venv/bin/python -m pip install ".[dev]"
```

### 元数据与人工补录

程序读取可用的拍摄日期、星期、时间、GPS 地点和设备型号。元数据缺失时只隐藏相应字段；没有日期或设备也能生成干净的输出。需要补录时，传入 UTF-8 CSV（表头必须完全为 `filename,captured_at,location,device`），例如：

```csv
filename,captured_at,location,device
IMG_0001.JPG,2024-05-06T07:08:09,杭州,Phone
```

补录仅匹配文件名，不允许路径组件；无效行会留在报告警告中。报告每张输入照片一行，包含输入、输出、状态、字段、有效 PPI、警告和错误。成功、警告、跳过和失败彼此隔离，单张坏文件不会中断同批其他照片；同名不同扩展名会自动生成不冲突的输出名。

### 输出标准

- 6×4 英寸、300 PPI：横版 1800×1200，竖版 1200×1800
- JPEG、sRGB、质量 94，并嵌入 sRGB ICC 配置
- 横图以居中的轻微裁切填满 1640×960 像素照片框，左右各留 80 像素白边；竖图和正方形完整保留、不裁切，底部白边放置一行或两行信息
- 支持横图、竖图、正方形和 EXIF 方向；低分辨率照片仍会输出，并在报告中提示有效 PPI
- 输出会移除 GPS 等隐私元数据；原图永不覆盖或修改

### 隐私与联网

GPS 首次在没有现成地点且缓存没有结果时，通过 Nominatim reverse API 转成可读地点；结果会写入本地缓存，之后优先复用缓存。请求之间至少间隔 1 秒，`--offline` 只读缓存，不发起联网请求。服务失败时，照片仍会继续处理，失败原因会写入 CSV 报告。输出 JPEG 会剥离 GPS。除这一项可选的反向地理编码外，处理在本机完成，不需要密钥。

### 常见问题

- `brew` 或依赖找不到：安装 Homebrew，运行 `brew install python@3.13 exiftool imagemagick`，再重试安装脚本。
- HEIC 无法读取：确认 `magick -list format` 中有 HEIC/HEIF delegate；用 `magick -version` 检查 ImageMagick。
- 字体或中文缺字：使用 `--font` 指定已安装字体，并用 `magick -list font` 查找字体名。
- 地点字符串太长：程序先去掉地点的细节部分，再逐步缩小到最小字体；仍放不下时会加省略号并在 CSV 中警告。完整地点仍保留在报告的元数据字段中，也可用人工补录 CSV 指定完整文字。
- 颜色或 ICC 报错：确认系统存在 `/System/Library/ColorSync/Profiles/sRGB Profile.icc`，且 ImageMagick 支持 profile。
- 照片缺字段：这是允许的；用人工补录 CSV 提供日期、地点或设备。
- 离线地点为空：使用缓存，或稍后联网运行；可用 `--offline` 明确禁止网络。
- 文件很多或处理很慢：先按批次处理；首次未缓存 GPS 地点会遵守 Nominatim 的 1 秒间隔。
- 低分辨率警告：竖图和正方形仍不裁切，横图仍使用上述居中轻微裁切；报告的有效 PPI 用于判断冲印清晰度。
- 符号链接被拒绝：输入、输出、报告和缓存路径必须是实际目录/文件路径，不要把它们替换成 symlink。
- 虚拟环境错误：删除项目目录内的 `.venv` 后重新双击安装脚本；不要复用带 symlink 的外部环境。

### 命令行

在项目根目录运行 `.venv/bin/python -m photo_caption_print.cli`。常用选项：`--base-dir`、`--input`、`--output`、`--report`、`--overrides`、`--cache`、`--offline`、`--nominatim-url`、`--font` 和 `--srgb-profile`。默认目录分别为 `已选照片`、`打印成品`、`reports/处理报告.csv`、`cache/geocoding.json` 和 `人工补录.csv`。

退出码为 0（全部处理成功）、1（至少一张照片失败，但报告已写入）、2（参数、依赖、路径或报告错误）和 130（用户取消）。

### 开发

```zsh
python3.13 -m pip install ".[dev]"
python3.13 -m pytest -v
zsh -n scripts/*.command
```

单元测试覆盖日期、地点、版式、元数据、CLI、报告和安全边界；`tests/integration/test_end_to_end.py` 在真实 ExifTool/ImageMagick 存在时生成临时合成图片，验证完整 CLI 流程、方向、横图裁切正确性、竖图/正方形边缘保留、ICC/GPS 清理、重复文件名和失败隔离。实现分为 `metadata`（提取）、`geocode`（缓存反向地理编码）、`captions`（字段格式化）、`layout`（几何与 ImageMagick 命令）和 `pipeline`（批处理、原子输出、报告）。

## English

### Status

Photo Caption Print is a local, folder-based macOS batch tool. The current version includes the core pipeline, CLI, CSV report, and double-click launcher. It turns photos exported from Apple Photos into 6×4-inch print-ready JPEGs with available capture information in a white border.

### Requirements

- macOS (latest release recommended)
- [Homebrew](https://brew.sh/)
- Python 3.11 or 3.13
- ExifTool and ImageMagick 7: `brew install exiftool imagemagick`

[XnView MP](https://www.xnview.com/en/xnviewmp/) is optional for browsing, rating, and selecting photos; it is not a runtime dependency.

### Recommended workflow

1. Select photos in macOS Photos and export them with current edits preserved and location metadata retained; start with the earliest 100 photos per batch in the project's `待筛选照片` staging folder (not `已选照片`).
2. Optionally open XnView MP, open the `待筛选照片` folder, and switch to thumbnails. Assign stars, color labels, or a Rating, then filter by the rating you want to retain.
3. Clear and prepare the `已选照片` input folder, then copy or move only the filtered selections into it. Never leave unfiltered exports in `已选照片`, and do not delete the original exports from `待筛选照片` in XnView MP.
4. On first use, double-click `scripts/Install.command` to install dependencies and the local virtual environment.
5. Double-click `scripts/Photo Caption Print.command`; collect JPEGs from `打印成品` and review `reports/处理报告.csv`.

The installer creates `.venv` only inside the project and does not edit shell profiles. Manual installation is also supported:

```zsh
brew install python@3.13 exiftool imagemagick
python3.13 -m venv --copies .venv
.venv/bin/python -m pip install ".[dev]"
```

### Metadata and manual CSV

The tool reads available capture date, weekday, time, GPS location, and device model. Missing fields are omitted, so files with partial or no metadata still receive a clean layout. For manual values, pass a UTF-8 CSV whose header is exactly `filename,captured_at,location,device`:

```csv
filename,captured_at,location,device
IMG_0001.JPG,2024-05-06T07:08:09,Hangzhou,Phone
```

Overrides match basenames only and reject path components; invalid rows appear as report warnings. The report has one row per input, with source, output, status, fields, effective PPI, warnings, and errors. Success, warning, skipped, and failed items are isolated; one unreadable file does not stop the rest of a batch. Duplicate stems with different extensions receive unique output names.

### Output standard

- 6×4 inches at 300 PPI: 1800×1200 landscape, 1200×1800 portrait
- JPEG, sRGB, quality 94, with an embedded sRGB ICC profile
- Landscape photos use a mild centered crop to fill a 1640×960-pixel photo frame with 80-pixel side margins; portrait and square photos remain uncropped, and the lower white border holds one or two caption lines
- Landscape, portrait, square, and EXIF-orientation inputs are supported; low-resolution files still render and report their effective PPI
- Generated files remove GPS and other private metadata; originals are never overwritten or modified

### Privacy and network use

On first use, GPS coordinates are sent to the Nominatim reverse API only when no location is already available and the local cache has no result; the result is cached and reused on later runs. Requests are spaced at least one second apart, and `--offline` uses only the cache and never makes a network request. If the service fails, the photo continues processing and the failure is explained in the CSV report. Generated JPEGs strip GPS. Processing is otherwise local and requires no secrets.

### Troubleshooting

- Missing `brew` or dependencies: install Homebrew, run `brew install python@3.13 exiftool imagemagick`, then retry the installer.
- HEIC cannot be read: ensure `magick -list format` includes an HEIC/HEIF delegate and inspect `magick -version`.
- Missing glyphs or font errors: pass `--font` with an installed font and inspect `magick -list font`.
- Long location strings: the tool drops location details first, then reduces the font to its documented minimum; if it still cannot fit, it adds an ellipsis and records a CSV warning. The full location remains in the report metadata fields, or you can provide the full text through the manual override CSV.
- Color or ICC errors: verify `/System/Library/ColorSync/Profiles/sRGB Profile.icc` exists and ImageMagick supports profiles.
- Missing photo fields: this is supported; supply date, location, or device values through the manual CSV.
- Offline location lookup: use cached results or retry online later; use `--offline` to explicitly forbid network access.
- Long runs: process smaller batches; uncached GPS lookups observe the one-second Nominatim interval.
- Low-resolution warnings: portrait and square photos remain uncropped, while landscape photos retain the centered mild crop described above; effective PPI in the report indicates expected print sharpness.
- Symlink rejection: input, output, report, and cache paths must be real directories/files, not symlink replacements.
- Virtual-environment errors: remove the project's `.venv` and run the installer again; do not reuse an external symlinked environment.

### CLI

Run `.venv/bin/python -m photo_caption_print.cli` from the project root. Common options are `--base-dir`, `--input`, `--output`, `--report`, `--overrides`, `--cache`, `--offline`, `--nominatim-url`, `--font`, and `--srgb-profile`. Defaults are `已选照片`, `打印成品`, `reports/处理报告.csv`, `cache/geocoding.json`, and `人工补录.csv`.

Exit codes are 0 (all items succeeded), 1 (at least one photo failed but the report was written), 2 (argument, dependency, path, or report error), and 130 (cancelled by the user).

### Development

```zsh
python3.13 -m pip install ".[dev]"
python3.13 -m pytest -v
zsh -n scripts/*.command
```

Unit tests cover dates, locations, layout, metadata, CLI behavior, reports, and security boundaries. `tests/integration/test_end_to_end.py` creates temporary synthetic images when real ExifTool/ImageMagick are available and verifies the complete CLI flow, orientation, correct landscape cropping, preserved portrait/square edges, ICC/GPS cleanup, duplicate names, and failure isolation. The implementation is split into `metadata` (extraction), `geocode` (cached reverse geocoding), `captions` (field formatting), `layout` (geometry and ImageMagick commands), and `pipeline` (batching, atomic output, and reports).

## Author｜作者

Chris

## License｜许可证

[MIT](LICENSE)
