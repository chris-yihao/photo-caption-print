# Caption Line Spacing Design

## Goal

Increase the visual separation between the two caption lines in the reduced
bottom information area without changing any other print layout behavior.

## Layout Change

- Move the primary caption baseline upward by exactly 5 pixels.
- Move the secondary caption baseline downward by exactly 5 pixels.
- The total baseline separation therefore increases by 10 pixels.
- Apply the same offset to landscape, portrait, and square prints.

## Unchanged Behavior

Canvas dimensions, 300 PPI output, photo margins, bottom information-area
height, font sizes, center cropping, caption fitting, metadata extraction,
weekday formatting, location lookup, and device display remain unchanged.

## Warning Investigation

The two reported warnings are outside this layout change. Their cause will be
diagnosed from the warning column in `处理报告.csv` or from the terminal's
concrete warning text. No warning behavior will be changed without that
evidence.

## Verification

Tests will assert the exact five-pixel baseline offsets for landscape,
portrait, and square geometry. Focused layout/render tests and the full test
suite will then be run.
