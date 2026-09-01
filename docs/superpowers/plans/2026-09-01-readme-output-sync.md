# README Output Documentation Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the bilingual README to match the current layout and device-name behavior, then push all completed `main` commits.

**Architecture:** Keep the existing Chinese-first bilingual structure and edit only the metadata, output-standard, troubleshooting, and development-test descriptions that are affected. Verify that both languages contain equivalent facts and that obsolete geometry values are gone.

**Tech Stack:** Markdown, Git, Python 3.13 project verification

---

## File Structure

- Modify `README.md`: synchronize current behavior in Chinese and English.

### Task 1: Synchronize the Bilingual README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the Chinese metadata and output sections**

Document these exact facts:

```text
iPhone7,2 显示为 iPhone 6；未知内部型号保持原样。
横图照片框为 1720×1080，左右白边各 40 px，底部信息白边 120 px。
宽高差不超过较长边 2% 的照片按正方形处理。
竖图和正方形完整保留、不裁切。
正方形双排文字按照实际可见字形在底部白边中上下居中。
```

Add one Chinese troubleshooting bullet explaining Apple internal model names.

- [ ] **Step 2: Apply equivalent English updates**

Document the same behavior with these terms:

```text
iPhone7,2 is displayed as iPhone 6; unknown identifiers are preserved.
Landscape photo frame: 1720×1080, 40-pixel side margins, 120-pixel caption border.
Within 2% of the longer edge is square-like.
Portrait and square-like photos remain uncropped.
Two-line square captions are vertically centered by visible glyph bounds.
```

Add the matching English troubleshooting bullet.

- [ ] **Step 3: Update the test-coverage descriptions**

In both Development sections, mention near-square classification, device-name
normalization, and visible square-caption centering without changing the listed
module architecture.

- [ ] **Step 4: Verify bilingual content and remove obsolete values**

Run:

```bash
test "$(rg -o '1720×1080' README.md | wc -l | tr -d ' ')" = "2"
test "$(rg -o 'iPhone7,2' README.md | wc -l | tr -d ' ')" -ge "2"
test "$(rg -o 'iPhone 6' README.md | wc -l | tr -d ' ')" -ge "2"
test "$(rg -o '2%' README.md | wc -l | tr -d ' ')" -ge "2"
! rg -n '1640×960|80-pixel side margins|左右各留 80' README.md
git diff --check
```

Expected: every command exits zero, the updated facts appear in both language
sections, and obsolete geometry values are absent.

- [ ] **Step 5: Commit the README**

```bash
git add README.md
git commit -m "docs: sync README with current print layout"
```

### Task 2: Verify and Push `main`

**Files:**
- Read: repository working tree and `origin/main`

- [ ] **Step 1: Run complete local verification**

```bash
PYTHONPATH=/Users/chris/Library/Python/3.9/lib/python/site-packages /opt/homebrew/opt/python@3.13/bin/python3.13 -m pytest -q
bash -n "scripts/Install.command" "scripts/Photo Caption Print.command"
zsh -n "scripts/Install.command" "scripts/Photo Caption Print.command"
git diff --check
```

Expected: the Python 3.13 suite passes, both launchers have valid syntax, and
Git reports no whitespace errors.

- [ ] **Step 2: Push and verify synchronization**

```bash
git push origin main
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
```

Expected: the push succeeds, `main` is no longer ahead, the working tree is
clean, and both revisions are identical.
