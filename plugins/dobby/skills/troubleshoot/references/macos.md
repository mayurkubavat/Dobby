# macOS setup and client registration

## Permissions

Dobby needs two macOS permissions, granted to whichever app launches the server process:

| Permission | Needed for | Where |
| --- | --- | --- |
| Screen Recording | Captures, OCR, Gemma checks | System Settings > Privacy & Security > Screen Recording |
| Accessibility | AX tree, `find_element`, clicks and keys | System Settings > Privacy & Security > Accessibility |

The launching app is Claude (Claude Desktop), Antigravity, or your terminal for Claude Code. macOS applies a grant only to processes started after it, so quit the app fully (cmd+Q) and reopen it.

## Registration

The server command is the venv's entry point:

`/path/to/Dobby/.venv/bin/dobby` (the `.venv` of your clone)

| Client | How |
| --- | --- |
| Claude Code | `claude mcp add -s user Dobby -- <command>`; check with `claude mcp get Dobby` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json`, under `mcpServers` |
| Antigravity | `~/.gemini/config/mcp_config.json`, under `mcpServers` |

Entry for the two JSON files:

```json
"Dobby": {
  "command": "/path/to/Dobby/.venv/bin/dobby"
}
```

Back up the file first, keep it valid JSON (`python3 -m json.tool FILE`), and edit Claude Desktop's file only while the app is quit.

## After updating the code

1. `uv pip install --python .venv/bin/python -e ".[dev,ocr,macos]"` if dependencies changed.
2. `.venv/bin/python -m pytest -q`.
3. Restart each client so it starts the new server.

## Local models

One Ollama (Homebrew service, `localhost:11434`) serves every project on the Mac; models live in `~/LocalAI/models/ollama`. `~/LocalAI/MODELS.md` lists approved models; `~/LocalAI/bin/ai-check` must pass before any pull, and duplicate downloads are not allowed.
