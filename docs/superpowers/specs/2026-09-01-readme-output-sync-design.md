# README Output Documentation Sync Design

## Goal

Bring the bilingual README into agreement with the current print behavior
before pushing the completed fixes to `main`.

## Chinese and English Changes

Update both language sections with equivalent information:

- landscape prints now use a centered `1720×1080` photo frame with 40-pixel
  left and right margins;
- portrait and square-like photos remain uncropped;
- a photo is square-like when its width and height differ by no more than 2%
  of the longer edge;
- a two-line square caption is centered using its actual visible glyph bounds;
- the confirmed Apple hardware identifier `iPhone7,2` is displayed as
  `iPhone 6`, while unknown identifiers remain unchanged.

Add a concise troubleshooting entry explaining that Apple EXIF data can contain
internal identifiers and that confirmed identifiers are normalized safely.

## Scope

Do not add a changelog, implementation internals, new commands, or unrelated
README restructuring. Preserve the existing Chinese-first bilingual structure,
author credit to Chris, and all installation and workflow instructions.

## Verification and Delivery

- check that old landscape values `1640×960` and 80-pixel side margins no
  longer appear;
- check that the new facts appear in both language sections;
- run Markdown whitespace checks and the already-established project
  verification commands;
- commit the README change and push all local `main` commits to `origin/main`.
