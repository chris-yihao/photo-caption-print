# Portrait Visible Caption Centering Design

## Goal

Vertically center the actual visible caption glyphs in the 180-pixel bottom
white area of portrait prints, for both one-line and two-line captions.

The supplied `1200×1800` example demonstrates the defect: its single date line
occupies caption-relative Y `90–128`, whose center is about 20 pixels below the
caption area's center at Y `90`.

## Recommended Rendering

For every portrait layout with at least one nonblank caption line, render the
caption into a transparent `1200×180` layer:

1. draw a single available line at relative Y `0`; or
2. draw both lines at relative Y `0` and at the existing 58-pixel line offset;
3. trim the transparent layer to its actual visible glyph bounds;
4. extend the trimmed group back to `1200×180` using center gravity;
5. composite the centered layer at portrait caption top Y `1620`.

The approach uses the fitted font sizes, so it remains centered if long text
causes either line to shrink. Horizontal centering and the approved two-line
separation remain unchanged.

## Scope

- Portrait one-line captions use actual visible-glyph centering.
- Portrait two-line captions use actual visible-glyph group centering.
- The existing square two-line visible-centering path remains unchanged.
- Square one-line captions and all landscape captions retain their current
  rendering behavior.
- Prints with no caption text do not create a caption layer.

## Implementation Boundary

Reuse one caption-layer argument builder for portrait captions and the existing
square two-line path. The helper receives the geometry, present lines, fitted
font sizes, font, and line offset, and returns ImageMagick arguments for the
trim-center-extend-composite operation. Direct annotation remains the fallback
for layouts outside this scope.

## Verification

Tests will prove:

- portrait single-line commands contain a transparent `1200×180` layer;
- portrait two-line commands use the existing 58-pixel relative line offset;
- empty portrait captions, landscape captions, and square single-line captions
  retain their intended paths;
- live ImageMagick renders of portrait single-line and two-line captions have
  top and bottom whitespace that differs by no more than one pixel, expressed
  as `abs(2 * ink_y + ink_height - 180) <= 1`; an odd glyph height cannot be
  divided evenly across an even-height caption area;
- the complete Python 3.13 suite and launcher syntax checks remain clean.
