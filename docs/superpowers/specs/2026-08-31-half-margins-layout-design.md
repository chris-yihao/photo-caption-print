# Half-Margins Print Layout Design

**Author:** Chris

## Scope

Only print geometry changes. Metadata extraction, captions, geocoding, fonts,
reports, installation, file safety, output format, and processing flow remain
unchanged.

## Geometry

- Keep 6×4-inch output at 300 PPI: landscape/square `1800×1200`, portrait
  `1200×1800`.
- Landscape: reduce side margins from 80 to 40 pixels and the caption area from
  240 to 120 pixels. Use a centered `1720×1080` crop frame at `(40, 0)`.
- Portrait: reduce the outer inset from 40 to 20 pixels and the caption area
  from 360 to 180 pixels. Keep the photo uncropped.
- Square: reduce the outer inset from 60 to 30 pixels and the caption area from
  240 to 120 pixels. Keep the photo uncropped.
- Preserve all existing font sizes and two-line caption formatting.

## Verification

- Update exact geometry tests for landscape, portrait, square, and panorama.
- Update native render bounds to prove the landscape frame is exactly
  `1720×1080+40+0`.
- Run the Python 3.13 suite and shell syntax checks once after implementation.
