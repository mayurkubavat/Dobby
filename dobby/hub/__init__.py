"""One shared Dobby per computer (architecture option B, step one).

Before: every AI client started its own ``dobby`` server, and every server
started its own overlay, loaded its own models and fought the others through
lock files. Now:

* ``dobby serve`` runs the engine once and listens on a private Unix socket
  (:func:`paths.socket_path`, owner-only).
* ``dobby`` (what clients already launch) is a small **connector**: it says
  hello (client pid, name, turbo or secure), then pipes MCP messages between
  the client's stdio and the socket. It starts the shared Dobby when none is
  running, and falls back to an in-process server when it cannot.
* Each connection is its own session: its own mode (``dobby.mode``), its own
  shared-control agent (``dobby.control``), one entry in :mod:`sessions`.

``DOBBY_SHARED=0`` keeps the old one-server-per-client behaviour.
"""
