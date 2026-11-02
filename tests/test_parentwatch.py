import threading

from dobby import parentwatch


def test_parent_gone_when_reparented_or_dead():
    assert not parentwatch.parent_gone(10, current=10, alive=lambda p: True)
    assert parentwatch.parent_gone(10, current=1, alive=lambda p: True)  # re-parented to launchd
    assert parentwatch.parent_gone(10, current=10, alive=lambda p: False)


def test_watch_calls_on_orphan_once(monkeypatch):
    fired = threading.Event()
    monkeypatch.setattr(parentwatch, "parent_gone", lambda parent: True)
    thread = parentwatch.watch_parent(fired.set, interval=0.01, parent=4242)
    assert thread is not None and fired.wait(2)
    thread.join(2)
    assert not thread.is_alive()


def test_no_watch_without_a_real_parent():
    assert parentwatch.watch_parent(lambda: None, parent=1) is None
