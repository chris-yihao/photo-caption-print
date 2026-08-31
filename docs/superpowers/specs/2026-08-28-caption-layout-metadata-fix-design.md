# Caption Metadata and Landscape Layout Fix

**Author:** Chris
**Date:** 2026-08-28

## Goal

Correct metadata extraction so captions show the capture date, Chinese weekday,
time, reverse-geocoded location, and device. Refine landscape prints to reduce
the excessive side margins while preserving the existing 6×4 inch, 300 PPI
output standard.

## Metadata and captions

- Request ExifTool family-0 group names so the parser receives stable keys such
  as `EXIF:DateTimeOriginal`, `EXIF:GPSLatitude`, and `EXIF:Model`.
- Format the primary line as
  `YYYY年MM月DD日 · 星期X · HH:MM`.
- When valid GPS coordinates exist, use the existing reverse-geocoding service
  and cache to produce a city plus useful local detail when available.
- Format the secondary line as `location / device`, hiding only fields that are
  genuinely unavailable.
- If reverse geocoding fails, retain date and device and record the precise
  location warning in the CSV report.

## Landscape layout

- Keep the final canvas at 1800×1200 pixels with 300 PPI metadata.
- Reserve the bottom 240 pixels for the two caption lines.
- Use a 1640×960 landscape photo frame, centered horizontally, leaving exactly
  80 pixels of white margin on each side.
- Fill that frame with a center crop. For ordinary 4:3 landscape photos, crop
  the top and bottom evenly.
- Reduce landscape type from 34/24 pixels to 28/20 pixels, with minimum sizes
  of 18/15 pixels for long captions.

## Portrait and square layout

Portrait and square photos retain the existing uncropped behavior. Square type
uses 28/20 pixels with 18/15-pixel minimums. Portrait type uses 42/30 pixels
with 27/22-pixel minimums. Their image geometry is otherwise unchanged.

## Safety and compatibility

- Never modify source photos.
- Continue writing only program-owned output files and the existing report and
  cache locations.
- Preserve graceful layouts for partially or entirely missing metadata.
- Keep the macOS Helvetica font-file fallback introduced for current
  ImageMagick versions.

## Verification

- Unit-test ExifTool group selection, Chinese weekday formatting, geometry,
  center-crop command construction, and missing-location warnings.
- Render an externally supplied `photo.jpeg` as the primary visual fixture.
- Confirm the output is 1800×1200 at 300 PPI, displays `星期二`, includes the
  resolved location when networking succeeds, uses narrow side margins, and
  leaves the input byte-for-byte unchanged.
- Re-run the existing non-native test suite and relevant native smoke tests.
