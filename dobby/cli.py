"""The ``dobby`` command.

    dobby              connect this AI client to the shared Dobby (starting it if needed)
    dobby --secure     the same, with permission prompts for this client
    dobby serve        run the shared Dobby (normally started for you)
    dobby stdio        run a private Dobby for this client only (the old behaviour)

Set ``DOBBY_SHARED=0`` to make plain ``dobby`` behave like ``dobby stdio``.
Kept free of engine imports so connecting is fast.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    argv = list(sys.argv if argv is None else argv)
    command = next((a for a in argv[1:] if not a.startswith("-")), "")
    if command == "serve":
        from dobby.hub.daemon import serve

        sys.exit(serve())
    if command == "stdio":
        from dobby.hub.connector import run_in_process

        run_in_process()
        return
    if command in ("help",) or "--help" in argv or "-h" in argv:
        print(__doc__.strip())
        return
    from dobby.hub.connector import main as connect

    connect(argv)


if __name__ == "__main__":
    main()
