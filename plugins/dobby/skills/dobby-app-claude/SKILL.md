---
name: dobby-app-claude
description: Drive the Claude desktop app (Electron) on macOS with Dobby - sidebar, projects, chats and the composer - using its accessibility tree instead of screenshots. Use when a task happens inside the Claude app window. Read the dobby skill first for the general rules.
---

# Claude desktop app

Claude is an Electron app. Its web content appears in the AX tree once Dobby has inspected it (the server sets `AXManualAccessibility` on first read). Verified 2026-11-01: 57 interactable elements versus 3 before.

## Steps

1. `focus_window` on the window titled "Claude".
2. `get_accessibility_tree(app_name_filter="Claude")`. If only the three window buttons come back, call it once more.
3. Click by name: `find_and_click(query="Search")`, `find_and_click(query="New")`, or a project name from the sidebar.
4. Type into the composer with `click_and_type`, using the composer's accessible name or placeholder from the tree.
5. Verify with `read_screen_text(contains=...)` or `ask_screen(..., expected_text=...)`, not a screenshot. Responses stream; use `wait_for_ui_change` until they settle.

## Cautions

- Sending a message, sharing, or deleting a chat or project is an outward or irreversible action: confirm with the user first.
- Do not drive the Claude window that is running this very session. Use another window, or ask the user.
- On a busy chat, OCR of the whole window is larger than a screenshot. Always pass `contains` or a region.

## References

- `references/ax-names.md`: accessible names and roles seen in the app.
