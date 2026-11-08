"""Replays every cassette in tests/cassettes. Run alone with `pytest -m replay`."""

from pathlib import Path

import pytest

from pinlet.hal.cassette_replay import discover_cassettes, replay_cassette

CASSETTES = discover_cassettes(Path(__file__).parent / "cassettes")


@pytest.mark.replay
@pytest.mark.parametrize("cassette", CASSETTES, ids=[p.name for p in CASSETTES])
def test_cassette_replays(cassette: Path):
    report = replay_cassette(cassette)
    assert report["ok"], "\n".join(report["failures"])
    assert report["steps"] == report["total_steps"]


def test_example_cassette_is_discovered():
    assert any(p.name == "example_click" for p in CASSETTES)
