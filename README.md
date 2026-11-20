<img src="assets/icon.svg" width="64" height="64" alt="">

# CCBridge

Give Claude Code work from the Claude voice app, by email.

The voice app emails you (from your own Gmail account, to yourself) with a
subject starting `Claude Code:`. A small service on an always-on machine sees
it, runs `claude -p` with the email as the prompt, and replies in the same email
thread. The voice app reads the reply aloud. Reply in the thread to follow up;
the follow-up resumes the same Claude session.

## Safety

Only messages in the account's own **Sent** folder are acted on, so mail from
anyone else is ignored, whatever its From header says. The bridge marks its own
replies with an `X-CCBridge` header and skips them. Every message it looks at
gets the Gmail label `ccbridge`, so nothing runs twice.

`claude -p` runs with full permissions, because a headless run can't ask. The
trust boundary is your Gmail account.

## Setup

1. Put a Gmail app password in `~/.config/ccbridge/app_password` (mode 600),
   and set `ADDRESS` in `ccbridge.py`. Replies go out through the local
   `sendmail`, which must relay through the same Gmail account.
2. Install the service:
   ```bash
   cp ccbridge.service ~/.config/systemd/user/
   systemctl --user enable --now ccbridge
   ```
3. Tell the voice app how to use it, for example:
   > To give Claude Code work, email me with the subject "Claude Code: " plus a
   > short summary, and the task in the body. The answer comes back as a reply
   > in the same thread within a few minutes. Reply in that thread to follow up.

`workspace/CLAUDE.md` tells Claude to answer briefly for the ear, and to start
a T3 Code thread for bigger jobs.
