"""One shared Pinlet per computer (architecture option B, step one).

Before: every AI client started its own ``pinlet`` server, and every server
started its own overlay, loaded its own models and fought the others through
lock files. Now:

* ``pinlet serve`` runs the engine once and listens on a private Unix socket
  (:func:`paths.socket_path`, owner-only).
* ``pinlet`` (what clients already launch) is a small **connector**: it says
  hello (client pid, name, turbo or secure), then pipes MCP messages between
  the client's stdio and the socket. It starts the shared Pinlet when none is
  running, and falls back to an in-process server when it cannot.
* Each connection is its own session: its own mode (``pinlet.mode``), its own
  shared-control agent (``pinlet.control``), one entry in :mod:`sessions`.

``PINLET_SHARED=0`` keeps the old one-server-per-client behaviour.
"""
