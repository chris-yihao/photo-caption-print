# Integration fixtures

The end-to-end test creates all images at runtime with ImageMagick and writes
synthetic metadata with ExifTool. No personal photographs, personal device
identifiers, GPS coordinates, or other private assets belong in this directory.

The coordinates used by the test are deliberately fictional test values. Keep
fixtures deterministic, disposable, and safe to publish. If a new test needs a
metadata edge case, generate it in `tmp_path` rather than committing an image.
