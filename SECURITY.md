# Security policy

Pinlet controls a real mouse and keyboard and reads the screen, so we treat
security reports with priority.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through GitHub:
go to the repository's **Security** tab and choose **Report a vulnerability**
([direct link](https://github.com/mayurkubavat/Pinlet/security/advisories/new)).

Include what you did, what Pinlet did, the platform (macOS or Linux, version),
how Pinlet was started (`pinlet`, `pinlet --secure`, `pinlet stdio`) and any admin
policy in force. A short reproduction helps most.

We aim to acknowledge a report within 5 working days and to agree a fix and
disclosure date with you. We credit reporters who want to be credited.

## What counts

Examples of issues we want to hear about:

- Pinlet acting without the consent it should ask for: an action that skips
  the Allow/Deny prompt in secure mode, or an app used without a grant.
- A way around the admin policy file (`/Library/Application Support/Pinlet/policy.json`,
  `/etc/pinlet/policy.json`), such as running a denied tool or a shell command
  outside the allow list.
- Typed secrets (password fields) logged, returned to the model or kept on disk.
- Another local user or process reaching the shared Pinlet's socket or control
  files under `~/.pinlet`.
- Screen content sent off the machine without an opt-in.
- Tampering with the bundled ONNX model going unnoticed.

Prompt injection through on-screen text (a web page telling the AI to do
something) is a known risk of every computer-use agent. Reports that show it
getting past secure mode's prompts are in scope; reports that it is possible
at all in turbo mode are not.

## Supported versions

Pinlet is pre-1.0. Fixes go into the latest release and `master` only.
