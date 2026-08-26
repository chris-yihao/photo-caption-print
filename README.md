# Photo Caption Print

A macOS folder-based batch tool for turning exported iPhone photos into print-ready 6×4-inch JPEGs with a clean white information border.

The planned workflow reads available photo metadata, preserves the full image without cropping, and adds a two-line caption containing the capture date, weekday, time, location, and device. Missing fields are omitted automatically so processed or metadata-free photos still have a clean layout.

## Planned workflow

1. Export selected photos from Apple Photos on macOS.
2. Review and select photos with XnView MP.
3. Place the selected files in an input folder.
4. Double-click the local batch tool.
5. Collect print-ready JPEGs and a CSV processing report.

## Output standard

- 6×4 inches at 300 PPI
- 1800×1200 pixels for landscape output
- 1200×1800 pixels for portrait output
- sRGB high-quality JPEG
- Full photo preserved without cropping
- Landscape, portrait, and square photos supported
- Two-line documentary-style caption on a white lower border
- Graceful layout when metadata is partly or entirely missing

## Planned free dependencies

- [ExifTool](https://exiftool.org/) for metadata extraction
- [ImageMagick](https://imagemagick.org/) for image composition and text rendering
- [XnView MP](https://www.xnview.com/en/xnviewmp/) for optional photo selection

The tool will run locally on macOS. Original photos will not be modified or overwritten. Network access will only be needed when converting uncached GPS coordinates into readable place names.

## Status

Design approved; implementation planning is next. See [the design specification](docs/design.md).

## Author

Chris

## License

[MIT](LICENSE)
