#!/usr/bin/env python3
"""Email bridge: run `claude -p` on emails Dan sends himself, reply by email.

A request is a message in Dan's own Sent folder (so it can't come from anyone
else) whose subject starts with "Claude Code:". Replies in the same email thread
continue the same Claude session. Standard library only.
"""

import email
import email.policy
import imaplib
import json
import logging
import os
import re
import ssl
import subprocess
import threading
import time
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

ADDRESS = "dan.bolser@outsee.co.uk"
PASSWORD_FILE = Path("~/.config/ccbridge/app_password").expanduser()
STATE_FILE = Path("~/.local/state/ccbridge/sessions.json").expanduser()
WORKDIR = Path(__file__).resolve().parent / "workspace"
SUBJECT_RE = re.compile(r"^\s*(re:\s*)*claude code\s*:", re.I)
SEEN_LABEL = "ccbridge"
BRIDGE_HEADER = "X-CCBridge"
SEARCH = f'in:sent to:me subject:(claude code) -label:{SEEN_LABEL}'
POLL_SECONDS = 30
JOB_TIMEOUT = 60 * 60

log = logging.getLogger("ccbridge")
state_lock = threading.Lock()
thread_locks: dict[str, threading.Lock] = {}


def load_sessions() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return {}


def save_session(thread_id: str, session_id: str) -> None:
    with state_lock:
        sessions = load_sessions()
        sessions[thread_id] = session_id
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(sessions, indent=1))
        tmp.replace(STATE_FILE)  # atomic, so readers never see a half-written file


def body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    text = part.get_content() if part else ""
    if part is not None and part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    # Drop the quoted history of a reply; Claude's session already has it.
    text = re.split(r"\n\s*On .{0,200}wrote:\s*\n", text, maxsplit=1)[0]
    return "\n".join(l for l in text.splitlines() if not l.startswith(">")).strip()


def send_reply(request: EmailMessage, text: str) -> None:
    subject = request["Subject"]
    reply = EmailMessage()
    reply["From"] = f"Claude Code bridge <{ADDRESS}>"
    reply["To"] = ADDRESS
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    reply["Message-ID"] = make_msgid(domain="outsee.co.uk")
    reply["In-Reply-To"] = request["Message-ID"]
    reply["References"] = f'{request.get("References", "")} {request["Message-ID"]}'.strip()
    reply[BRIDGE_HEADER] = "reply"
    reply.set_content(text)
    subprocess.run(["/usr/sbin/sendmail", "-t", "-oi"], input=reply.as_bytes(), check=True)


def run_claude(prompt: str, session_id: str | None) -> tuple[str, str | None]:
    cmd = ["claude", "-p", "--output-format", "json", "--permission-mode", "bypassPermissions"]
    if session_id:
        cmd += ["--resume", session_id]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           cwd=WORKDIR, timeout=JOB_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"Sorry, the job ran for over {JOB_TIMEOUT // 60} minutes and was stopped.", session_id
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        return f"Sorry, Claude Code failed (exit {p.returncode}):\n{p.stderr[-2000:]}", session_id
    return out.get("result") or "(Claude Code returned no text.)", out.get("session_id")


def handle(thread_id: str, msg: EmailMessage) -> None:
    lock = thread_locks.setdefault(thread_id, threading.Lock())
    with lock:  # follow-ups in one email thread run in order
        prompt = f'Subject: {msg["Subject"]}\n\n{body_text(msg)}'
        session_id = load_sessions().get(thread_id)
        log.info("job thread=%s resume=%s subject=%r", thread_id, session_id, msg["Subject"])
        result, new_session = run_claude(prompt, session_id)
        if new_session:
            save_session(thread_id, new_session)
        send_reply(msg, result)
        log.info("replied thread=%s", thread_id)


def poll(imap: imaplib.IMAP4_SSL) -> None:
    imap.select('"[Gmail]/All Mail"')
    _, data = imap.uid("SEARCH", "X-GM-RAW", f'"{SEARCH}"')
    for uid in data[0].split():
        _, parts = imap.uid("FETCH", uid, "(X-GM-LABELS X-GM-THRID BODY.PEEK[])")
        meta, raw = parts[0][0].decode(), parts[0][1]
        # Mark first: a crash must never re-run a job (at-most-once).
        if imap.uid("STORE", uid, "+X-GM-LABELS", f"({SEEN_LABEL})")[0] != "OK":
            raise RuntimeError(f"could not label message {uid!r}")
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        if not re.search(r"X-GM-LABELS \([^)]*\\Sent", meta):  # only mail from this account
            continue
        if msg[BRIDGE_HEADER] or not SUBJECT_RE.match(msg["Subject"] or ""):
            continue
        thread_id = re.search(r"X-GM-THRID (\d+)", meta).group(1)
        threading.Thread(target=handle_safely, args=(thread_id, msg), daemon=True).start()


def handle_safely(thread_id: str, msg: EmailMessage) -> None:
    try:
        handle(thread_id, msg)
    except Exception as e:
        log.exception("job failed thread=%s", thread_id)
        try:
            send_reply(msg, f"Sorry, the email bridge failed before Claude Code could answer: {e!r}")
        except Exception:
            log.exception("could not send failure email")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    password = PASSWORD_FILE.read_text().strip()
    while True:
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com", ssl_context=ssl.create_default_context()) as imap:
                imap.login(ADDRESS, password)
                log.info("connected, polling every %ss", POLL_SECONDS)
                while True:
                    poll(imap)
                    time.sleep(POLL_SECONDS)
        except Exception:
            log.exception("IMAP error, reconnecting in 60s")
            time.sleep(60)


if __name__ == "__main__":
    main()
