# Caption Group Vertical Centering Design

## Goal

Vertically center the two-line caption group inside the bottom white area while
preserving the approved distance between its two lines.

## Two-Line Layout

- Treat `primary_y` and `secondary_y` as one coordinate pair.
- Preserve the existing baseline distance: 42 pixels for landscape and square
  prints, and 58 pixels for portrait prints.
- Align the arithmetic midpoint of the two baselines with the vertical center
  of the bottom caption area.
- The resulting baselines are `(1119, 1161)` for landscape and square prints,
  and `(1681, 1739)` for portrait prints.
- Relative to the current layout, this moves the complete group upward by 4
  pixels for landscape and square prints and by 6 pixels for portrait prints.

## Single-Line Layout

When only one caption line is present, retain the existing behavior: that one
line remains vertically centered by itself in the bottom white area.

## Unchanged Behavior

Canvas dimensions, margins, caption-area height, font sizes, caption fitting,
photo cropping, metadata extraction, weekday, location, device display, and
300 PPI output remain unchanged.

## Verification

Exact geometry tests will cover landscape, portrait, and square baseline
coordinates and verify the preserved line separation. Existing command tests
will continue to prove that single-line captions use their original center.
Focused render tests and the complete Python 3.13 suite will be run.
