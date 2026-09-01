# Near-Square Layout and Device Name Fix Design

## Goal

Correct the output for edited photos that are visually square but differ by a
few pixels, and replace confirmed Apple hardware identifiers with readable
device names.

The production example is `IMG_0466.jpeg`: its dimensions are `956×961` and
its EXIF model is `iPhone7,2`.

## Near-Square Classification

A source is square-like when:

```text
abs(width - height) / max(width, height) <= 0.02
```

Square-like sources use the existing square layout:

- `1800×1200` 6×4-inch canvas at 300 PPI;
- uncropped proportional photo fitting;
- square margins and caption-area dimensions;
- the existing two-line caption-group centering behavior.

A source outside the 2% tolerance continues to use strict landscape or
portrait classification. The 2% boundary is inclusive.

## Device Name Normalization

Normalize device names immediately after selecting the highest-precedence EXIF,
QuickTime, or XMP model value. Begin with the confirmed mapping:

```text
iPhone7,2 -> iPhone 6
```

Already-readable model names remain unchanged. Unknown Apple identifiers and
all other device values also remain unchanged; the tool will not guess.
Manual override values continue to take precedence and are not rewritten.

## Data Flow

`metadata_from_exiftool` selects the raw model and passes it through a small
device-name normalization helper. The resulting readable value flows through
the existing metadata model, report, caption formatting, fitting, and renderer.

`geometry_for` classifies the oriented source dimensions using the 2%
square-like rule before selecting portrait or landscape geometry. Rendering
otherwise remains unchanged.

## Error Handling

No new warnings or failures are introduced. Unknown models are preserved, and
valid positive integer dimensions continue to use the existing validation.

## Verification

Tests will cover:

- `956×961` selecting the square layout;
- the inclusive 2% boundary;
- dimensions immediately beyond the boundary remaining portrait or landscape;
- centered square caption coordinates;
- `iPhone7,2` becoming `iPhone 6`;
- readable and unknown device values remaining unchanged;
- focused metadata/layout/render tests and the complete Python 3.13 suite;
- regeneration of `IMG_0466-print.jpg` to a separate preview file for visual
  inspection without overwriting the user's current print output.
