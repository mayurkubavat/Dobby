"""``pinlet serve``: the shared Pinlet. One engine, one overlay, many clients.

It listens on :func:`pinlet.hub.paths.socket_path` (owner-only), serves one
MCP session per connection, and exits after ``PINLET_IDLE_EXIT`` seconds
(default 600, 0 = never) with no clients. Only one runs per user: a second
``pinlet serve`` sees the lock and exits.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import socket
import sys
import time
from typing import Any

import anyio
import anyio.abc
from anyio.streams.buffered import BufferedByteReceiveStream

from . import paths, sessions

log = logging.getLogger("pinlet.hub")
MAX_LINE = 64 * 1024 * 1024


def _idle_exit_s() -> float:
    try:
        return max(0.0, float(os.environ.get("PINLET_IDLE_EXIT", "600")))
    except ValueError:
        return 600.0


def _peer_pid(sock: Any) -> int | None:
    """The connecting process, from the kernel (not from what it claims)."""
    raw = sock.extra(anyio.abc.SocketAttribute.raw_socket, None)
    if raw is None:
        return None
    try:
        if hasattr(socket, "SO_PEERCRED"):  # Linux
            import struct

            data = raw.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            return struct.unpack("3i", data)[0]
        if sys.platform == "darwin":
            LOCAL_PEERPID = 0x002
            import struct

            data = raw.getsockopt(0, LOCAL_PEERPID, struct.calcsize("i"))  # SOL_LOCAL = 0
            return struct.unpack("i", data)[0]
    except OSError:
        return None
    return None


def _same_user(sock: Any) -> bool:
    raw = sock.extra(anyio.abc.SocketAttribute.raw_socket, None)
    if raw is None or not hasattr(socket, "SO_PEERCRED"):
        return True  # macOS: the socket's directory and mode already keep others out
    import struct

    try:
        _pid, uid, _gid = struct.unpack(
            "3i", raw.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        )
    except OSError:
        return False
    return uid == os.getuid()


class Hub:
    """The shared Pinlet: serves MCP sessions and control requests on one Unix socket."""

    def __init__(self, server: Any) -> None:
        self.server = server  # pinlet.server module
        self.last_client = time.monotonic()
        self.task_group: Any = None
        self.quitting = False

    async def handle(self, stream: Any) -> None:
        """Serves one connection: an MCP client session or a control connection."""
        from pinlet.control import new_session_controller, session_controller
        from pinlet.mode import session_mode

        buffered = BufferedByteReceiveStream(stream)
        info = None
        ctl = None
        try:
            if not _same_user(stream):
                await stream.send(b'{"pinlet_ready": 0, "error": "not your Pinlet"}\n')
                return
            try:
                line = await buffered.receive_until(b"\n", 64 * 1024)
                hello = json.loads(line.decode() or "{}")
            except Exception:  # noqa: BLE001
                await stream.send(b'{"pinlet_ready": 0, "error": "expected a hello line"}\n')
                return
            if hello.get("pinlet_control") == 1:  # the Pinlet app (menu bar / tray)
                await self._serve_control(buffered, stream)
                return
            if hello.get("pinlet_hello") != 1:
                await stream.send(b'{"pinlet_ready": 0, "error": "unknown hello version"}\n')
                return
            pid = _peer_pid(stream) or hello.get("pid")
            from pinlet.policy import current as admin_policy

            # An admin policy can make every client secure, whatever it asked for.
            mode = (
                "secure"
                if hello.get("mode") == "secure" or admin_policy().forces_secure
                else "turbo"
            )
            name = str(hello.get("name") or "AI client")[:40]
            from pinlet.control import pick_first_name
            from pinlet.control.names import display

            # Pick the name and reserve it before any await, so two clients
            # connecting at once never both get "Tibble".
            first = pick_first_name(pid or 0)
            info = sessions.open_session(display(first, name), pid, mode)
            info.first_name, info.client = first, name
            ctl = await anyio.to_thread.run_sync(
                lambda: new_session_controller(name, pid, first_name=first)
            )
            log.info("client %s connected: %s (pid %s, %s)", info.id, name, pid, mode)
            await stream.send(
                (
                    json.dumps({"pinlet_ready": 1, "server_pid": os.getpid(), "session": info.id})
                    + "\n"
                ).encode()
            )
            token = sessions.CURRENT.set(info)
            try:
                with session_mode(mode), session_controller(ctl):
                    await self._serve_mcp(buffered, stream)
            finally:
                sessions.CURRENT.reset(token)
        except (anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass
        finally:
            if info is not None:
                sessions.close_session(info)
                log.info("client %s disconnected", info.id)
            if ctl is not None:
                await anyio.to_thread.run_sync(ctl.close)
            self.last_client = time.monotonic()
            with contextlib.suppress(Exception):
                await stream.aclose()

    async def _serve_control(self, buffered: BufferedByteReceiveStream, stream: Any) -> None:
        """The Pinlet app's connection: status, menu content, Pause/Stop, quit."""
        from . import control

        await stream.send(
            (
                json.dumps({"pinlet_ready": 1, "control": 1, "server_pid": os.getpid()}) + "\n"
            ).encode()
        )
        while True:
            try:
                line = await buffered.receive_until(b"\n", 1024 * 1024)
            except (
                anyio.IncompleteRead,
                anyio.EndOfStream,
                anyio.BrokenResourceError,
                anyio.ClosedResourceError,
            ):
                return
            if not line.strip():
                continue
            quit_asked: list = []
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("expected an object")
            except ValueError as exc:
                reply = {"ok": False, "error": f"bad request: {exc}"}
            else:
                reply = await anyio.to_thread.run_sync(
                    lambda: control.handle(request, lambda: quit_asked.append(1))
                )
            await stream.send((json.dumps(reply) + "\n").encode())
            if quit_asked:
                self.quit()
                return

    def quit(self, reason: str = "the Pinlet app") -> None:
        """Ask the shared Pinlet to exit (from the app's Quit, or a signal).

        Args:
            reason: Who asked, for the log.
        """
        log.info("quit requested by %s", reason)
        self.quitting = True
        if self.task_group is not None:
            self.task_group.cancel_scope.cancel()

    async def _serve_mcp(self, buffered: BufferedByteReceiveStream, stream: Any) -> None:
        import mcp_types as types
        from mcp.shared._context_streams import create_context_streams
        from mcp.shared.message import SessionMessage

        read_writer, read_stream = create_context_streams[SessionMessage | Exception](0)
        write_stream, write_reader = create_context_streams[SessionMessage](0)

        async def reader() -> None:
            async with read_writer:
                while True:
                    try:
                        line = await buffered.receive_until(b"\n", MAX_LINE)
                    except (
                        anyio.IncompleteRead,
                        anyio.EndOfStream,
                        anyio.BrokenResourceError,
                        anyio.ClosedResourceError,
                    ):
                        break
                    if not line.strip():
                        continue
                    try:
                        message = types.jsonrpc_message_adapter.validate_json(line, by_name=False)
                    except Exception as exc:  # noqa: BLE001
                        await read_writer.send(exc)
                        continue
                    await read_writer.send(SessionMessage(message))

        async def writer() -> None:
            async with write_reader:
                async for msg in write_reader:
                    data = msg.message.model_dump_json(by_alias=True, exclude_unset=True) + "\n"
                    try:
                        await stream.send(data.encode())
                    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                        break

        low = self.server.mcp_server._lowlevel_server
        async with anyio.create_task_group() as tg:
            tg.start_soon(reader)
            tg.start_soon(writer)
            await low.run(read_stream, write_stream, low.create_initialization_options())
            tg.cancel_scope.cancel()

    async def idle_watch(self, tg: Any) -> None:
        """Exits the daemon after it has had no clients for ``PINLET_IDLE_EXIT`` seconds."""
        limit = _idle_exit_s()
        if limit <= 0:
            return
        while True:
            await anyio.sleep(min(30.0, limit))
            if sessions.count() == 0 and time.monotonic() - self.last_client >= limit:
                log.info("no clients for %.0fs: exiting", limit)
                tg.cancel_scope.cancel()
                return


def _take_lock() -> Any | None:
    path = paths.lock_path()
    paths.ensure_private_dir(path.parent)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


async def _quit_on_signal(hub: Hub) -> None:
    """Quits the shared Pinlet on SIGTERM or SIGINT the same way the app's Quit does.

    The app stops us with SIGTERM. Cancelling the task group lets every session
    close cleanly; raising KeyboardInterrupt from a signal handler instead could
    land in the middle of the event loop's own shutdown and leave the process
    hanging.
    """
    import signal

    try:
        receiver = anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT)
        with receiver as signals:
            async for signum in signals:
                hub.quit(signal.Signals(signum).name)
                return
    except NotImplementedError:  # no signal handlers on this event loop (Windows)
        await anyio.sleep_forever()


def serve() -> int:
    """Runs the shared Pinlet until it is told to quit or goes idle.

    Returns:
        The process exit code. Returns 0 at once if another shared Pinlet is running.
    """
    lock = _take_lock()
    if lock is None:
        sys.stderr.write("pinlet serve: another shared Pinlet is already running\n")
        return 0
    sock_path = paths.socket_path()
    paths.ensure_private_dir(sock_path.parent)
    with contextlib.suppress(FileNotFoundError):
        sock_path.unlink()  # stale: we hold the lock, so nobody else is listening

    import pinlet.server as server  # the engine: HAL, perception, overlay, models (slow)

    server.setup_server_log()
    server.tool_log.info("shared Pinlet %s listening on %s", os.getpid(), sock_path)
    hub_log = logging.getLogger("pinlet.hub")
    hub_log.addHandler(logging.StreamHandler(sys.stderr))
    hub_log.setLevel(logging.INFO)
    hub_log.propagate = False
    if server.CONFIG.gemma3_warmup_on_start:
        import threading

        threading.Thread(target=server._warmup_gemma, name="gemma-warmup", daemon=True).start()
    from pinlet.control import get_controller

    process_ctl = get_controller(create=False)
    if process_ctl is not None:
        process_ctl.close()  # every client gets its own agent; the server itself is not one
    hub = Hub(server)

    async def run() -> None:
        old_umask = os.umask(0o077)
        try:
            listener = await anyio.create_unix_listener(str(sock_path), mode=0o600)
        finally:
            os.umask(old_umask)
        async with anyio.create_task_group() as tg:
            hub.task_group = tg
            tg.start_soon(hub.idle_watch, tg)
            tg.start_soon(_quit_on_signal, hub)
            await listener.serve(hub.handle, task_group=tg)

    try:
        anyio.run(run)
    except KeyboardInterrupt:
        pass
    finally:
        with contextlib.suppress(FileNotFoundError):
            sock_path.unlink()
        lock.close()
    return 0
