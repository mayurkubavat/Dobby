# Cassettes

Source of truth: `dobby/hal/mock_replay.py` (`MockReplayHAL`, `load_cassette`, `CassetteRecorder`).

## Loading

- From Python: `load_cassette(path)` accepts an `index.json` file, a directory holding `index.json`, a zip (`.halpack`) holding `index.json`, or a JSON string.
- As the server's backend: `get_desktop_hal(kind="replay", cassette=path)`, or start the server with `DESKTOP_HAL=replay` and `DESKTOP_CASSETTE=<path>`.
- In `crystallize_skill(..., cassette_path=path)`: the new macro is replayed on it before registration.

## index.json

```json
{
  "frames": [
    {
      "frame_id": "pre",
      "ocr_boxes": [{"text": "Run"}],
      "ax_tree": {},
      "ax_diff": {},
      "screen_size": [1512, 982],
      "cursor_pos": [0, 0],
      "windows": [],
      "active_window": null,
      "pixel_delta_from_prev": 0.0,
      "image_path": "frames/pre.png",
      "phase": "QUIESCENT"
    },
    {"frame_id": "post", "ocr_boxes": [{"text": "Done"}]}
  ],
  "transitions": [
    {
      "pre_frame_id": "pre",
      "action": "mouse_click",
      "args": {"x": 3, "y": 4, "button": "left", "click_count": 1},
      "post_frame_id": "post"
    }
  ],
  "topology": null
}
```

Only `frame_id` is required per frame; the rest default as shown. `image_path` is relative to the cassette's folder. `phase` is reported by the replay quiescence monitor (`QUIESCENT`, `SETTLING`, ...).

## Behavior

- The replay starts at the first frame. Each HAL action must match the next transition from the current frame (action name and args), or `CassetteMismatch` is raised.
- `read_screen_text(contains=...)` on the replay HAL searches the current frame's `ocr_boxes`.
- `wait_for_quiescence` returns the frame's recorded phase and `pixel_delta_from_prev` without sleeping.

## Recording from a live session

1. `manage_toolsets(action="enable", domains=["skills"])`.
2. `start_cassette_recording(name, capture_images=false, capture_ocr=true, settle_ms=400)`. The first frame is taken now.
3. Do the task with the normal tools (`click`, `type_text`, `press_key`, `hotkey`, `find_and_click`, ...). After each input call the session waits `settle_ms` and records a frame; the transition stores the exact HAL call (`mouse_click {x,y,button,click_count}`, `keyboard_type {text,delay_ms}`, `keyboard_press_key {key_name}`, `keyboard_hotkey {keys}`, `mouse_drag`, `mouse_scroll`, `mouse_move`, `focus_window`).
4. `stop_cassette_recording()` writes `<cassette_dir>/<name>/index.json` (default `~/.dobby/cassettes`) and `frames/fNNN.jpg` when images were captured. It reports frame, transition and snapshot-error counts.

Names are lower-cased to `[a-z0-9_-]`; an existing name is refused rather than overwritten. Typed text is stored verbatim: do not record password entry. OCR per frame costs a few hundred milliseconds; pass `capture_ocr=false` for long sessions and rely on AX.

In Python, the same recorder is `LiveCassetteRecorder(name, out_dir, snapshot_fn)` in `dobby/hal/cassette_recorder.py`, attached as `session.recorder`; `FeedbackLoopController.cassette_snapshot` is the live snapshot function.

## expect.json and the replay runner

An optional `expect.json` beside `index.json` states what the final frame must show:

```json
{
  "final_frame": "f002",
  "text_present": ["Synthesis Completed"],
  "text_absent": ["Error"],
  "ax_names_present": ["OK"]
}
```

- `pytest -m replay`: replays every cassette in `tests/cassettes/` (`tests/test_cassette_replay.py`).
- `python scripts/replay_cassette.py <folder>` or `--all <parent>`: same check from the command line, exit code 1 on failure.
- `dobby.hal.cassette_replay.replay_cassette(path)` returns `{ok, steps, total_steps, final_frame, failures}`.

The runner replays each recorded transition through the HAL input API, so it proves the cassette is self-consistent and its expectations hold. The useful failures come from code that drives the replay HAL itself (a macro via `crystallize_skill(cassette_path=...)`, a trajectory, a controller change): any call that differs from the recording raises `CassetteMismatch`.

`tests/cassettes/example_click/` is a small worked example.

## Tips for a useful cassette

- One cassette per task, named for it: `tests/cassettes/<app>_<task>/index.json`.
- Keep live recordings short: one task, few actions. Trim frames you do not need before committing.
- Keep OCR text exact; it is what Tier 0 checks match against.
