# Local Gemma in Pinlet

Measured on a MacBook Pro (16 GB), 2026-11-01, Claude window 1512x949.

| Step | Time | Notes |
| --- | --- | --- |
| Capture window | ~265 ms | Quartz, Retina downscaled to logical points |
| Apple Vision OCR | ~220 ms, ~0.6 ms cached | dHash cache keyed on the frame |
| Gemma yes/no, cold | ~8.4 s | First call loads gemma3:4b; warmup at server start hides it |
| Gemma yes/no, warm | ~1.2 s | ~30 output tokens |
| Screenshot sent to a cloud model | ~1,533 image tokens | For comparison |

## Where Gemma is used

| Tool or path | What Gemma does | Fallback |
| --- | --- | --- |
| `ask_screen` | Answers yes, no or unsure with evidence, after OCR could not settle it; sees the crop's OCR text | `answer: null` (unsure or unavailable); caller escalates |
| `find_visual_element` | Picks one numbered box (Set-of-Marks) matching a description | OCR match first; none found returns no element |
| `capture_vdom(label_with_gemma=true)` | Names unnamed detector boxes; cached per app by patch dHash | Boxes stay unnamed |
| Modal triage (obstruction interceptor) | Tie-breaker on dialog risk when rules disagree; fail-closed | Escalates, never auto-dismisses |
| Bicameral Tier 1 | Judges a step's expected outcome after Tier 0 (AX diff, OCR) is unsure | Tier 2 (cloud screenshot) |

## Prompt pack

Every Gemma prompt lives in `pinlet/gemma/prompts.py` with an id and version (`yes_no@4`, `modal_triage@1`, `mark_label@1`, `mark_find@1`, `ground_roi@1`, `compress_history@1`). `get_vision_diagnostics` lists the active tags.

`yes_no@4` rules, and how to read its answer (v3 made a visible but different state a no, after v2 answered unsure on a red indicator; v4 makes blank crops unsure after v3 invented a dialog on a blank screen, and a near-uniform crop now returns `source: blank_crop` without calling Gemma):
- `answer` is `true`, `false`, or `null`. `null` with `source: gemma` means Gemma said unsure: take a screenshot, do not retry the same question.
- `evidence_type: text` means the evidence is visible words. They are checked against local OCR of the same crop; `evidence_verified: false` means OCR could not find them, and confidence is capped at 0.5 (below every pass bar). Treat that as unsure.
- `evidence_type: visual` (a color, icon, layout) is not checked; give it less weight than verified text.
- `prompt` names the version that answered, so a regression can be traced to a prompt change.

To try a different wording without editing code, put `<prompt_id>.txt` (system text only) in `~/.pinlet/prompts/`; results then report `yes_no@4+local`. Measure it first with `scripts/eval_prompts.py` (see the `eval` skill).

## Settings (`pinlet/config.py`, or `configure_vision(setting, value)` at runtime)

- `gemma3_endpoint` (`http://127.0.0.1:11434`), `gemma3_vision_model` and `gemma3_text_model` (`gemma3:4b`).
- `gemma3_keep_alive` (`10m`) keeps the model loaded between calls; `gemma3_warmup_on_start` loads it at server start.
- `gemma3_visual_max_side` (896) caps the image side sent to Gemma; `gemma3_max_marks` (30) caps boxes per call.
- `gemma3_modal_min_confidence` (0.8) and `gemma3_visual_min_confidence` (0.6) gate acting on an answer.
- `gemma3_timeout_seconds` (25) per call.
- `gemma3_ocr_verify` (true) checks text evidence against OCR of the crop; `gemma3_unverified_text_cap` (0.5); `gemma3_prompt_dir` (`~/.pinlet/prompts`).
- `gemma3_ocr_context` (false) would also show Gemma the OCR text. It is off because on gemma3:4b it made one confident wrong answer in `eval_prompts.py` (said a Save dialog was closed because the button words were listed) and flattened confidences, for +0.4 s.

## Models

Pinlet never pulls models; ask the user before downloading one, since one Ollama may serve several projects. gemma3:12b is installed too, for slower, off-the-hot-path work such as history compaction.

## Measuring

`python scripts/bench_local.py --repeats 3` prints these numbers for the active window (read-only, never clicks). `python scripts/smoke_local.py` checks each layer once. `python scripts/eval_prompts.py --synthetic` scores the yes/no prompt on labelled crops (correct, wrong, unsure, latency).
