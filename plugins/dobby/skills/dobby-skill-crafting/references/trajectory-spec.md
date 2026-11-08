# Trajectory spec

Source of truth: `TrajectorySpec` and `TrajectoryStep` in `dobby/types.py`, executed by `dobby/agent_loop/trajectory.py`.

## Spec

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `trajectory_name` | string | required | Name shown in results |
| `description` | string | "" | What the workflow does |
| `variables` | object | {} | Values used by `{{ name }}` templates in step parameters |
| `steps` | list | required | Ordered steps (below) |
| `stop_on_failure` | bool | true | Abort the rest after a failed step |
| `capture_final_screenshot` | bool | false | Return a final image; leave off unless needed |

## Step

| Field | Default | Meaning |
| --- | --- | --- |
| `step_id` | required | Unique id; later steps can read its output as `{{ steps.<step_id> }}` |
| `action` | required | One of the actions below |
| `parameters` | {} | Action parameters; Jinja2 templates are rendered first |
| `description` | "" | Intent, for the log |
| `verify_delta` | true | Check that the screen changed |
| `assertions` | [] | Post-step checks (below) |
| `retry_count` | 1 | 1 to 5 attempts |
| `timeout_sec` | 15.0 | Per-step limit |
| `continue_on_failure` | false | Keep going even if this step fails |

## Actions and their parameters

| Action | Parameters |
| --- | --- |
| `focus_window` | `{"title": "..."}` or `{"window_id": "..."}` |
| `find_and_click` | `{"query": "...", "button": "left", "verify_delta": true}` |
| `click` | `{"x": 100, "y": 200}` or `{"query": "..."}` |
| `click_and_type` | `{"query": "...", "text": "...", "press_enter": true}` |
| `type_text` | `{"text": "...", "press_enter": true}` |
| `hotkey` | `{"keys": ["cmd", "s"]}` |
| `press_key` | `{"key": "enter"}` |
| `scroll` | `{"dx": 0, "dy": -5}` |
| `wait` | `{"duration_sec": 1.5}` |
| `wait_for_ui_change` | `{"timeout": 5.0}` |
| `capture_pane` | `{"pane_name": "waveform_canvas"}` |
| `capture_vdom` | `{"query_selector": "push_button[name*='Run']"}` |
| `query_vdom` | `{"selector": "push_button"}` |
| `execute_crystallized_skill` | `{"skill_name": "...", "parameters": {...}}` |
| `run_terminal_command` | `{"command": "..."}` (needs the user's go-ahead) |
| `dismiss_obstructions` | `{}` |

## Assertions

Each assertion is an object with one key:

| Key | Passes when |
| --- | --- |
| `assert_delta` | The step's delta detection equals the given bool |
| `assert_text_present` | The given text appears in the step's message or output data (not the screen) |
| `assert_vdom_selector` | The vDOM selector matches at least one node |
| `assert_min_nodes` | The step output reports at least this many nodes |

To check the screen itself after a step, add a `capture_vdom` or `query_vdom` step with `assert_vdom_selector`.

## Example

```json
{
  "trajectory_name": "chrome_open_url",
  "variables": {"url": "https://example.com"},
  "steps": [
    {"step_id": "focus", "action": "focus_window", "parameters": {"title": "Chrome"}},
    {"step_id": "address", "action": "hotkey", "parameters": {"keys": ["cmd", "l"]}},
    {"step_id": "type", "action": "type_text", "parameters": {"text": "{{ url }}", "press_enter": true}},
    {"step_id": "settle", "action": "wait_for_ui_change", "parameters": {"timeout": 8.0}}
  ]
}
```
