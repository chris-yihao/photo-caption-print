# Photo Caption Print｜照片信息边框打印工具

[中文](#中文说明) · [English](#english)

## 中文说明

Photo Caption Print 是一款计划用于 macOS 的文件夹式批处理工具。它可以把从 iPhone／Apple“照片”App 导出的照片生成适合 6×4 英寸冲印的 JPEG，并在底部白边中自动加入拍摄信息。

工具会读取照片中仍然存在的拍摄日期、星期、时间、地点和设备信息，完整保留画面而不裁切。缺少的字段会自动隐藏，因此经过其他软件处理、部分或全部丢失元数据的照片仍能保持干净的版式。

### 计划工作流程

1. 从 macOS 的 Apple“照片”App 导出已选照片。
2. 使用 XnView MP 浏览、评分和筛选。
3. 将保留的照片放入“已选照片”文件夹。
4. 双击本地批处理工具。
5. 从“打印成品”文件夹取得 JPEG，并查看 CSV 处理报告。

### 输出标准

- 6×4 英寸、300 PPI
- 横版 1800×1200 像素
- 竖版 1200×1800 像素
- sRGB 高质量 JPEG
- 完整保留照片，不裁切
- 支持横图、竖图和正方形照片
- 底部白边采用双行纪实信息版式
- 元数据部分或全部缺失时自动重新排版

### 计划使用的免费软件

- [ExifTool](https://exiftool.org/)：提取照片元数据
- [ImageMagick](https://imagemagick.org/)：合成画布与渲染文字
- [XnView MP](https://www.xnview.com/en/xnviewmp/)：可选的照片筛选工具

工具将在 Mac 本地运行，不修改或覆盖原照片。只有把 GPS 坐标转换成可读地点、且本地没有缓存结果时才需要联网。生成的冲印文件将移除 GPS 等隐私元数据。

### 当前状态

设计和实施计划已经完成，开发工作正在进行。参阅[设计规格](docs/design.md)和[实施计划](docs/superpowers/plans/2026-08-26-photo-caption-print-implementation.md)。

---

## English

Photo Caption Print is a planned folder-based macOS batch tool for turning photos exported from an iPhone or Apple Photos into print-ready 6×4-inch JPEGs with a clean white information border.

The tool reads any available capture date, weekday, time, location, and device metadata while preserving the full image without cropping. Missing fields are omitted automatically, so photos processed by other software—or photos with partially or entirely missing metadata—still receive a clean layout.

### Planned workflow

1. Export selected photos from Apple Photos on macOS.
2. Review, rate, and select photos with XnView MP.
3. Place the selected files in the input folder.
4. Double-click the local batch tool.
5. Collect print-ready JPEGs and review the CSV processing report.

### Output standard

- 6×4 inches at 300 PPI
- 1800×1200 pixels for landscape output
- 1200×1800 pixels for portrait output
- High-quality sRGB JPEG
- Full photo preserved without cropping
- Landscape, portrait, and square photos supported
- Two-line documentary-style caption on a white lower border
- Graceful reflow when metadata is partly or entirely missing

### Planned free dependencies

- [ExifTool](https://exiftool.org/) for metadata extraction
- [ImageMagick](https://imagemagick.org/) for image composition and text rendering
- [XnView MP](https://www.xnview.com/en/xnviewmp/) for optional photo selection

The tool will run locally on macOS and will not modify or overwrite original photos. Network access is needed only when uncached GPS coordinates must be converted into readable place names. Generated print files will have private GPS metadata removed.

### Status

The design and implementation plan are complete, and development is in progress. See the [design specification](docs/design.md) and [implementation plan](docs/superpowers/plans/2026-08-26-photo-caption-print-implementation.md).

## Author｜作者

Chris

## License｜许可证

[MIT](LICENSE)
