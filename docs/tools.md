# MCP Tool Reference

`pinlet` exposes 37 tools across 6 functional categories.

---

## 1. Screen Perception & Window Management

### `take_screenshot`
Captures a screenshot of the display with optional Set-of-Marks labels and window clipping.
- **Parameters**:
  - `mode` (`str`, optional): `'annotated_som'`, `'raw'`, or `'cursor_only'`. Default `'annotated_som'`.
  - `app_filter` (`str`, optional): Title or application name filter.
  - `window_id` (`str`, optional): Specific window ID to crop to.
  - `clip_to_active_window` (`bool`, optional): If true, crops screenshot to the active window.

### `capture_window`
Captures strictly the target or active window, reducing image token size.
- **Parameters**:
  - `window_id` (`str`, optional): Target window ID.
  - `title_filter` (`str`, optional): Title substring filter.
  - `mode` (`str`, optional): Grounding mode (`'raw'`, `'cursor_only'`, `'annotated_som'`).

### `capture_pane`
Captures a named functional sub-pane within the active application (e.g., `'waveform_canvas'`, `'tcl_console'`).
- **Parameters**:
  - `pane_name` (`str`): Name of the sub-pane.
  - `window_id` (`str`, optional): Target window ID.
  - `mode` (`str`, optional): Grounding mode.

### `list_windows`
Lists all open desktop windows with their IDs, titles, bounding boxes, and active focus state.
- **Parameters**: None.

### `get_active_window`
Returns metadata and bounding box for the currently focused window.
- **Parameters**: None.

### `focus_window`
Brings a window to the foreground and gives it keyboard focus.
- **Parameters**:
  - `window_id` (`str`): The window ID (hex or decimal) to focus.

### `wait_for_ui_change`
Blocks until a visual difference is detected on screen (useful after clicking links or opening tabs).
- **Parameters**:
  - `timeout_seconds` (`float`, optional): Maximum wait time. Default `5.0`.
  - `min_delta_ratio` (`float`, optional): Minimum changed pixel fraction. Default `0.005`.

---

## 2. Mouse and Keyboard Control

### `click`
Clicks the mouse at specified coordinates, a mark ID, or by label query.
- **Parameters**:
  - `x` (`int`, optional), `y` (`int`, optional): Absolute desktop coordinates.
  - `mark_id` (`str`, optional): Set-of-Marks badge ID.
  - `query` (`str`, optional): Element label to locate and click.
  - `button` (`str`, optional): `'left'`, `'right'`, or `'middle'`. Default `'left'`.
  - `click_count` (`int`, optional): Number of clicks (1 for single, 2 for double). Default `1`.
  - `verify_delta` (`bool`, optional): Confirm UI changed after click. Default `true`.

### `click_and_type`
Atomic compound action that focuses an element, clears existing content, types text, and presses Enter.
- **Parameters**:
  - `text` (`str`): Text to type.
  - `query` (`str`, optional), `x` (`int`, optional), `y` (`int`, optional): Target location.
  - `clear_first` (`bool`, optional): Clear existing text before typing. Default `false`.
  - `press_enter` (`bool`, optional): Press Enter after typing. Default `true`.
  - `delay_ms` (`float`, optional): Delay between keystrokes in ms. Default `12.0`.

### `type_text`
Types text into whichever element currently has focus.
- **Parameters**:
  - `text` (`str`): String to type.
  - `press_enter` (`bool`, optional): Whether to press Enter after typing. Default `false`.
  - `delay_ms` (`float`, optional): Keystroke delay in ms. Default `12.0`.

### `press_key`
Presses and releases a single key.
- **Parameters**:
  - `key` (`str`): Key name (e.g., `'enter'`, `'escape'`, `'tab'`, `'backspace'`, `'f5'`).

### `hotkey`
Presses a combination of keys simultaneously.
- **Parameters**:
  - `keys` (`list[str]` or `str`): Keys to press (e.g., `["ctrl", "c"]` or `'ctrl+alt+t'`).

### `move_cursor`
Moves the mouse pointer to coordinates without clicking.
- **Parameters**:
  - `x` (`int`), `y` (`int`): Target desktop coordinates.

### `drag_and_drop`
Drags the mouse from a start position to an end position.
- **Parameters**:
  - `start_x` (`int`), `start_y` (`int`): Drag origin.
  - `end_x` (`int`), `end_y` (`int`): Drag destination.
  - `duration_sec` (`float`, optional): Drag animation duration. Default `0.5`.

### `scroll`
Scrolls the mouse wheel.
- **Parameters**:
  - `dx` (`int`, optional): Horizontal scroll delta. Default `0`.
  - `dy` (`int`, optional): Vertical scroll delta (positive for up, negative for down). Default `-3`.

### `scroll_until_visible`
Repeatedly scrolls in a direction until a target element or text becomes visible.
- **Parameters**:
  - `query` (`str`): Text or element to find.
  - `direction` (`str`, optional): `'up'` or `'down'`. Default `'down'`.
  - `max_scrolls` (`int`, optional): Maximum scroll attempts. Default `5`.

---

## 3. UI Element Grounding & Virtual DOM

### `find_element`
Finds an element's physical screen coordinates without sending screenshot images to the model.
- **Parameters**:
  - `query` (`str`): Label, title, or substring.
  - `role` (`str`, optional): UI role (`'push_button'`, `'text_input'`, etc.).

### `find_and_click`
Locates an element by label and clicks it in a single verified step.
- **Parameters**:
  - `query` (`str`): Label or text to find.
  - `button` (`str`, optional): Mouse button. Default `'left'`.
  - `click_count` (`int`, optional): Click count. Default `1`.

### `get_accessibility_tree`
Queries the operating system accessibility tree (Linux AT-SPI2 / D-Bus).
- **Parameters**:
  - `app_name_filter` (`str`, optional): Filter by application name.
  - `max_depth` (`int`, optional): Tree traversal depth limit. Default `5`.

### `capture_vdom`
Fuses accessibility nodes and neural bounding box proposals into a unified virtual DOM.
- **Parameters**:
  - `window_id` (`str`, optional): Target window ID.

### `query_vdom`
Queries the captured virtual DOM using CSS-style selectors.
- **Parameters**:
  - `selector` (`str`): Selector string (e.g. `'push_button'`, `'tab_item[name*="Tcl"]'`).

---

## 4. Local Trajectories & Macros

### `execute_trajectory`
Executes a multi-step declarative trajectory locally at native speed.
- **Parameters**:
  - `trajectory_spec` (`dict`): Specification containing:
    - `trajectory_name` (`str`): Name of trajectory.
    - `variables` (`dict`, optional): Jinja2 variables for template interpolation.
    - `steps` (`list[dict]`): Ordered list of action steps (`focus_window`, `click`, `capture_pane`, etc.).
    - `stop_on_failure` (`bool`, optional): Abort if an unhandled step fails. Default `true`.

---

## 5. Skill Recording & Crystallization

### `start_skill_recording`
Starts recording physical actions alongside application command journal streams (e.g., Vivado `.jou`).
- **Parameters**:
  - `journal_path` (`str`, optional): Path to application journal file.

### `stop_skill_recording`
Stops recording and returns correlated GUI-to-command steps.
- **Parameters**: None.

### `crystallize_skill`
Compiles recorded steps into a reusable, parameterized script template.
- **Parameters**:
  - `skill_name` (`str`): Name for the skill.
  - `description` (`str`): Description of what the skill does.

### `execute_crystallized_skill`
Executes a saved parameterized script through the application CLI or console.
- **Parameters**:
  - `skill_name` (`str`): Name of skill to run.
  - `parameters` (`dict`, optional): Parameter values to interpolate into the template.

### `list_crystallized_skills`
Lists all saved skills in the registry.
- **Parameters**: None.

### `delete_crystallized_skill`
Deletes a skill from the registry.
- **Parameters**:
  - `skill_name` (`str`): Name of skill to remove.

---

## 6. Plugins & Diagnostics

### `list_plugins`
Lists registered application plugins and reports which plugin matches the currently active window.
- **Parameters**: None.

### `list_plugin_features`
Returns the capabilities, sub-panes, and commands supported by a plugin.
- **Parameters**:
  - `plugin_id` (`str`): Plugin identifier (e.g., `'vivado'`, `'chrome'`).

### `invoke_plugin_feature`
Executes a high-level application capability directly (e.g., `'vivado.zoom_fit'`, `'vivado.run_for'`).
- **Parameters**:
  - `feature_id` (`str`): Feature identifier.
  - `args` (`dict`, optional): Feature arguments.

### `dismiss_obstructions`
Dismisses blocking popups, permission dialogs, cookie banners, or screensavers.
- **Parameters**:
  - `max_attempts` (`int`, optional): Maximum dismissal attempts. Default `3`.

### `resolve_modal_dialog`
Inspects an active modal dialog, classifies risk, and clicks appropriate response buttons.
- **Parameters**:
  - `auto_confirm` (`bool`, optional): Automatically confirm safe/informative dialogs.

### `manage_cache`
Manages the self-healing locator cache.
- **Parameters**:
  - `action` (`str`): `'stats'`, `'verify'`, `'heal'`, `'invalidate'`, or `'clear'`.

### `configure_vision`
Updates runtime settings dynamically.
- **Parameters**:
  - `setting` (`str`): Setting key (e.g., `'grounding_strategy'`).
  - `value` (`str`): New value.

### `get_vision_diagnostics`
Returns system diagnostics, active display server, screen bounds, and active grounding engine.
- **Parameters**: None.

### `run_terminal_command`
Runs a bash command on the host and returns its output and exit code.
- **Parameters**:
  - `command` (`str`): Bash command string.
  - `timeout_seconds` (`int`, optional): Execution timeout. Default `30`.
