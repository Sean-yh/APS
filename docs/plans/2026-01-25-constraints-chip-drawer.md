# Constraints Chip + Drawer Implementation Plan
 
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
 
**Goal:** Replace the in-flow "Constraints (Live)" card with a compact chip in the workspace sticky toolbar that opens a right-side drawer containing the existing `ConstraintsPanel`.
 
**Architecture:** Add a small client component (`ConstraintsChip`) that owns the open/close state and renders a `role="dialog"` drawer overlay. Mount the chip in `WorkspaceArea`'s sticky controls row and remove the old `ConstraintsCard` from the main vertical flow.
 
**Tech Stack:** Next.js (app router), React, TypeScript, Tailwind CSS, Vitest + Testing Library (new) for UI behavior tests.
 
### Task 1: Add Frontend Test Harness (Vitest)
 
**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/test/setup.ts`
 
**Step 1: Write the failing test**
 
Create a test file that imports a not-yet-existing `ConstraintsChip` and asserts it opens/closes a drawer on click/Escape.
 
**Step 2: Run test to verify it fails**
 
Run: `npm test`
Expected: FAIL (module not found / test script missing)
 
**Step 3: Add minimal test tooling**
 
Add Vitest + React Testing Library dependencies and configuration until tests execute.
 
**Step 4: Run tests to verify they're runnable**
 
Run: `npm test`
Expected: FAIL (because `ConstraintsChip` isn't implemented yet)
 
### Task 2: Implement Constraints Chip + Drawer Component
 
**Files:**
- Create: `frontend/src/components/workspace/ConstraintsChip.tsx`
- Test: `frontend/src/components/workspace/ConstraintsChip.test.tsx`
 
**Step 1: Write the failing test**
 
Test behaviors:
- Default: drawer closed
- Click chip: drawer opens (`role="dialog"`)
- Press Escape OR click backdrop: drawer closes
 
**Step 2: Run test to verify it fails**
 
Run: `npm test`
Expected: FAIL (component missing / behavior missing)
 
**Step 3: Write minimal implementation**
 
Implement the chip button + fixed-position drawer overlay containing `ConstraintsPanel`.
 
**Step 4: Run tests to verify it passes**
 
Run: `npm test`
Expected: PASS
 
### Task 3: Wire Chip Into Workspace Toolbar and Remove In-Flow Card
 
**Files:**
- Modify: `frontend/src/components/workspace/WorkspaceArea.tsx`
- (Optional) Delete: `frontend/src/components/workspace/ConstraintsCard.tsx` (only if unused)
 
**Step 1: Manual smoke check**
 
Run: `npm run dev`
Expected: chip appears next to the `1D/3D/5D/7D` controls; clicking opens drawer with constraints status; no extra constraints card in the scroll body.
 
