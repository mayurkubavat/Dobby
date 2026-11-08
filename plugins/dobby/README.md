# Dobby skills

Agent Skills that teach any AI agent when to use the Dobby MCP server and how
to drive the desktop with it. Each skill is a short `SKILL.md` (when to use
it, the steps) plus a `references/` folder read only when needed. They follow
the open Agent Skills format, so the same folders work in Claude Code, Codex,
Cursor, Gemini CLI, Antigravity, GitHub Copilot and OpenCode.

| Skill | Use it for |
| --- | --- |
| `dobby` | Entry point for any desktop task: when to use Dobby, perception ladder, contracts, recovery, safety, handoffs |
| `dobby-perception` | Choosing between AX, OCR, local models and screenshots; the full tool reference |
| `dobby-skill-crafting` | Recording a task and crystallizing it into a replayable skill |
| `dobby-eval` | Cassettes, replay tests and the local benchmark |
| `dobby-troubleshoot` | Not connected, permissions, Ollama and models, toolsets, client config |
| `dobby-app-claude` | The Claude desktop app (Electron) |
| `dobby-app-chrome` | Chrome and web apps, including Gemini on the web |
| `dobby-app-vivado` | AMD/Xilinx Vivado GUI and simulation waveforms |
| `dobby-app-verdi` | Synopsys Verdi and other dense EDA UIs |

Every name carries the `dobby` prefix because most tools put all skills in one
shared folder, where a bare `eval` or `perception` would collide.

## Install

For every AI tool on the computer at once:

```
dobby setup            # MCP config + skills; --dry-run to preview, --list for state
```

That links each skill folder into:

| Folder | Read by |
| --- | --- |
| `~/.agents/skills/` | Codex, Cursor, Gemini CLI, GitHub Copilot, OpenCode, Antigravity |
| `~/.claude/skills/` | Claude Code (skipped when this plugin is installed) |
| `~/.gemini/antigravity-cli/skills/` | Antigravity CLI |

The links point back at this clone, so `git pull` updates every tool.

Claude Code can instead install the skills as a plugin (names then show as
`dobby:dobby`, `dobby:dobby-perception`...):

```
/plugin marketplace add /path/to/Dobby
/plugin install dobby@dobby
```

Agents working inside this repository see the skills without any install,
through the `.agents/skills` link at the repo root.

Clients with no skills support still get the guidance: Dobby sends a short
version as MCP server instructions, serves every skill as the resource
`mcp://dobby/skills/<name>` (index at `mcp://dobby/skills`), and offers a
`dobby` MCP prompt that starts a task with the full guide.

## Writing skills that trigger

- The `description` decides when an agent loads the skill. Say what the user
  would ask for ("click", "my screen", the app's name), not how Dobby works.
- Keep tool-specific wording out: name skills plainly (`the dobby-perception
  skill`), and call tools by their bare MCP names (`find_and_click(...)`).
- Keep `SKILL.md` short; move detail into `references/`.

## Editing references

Files whose first line says `GENERATED` come from code or from `learnings/` and
`docs/`. Edit the source and run `python scripts/build_skill_refs.py`;
`tests/test_skill_plugin.py` fails while any of them is stale. Everything else is
written by hand and edited in place.
