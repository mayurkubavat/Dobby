"""The person's words with a Deny reach the AI that asked."""

from __future__ import annotations

import threading
import time

from pinlet import handoff
from pinlet.hub import control, prompts
from pinlet.overlay.client import OverlayDenied


def _ask_in_thread(out):
    def run():
        ok = prompts.ask("Allow Pinlet to click ‘Send’?", secs=5)
        try:
            if not ok:
                raise OverlayDenied("The user denied: click ‘Send’")
        except OverlayDenied as exc:
            out.append(handoff.classify(exc))

    t = threading.Thread(target=run)
    t.start()
    return t


def _wait_pending():
    end = time.monotonic() + 3
    while not prompts.pending() and time.monotonic() < end:
        time.sleep(0.01)
    return prompts.pending()[0]["id"]


def test_deny_with_a_reason_reaches_the_ai(monkeypatch):
    prompts._reset_for_tests()
    out: list = []
    t = _ask_in_thread(out)
    pid = _wait_pending()
    reply = control.handle(
        {"op": "answer", "prompt_id": pid, "ok": False, "reason": "  use the\ntest account "},
        lambda: None,
    )
    assert reply["answered"]
    t.join(3)
    result = out[0]
    assert result["handoff"] == "denied"
    assert result["person_said"] == "use the test account"
    assert "person_said" in result["note"]


def test_a_plain_deny_has_no_reason_and_reasons_do_not_leak():
    prompts._reset_for_tests()
    out: list = []
    t = _ask_in_thread(out)
    prompts.answer(_wait_pending(), False)
    t.join(3)
    assert "person_said" not in out[0]
    assert prompts.take_reason() == ""  # this thread never asked


def test_allow_ignores_a_reason_and_long_reasons_are_cut():
    prompts._reset_for_tests()
    results: list = []

    def run():
        results.append(prompts.ask("q?", secs=5))

    t = threading.Thread(target=run)
    t.start()
    prompts.answer(_wait_pending(), True, reason="ignored")
    t.join(3)
    assert results == [True]
    p = prompts.Prompt(id=999, text="q")
    with prompts._lock:
        prompts._pending[999] = p
    prompts.answer(999, False, reason="x" * 2000)
    assert len(p.reason) == prompts.MAX_REASON
    prompts._reset_for_tests()
