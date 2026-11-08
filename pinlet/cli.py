"""The ``pinlet`` command.

    pinlet              connect this AI client to the shared Pinlet (starting it if needed)
    pinlet --secure     the same, with every permission prompt for this client
    pinlet --careful    the same, asking only before risky steps (Send, Delete, Pay)
    pinlet serve        run the shared Pinlet (normally started for you)
    pinlet stdio        run a private Pinlet for this client only (the old behaviour)
    pinlet setup        register Pinlet and its skills with every AI tool on this computer
                        (--dry-run to preview, --list to see each tool; pinlet setup --help)
    pinlet doctor       check that Pinlet can run here and say how to fix what is missing
    pinlet mode         how much Pinlet asks, per AI client (pinlet mode cursor careful)
    pinlet policy       check an admin policy file before deploying it, or show the one in force
                        (pinlet policy check policy.json, pinlet policy show)
    pinlet activity     what the agents did lately, from the activity journal (--raw, -n N)

Set ``PINLET_SHARED=0`` to make plain ``pinlet`` behave like ``pinlet stdio``.
Kept free of engine imports so connecting is fast.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    """Runs the ``pinlet`` command line.

    With no command it connects this AI client to the shared Pinlet; ``serve`` runs the
    shared Pinlet and ``stdio`` a private one. ``pinlet --help`` prints the module docstring.
    """
    argv = list(sys.argv if argv is None else argv)
    command = next((a for a in argv[1:] if not a.startswith("-")), "")
    if command == "serve":
        from pinlet.hub.daemon import serve

        sys.exit(serve())
    if command == "setup":
        from pinlet.agent_setup import main as setup

        rest = argv[argv.index("setup") + 1 :]
        sys.exit(setup(rest))
    if command == "doctor":
        from pinlet.doctor import main as doctor

        sys.exit(doctor(argv[argv.index("doctor") + 1 :]))
    if command == "mode":
        from pinlet.mode import main as mode

        sys.exit(mode(argv[argv.index("mode") + 1 :]))
    if command == "policy":
        from pinlet.policy import main as policy

        sys.exit(policy(argv[argv.index("policy") + 1 :]))
    if command == "activity":
        from pinlet.journal import main as activity

        sys.exit(activity(argv[argv.index("activity") + 1 :]))
    if command == "stdio":
        from pinlet.hub.connector import run_in_process

        run_in_process()
        return
    if command in ("help",) or "--help" in argv or "-h" in argv:
        print(__doc__.strip())
        return
    from pinlet.hub.connector import main as connect

    connect(argv)


if __name__ == "__main__":
    main()
