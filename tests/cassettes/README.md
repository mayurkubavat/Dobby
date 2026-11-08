# Replay cassettes

Each folder here is a recorded cassette (`index.json`, optional `frames/`) with an
optional `expect.json`. `pytest -m replay` replays every one through the HAL input
API and checks the expectations against the final frame.

Record one from a live session with the `start_cassette_recording` /
`stop_cassette_recording` MCP tools (domain `skills`), then copy the folder from
`~/.pinlet/cassettes/<name>` into this directory and add `expect.json`.

Check a single cassette from the command line:

    python scripts/replay_cassette.py tests/cassettes/example_click
