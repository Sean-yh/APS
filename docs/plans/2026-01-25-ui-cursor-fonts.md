# UI Cursor + Font Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make all buttons show the correct mouse cursor (pointer/disabled) and improve typography so text looks consistent and less "weird".

**Architecture:** Prefer global CSS defaults for standard interactive elements (buttons/inputs/anchors) and then fix any remaining non-semantic click targets (e.g., `div` with `onClick`) locally.

**Tech Stack:** Next.js (app router), React, Tailwind v4, global CSS in `frontend/src/app/globals.css`

---

### Task 1: Establish Verification Baseline (Frontend)

**Files:**
- None (read-only)

**Step 1: Install deps (if needed)**

Run:
```bash
npm -C frontend ci
```

Expected: command succeeds; no file changes (only `node_modules/`).

**Step 2: Run lint**

Run:
```bash
npm -C frontend run lint
```

Expected: currently fails with existing issues. Capture the failing files so we can ensure we don’t regress.

**Step 3: Run build**

Run:
```bash
npm -C frontend run build
```

Expected: may pass or fail; use as a baseline before/after.

---

### Task 2: Fix Existing ESLint Errors Blocking Verification

**Files:**
- Modify: `frontend/src/components/layout/ChatList.tsx`
- Modify: `frontend/src/components/workspace/ScheduleCard.tsx`

**Step 1: Make ChatList grouping pure**

Change `Date.now()` usage so it does not run during render (rule: purity/idempotence). Minimal approach:
- Store `now` once using `useMemo(() => Date.now(), [])` or `useState(() => Date.now())`
- Use that memoized value inside the existing `useMemo` block

**Step 2: Remove setState-in-effect lint error**

Refactor `ScheduleCard` so it does not call `setIsCollapsed(false)` synchronously inside an effect.

Minimal approach (pick one):
- Derive `isCollapsed` from `isFocused` (if UX allows), OR
- Move the state update into the event that changes focus (preferred if there is a focus handler), OR
- If the effect must exist, gate it via `requestAnimationFrame` so the state update is not synchronous in the effect body.

**Step 3: Re-run lint**

Run:
```bash
npm -C frontend run lint
```

Expected: no `react-hooks/purity` or `react-hooks/set-state-in-effect` errors remain (warnings may remain if the project tolerates them).

---

### Task 3: Add Global Cursor Defaults For Buttons/Inputs

**Files:**
- Modify: `frontend/src/app/globals.css`

**Step 1: Add global cursor rules**

Add CSS that ensures:
- Enabled buttons show `cursor: pointer`
- Disabled buttons show `cursor: not-allowed`
- `input[type="button"|"submit"|"reset"]` follow the same behavior
- Anchors show `cursor: pointer` (unless overridden)

Example shape:
```css
button,
input[type="button"],
input[type="submit"],
input[type="reset"],
a[href] {
  cursor: pointer;
}

button:disabled,
input[type="button"]:disabled,
input[type="submit"]:disabled,
input[type="reset"]:disabled,
[aria-disabled="true"] {
  cursor: not-allowed;
}
```

**Step 2: Re-run build + lint**

Run:
```bash
npm -C frontend run lint
npm -C frontend run build
```

Expected: both succeed.

---

### Task 4: Fix Non-Semantic Click Targets Still Missing Pointer Cursor

**Files:**
- Modify: any `frontend/src/**/*.tsx` that uses `onClick` on non-button elements

**Step 1: Audit**

Run:
```bash
rg -n \"onClick=\\{\" frontend/src
```

**Step 2: Fix**

For each non-button click target:
- Prefer converting it to a semantic `<button type=\"button\">` (best)
- If not feasible, add `role=\"button\"`, `tabIndex={0}`, keyboard handlers, and a `cursor-pointer` class

**Step 3: Verify**

Run:
```bash
npm -C frontend run lint
npm -C frontend run build
```

Expected: no new warnings/errors introduced.

---

### Task 5: Improve Font Stack + Typography Consistency

**Files:**
- Modify: `frontend/src/app/globals.css`

**Step 1: Update `body` font-family**

Use a sane cross-platform stack that looks good for English + Chinese:
- `system-ui` first
- macOS: `-apple-system`, `PingFang SC`
- Windows: `Segoe UI`, `Microsoft YaHei`
- Common fallbacks: `Helvetica Neue`, `Arial`

Also consider adding:
- `text-rendering: optimizeLegibility;`
- Keep existing font smoothing settings

**Step 2: Ensure markdown content inherits**

Make sure `.prose` does not introduce a different font-family (explicitly set it to `inherit` if needed).

**Step 3: Verify**

Run:
```bash
npm -C frontend run lint
npm -C frontend run build
```

Expected: both succeed.

---

### Task 6: Commit

**Step 1: Commit changes**

Run:
```bash
git status
git add docs/plans/2026-01-25-ui-cursor-fonts.md frontend/src/app/globals.css frontend/src/components/layout/ChatList.tsx frontend/src/components/workspace/ScheduleCard.tsx
git commit -m \"chore(ui): fix cursor defaults and improve typography\"
```

Expected: clean working tree after commit (aside from `node_modules/`).

