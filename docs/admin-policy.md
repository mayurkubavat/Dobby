# Admin policy

An organisation can set rules for every Dobby on a computer with one JSON
file. A person's own settings (`DOBBY_*` variables, `~/.dobby/apps.json`,
`dobby --turbo`, the overlay's Allow button) can make Dobby stricter but never
looser than this file.

## Where it goes

| System | Path |
| --- | --- |
| macOS | `/Library/Application Support/Dobby/policy.json` |
| Linux | `/etc/dobby/policy.json` |
| Windows | `%ProgramData%\Dobby\policy.json` |

Make it owned by root (or Administrators) and not writable by users. Deploy it
with your MDM or configuration tool (Jamf, Kandji, Intune, Ansible, Puppet).
Dobby re-reads it when it changes, so no restart is needed.

To try a policy without admin rights, point `DOBBY_POLICY_FILE` at a file.
That variable is ignored as soon as the system file exists.

## Example

```json
{
  "mode": "secure",
  "tools": {
    "deny": ["execute_trajectory"]
  },
  "shell": {
    "enabled": true,
    "allow": ["git status", "git log *", "open -a *", "ls *"]
  },
  "apps": {
    "Vivado": "full",
    "Terminal": "click",
    "Slack": "read",
    "1Password": "read"
  },
  "app_default": "ask"
}
```

## Keys

All keys are optional. A key left out leaves that choice to the person.

| Key | Values | Effect |
| --- | --- | --- |
| `mode` | `"secure"` | Every client runs in secure mode: app questions, Allow/Deny for risky shortcuts and for buttons like Send, Delete and Pay, no typing into password fields, pause on take-over. `--turbo`, `DOBBY_MODE=turbo`, `DOBBY_OVERLAY_TURBO=1`, `DOBBY_OVERLAY=0` and `DOBBY_CONFIRM_CLICKS=0` are ignored. |
| `tools.deny` | list of tool names | These tools are hidden from the AI and refused if called. |
| `tools.allow` | list of tool names | When not empty, only these tools work. `manage_toolsets` and `wait_for_wake` always stay callable. |
| `shell.enabled` | `true` / `false` | Whether `run_terminal_command` works at all. Without a policy it is off unless the person sets `DOBBY_ALLOW_SHELL=1`. |
| `shell.allow` | list of patterns | Only commands matching a pattern run (`*` matches anything). They run without a shell, so `;`, `\|`, `&`, `` ` ``, `$` and redirects are refused. |
| `apps` | app name to `full`, `click` or `read` | What Dobby may do in each app: `full` clicks and types, `click` only clicks and scrolls, `read` only looks. These win over `~/.dobby/apps.json` and over the person's Allow, and apply in turbo mode too. |
| `app_default` | `ask`, `full`, `click`, `read` | The tier for apps not listed. `ask` (default) keeps the "Let Dobby use …?" question in secure mode. |

App names match without case and without endings like `.app` or `-bin`.

## Fail closed

If the file exists but cannot be read, is not valid JSON, or has an unknown
value, Dobby does not guess. Until it is fixed: every client is secure, only
`manage_toolsets` and `wait_for_wake` work, the shell is off, and every app is
read-only. The reason is written to `~/.dobby/logs/server.log` and returned to
the AI with each refused call.

Under a secure policy, if the overlay cannot be shown (no display, for
example), nobody can press Allow, so every question is answered Deny.

## What it does not cover yet

- An audit log of every action (planned).
- Keeping Dobby from taking screenshots of particular apps. A `read` tier still
  lets Dobby look.
