"""PTY bridge for `hermes dashboard` chat tab.

Wraps a child process behind a pseudo-terminal so its ANSI output can be
streamed to a browser-side terminal emulator (xterm.js) and typed
keystrokes can be fed back in.  The only caller today is the
``/api/pty`` WebSocket endpoint in ``hermes_cli.web_server``.

Design constraints:

* **Platform PTY bindings.**  POSIX hosts use ``ptyprocess`` plus raw fd
  reads/writes.  Native Windows uses ``pywinpty``/ConPTY; that API is
  text-oriented, so this bridge converts UTF-8 bytes at the boundary while
  keeping the browser/WebSocket contract byte-oriented.
* **Zero Node dependency on the server side.**  We use :mod:`ptyprocess`,
  which is a pure-Python wrapper around the OS calls.  The browser talks
  to the same ``hermes --tui`` binary it would launch from the CLI, so
  every TUI feature (slash popover, model picker, tool rows, markdown,
  skin engine, clarify/sudo/approval prompts) ships automatically.
* **Byte-safe I/O.**  Reads and writes go through the PTY master fd
  directly — we avoid :class:`ptyprocess.PtyProcessUnicode` because
  streaming ANSI is inherently byte-oriented and UTF-8 boundaries may land
  mid-read.
"""

from __future__ import annotations

import errno
import os
import struct
import sys
import time
from typing import Optional, Sequence

if sys.platform.startswith("win"):
    try:
        import winpty  # type: ignore
        _PTY_BACKEND = "windows"
    except ImportError:  # pragma: no cover - dev env without pywinpty
        winpty = None  # type: ignore
        _PTY_BACKEND = None
    ptyprocess = None  # type: ignore
    fcntl = None  # type: ignore
    select = None  # type: ignore
    signal = None  # type: ignore
    termios = None  # type: ignore
else:
    try:
        import fcntl  # type: ignore
        import select  # type: ignore
        import signal  # type: ignore
        import termios  # type: ignore
        import ptyprocess  # type: ignore
        _PTY_BACKEND = "posix"
    except ImportError:  # pragma: no cover - dev env without ptyprocess
        fcntl = None  # type: ignore
        select = None  # type: ignore
        signal = None  # type: ignore
        termios = None  # type: ignore
        ptyprocess = None  # type: ignore
        _PTY_BACKEND = None


__all__ = ["PtyBridge", "PtyUnavailableError"]


# ``struct winsize`` packs rows/cols as unsigned short (0..65535).  We clamp
# well below that ceiling: real terminals never exceed a couple thousand
# columns, and a value above this is a broken probe (WSL2 reports
# columns=131072) rather than a genuine ultrawide.  Lower bound is 1 — a
# zero/negative dimension is the classic "no size yet" signal.
_MIN_DIMENSION = 1
_MAX_COLS = 2000
_MAX_ROWS = 1000


def _clamp_dimension(value: int, maximum: int) -> int:
    """Clamp a reported terminal dimension into ``[_MIN_DIMENSION, maximum]``.

    Non-integer / non-finite values fall back to ``_MIN_DIMENSION`` so a bad
    probe can never reach ``struct.pack`` and raise ``struct.error``.
    """
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return _MIN_DIMENSION
    if n < _MIN_DIMENSION:
        return _MIN_DIMENSION
    if n > maximum:
        return maximum
    return n


class PtyUnavailableError(RuntimeError):
    """Raised when a PTY cannot be created on this platform.

    Today this means native Windows without ``pywinpty`` or a POSIX dev
    environment missing the ``ptyprocess`` dependency.  The dashboard
    surfaces the message to the user as a chat-tab banner.
    """


class PtyBridge:
    """Thin wrapper around ``ptyprocess.PtyProcess`` for byte streaming.

    Not thread-safe.  A single bridge is owned by the WebSocket handler
    that spawned it; the reader runs in an executor thread while writes
    happen on the event-loop thread.  Both sides are OK because the
    kernel PTY is the actual synchronization point — we never call
    :mod:`ptyprocess` methods concurrently, we only call ``os.read`` and
    ``os.write`` on the master fd, which is safe.
    """

    def __init__(self, proc: object, backend: str):
        self._proc = proc
        self._backend = backend
        self._fd: int | None = getattr(proc, "fd", None)
        self._closed = False

    # -- lifecycle --------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """True if a PTY can be spawned on this platform."""
        return _PTY_BACKEND is not None

    @classmethod
    def spawn(
        cls,
        argv: Sequence[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        cols: int = 80,
        rows: int = 24,
    ) -> "PtyBridge":
        """Spawn ``argv`` behind a new PTY and return a bridge.

        Raises :class:`PtyUnavailableError` if the platform can't host a
        PTY.  Raises :class:`FileNotFoundError` or :class:`OSError` for
        ordinary exec failures (missing binary, bad cwd, etc.).
        """
        if _PTY_BACKEND is None:
            if sys.platform.startswith("win"):
                raise PtyUnavailableError(
                    "The `pywinpty` package is missing. "
                    "Install with: pip install pywinpty."
                )
            if ptyprocess is None:
                raise PtyUnavailableError(
                    "The `ptyprocess` package is missing. "
                    "Install with: pip install ptyprocess "
                    "(or pip install -e '.[pty]')."
                )
            raise PtyUnavailableError("Pseudo-terminals are unavailable.")
        # PTY-hosted programs expect TERM to describe the terminal type.
        # CI often runs without TERM in the parent process, which makes
        # simple terminal probes like `tput cols` fail before winsize reads.
        # Preserve explicit caller overrides, but backfill a sensible default
        # when TERM is missing or blank.
        spawn_env = (os.environ.copy() if env is None else env.copy())
        if not spawn_env.get("TERM"):
            spawn_env["TERM"] = "xterm-256color"
        if _PTY_BACKEND == "windows":
            # pywinpty reads this process env var when constructing
            # PtyProcess, not from the child env mapping. Nonblocking reads let
            # the WebSocket pump poll and shut down promptly.
            previous_block = os.environ.get("PYWINPTY_BLOCK")
            os.environ["PYWINPTY_BLOCK"] = "0"
            spawn_env["PYWINPTY_BLOCK"] = "0"
            try:
                proc = winpty.PtyProcess.spawn(  # type: ignore[union-attr]
                    list(argv),
                    cwd=cwd,
                    env=spawn_env,
                    dimensions=(rows, cols),
                )
            finally:
                if previous_block is None:
                    os.environ.pop("PYWINPTY_BLOCK", None)
                else:
                    os.environ["PYWINPTY_BLOCK"] = previous_block
            return cls(proc, "windows")

        proc = ptyprocess.PtyProcess.spawn(  # type: ignore[union-attr]
            list(argv),
            cwd=cwd,
            env=spawn_env,
            dimensions=(rows, cols),
        )
        return cls(proc, "posix")

    @property
    def pid(self) -> int:
        return int(self._proc.pid)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    # -- I/O --------------------------------------------------------------

    def read(self, timeout: float = 0.2) -> Optional[bytes]:
        """Read up to 64 KiB of raw bytes from the PTY master.

        Returns:
            * bytes — zero or more bytes of child output
            * empty bytes (``b""``) — no data available within ``timeout``
            * None — child has exited and the master fd is at EOF

        Never blocks longer than ``timeout`` seconds.  Safe to call after
        :meth:`close`; returns ``None`` in that case.
        """
        if self._closed:
            return None
        if self._backend == "windows":
            try:
                text = self._proc.read(65536)  # type: ignore[attr-defined]
            except EOFError:
                return None
            if not text:
                return b""
            return str(text).encode("utf-8", "replace")

        if self._fd is None:
            return None
        try:
            readable, _, _ = select.select([self._fd], [], [], timeout)  # type: ignore[union-attr]
        except (OSError, ValueError):
            return None
        if not readable:
            return b""
        try:
            data = os.read(self._fd, 65536)
        except OSError as exc:
            # EIO on Linux = slave side closed.  EBADF = already closed.
            if exc.errno in {errno.EIO, errno.EBADF}:
                return None
            raise
        if not data:
            return None
        return data

    def write(self, data: bytes) -> None:
        """Write raw bytes to the PTY master (i.e. the child's stdin)."""
        if self._closed or not data:
            return
        if self._backend == "windows":
            try:
                self._proc.write(data.decode("utf-8", "ignore"))  # type: ignore[attr-defined]
            except EOFError:
                return
            return

        if self._fd is None:
            return
        # os.write can return a short write under load; loop until drained.
        view = memoryview(data)
        while view:
            try:
                n = os.write(self._fd, view)
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF, errno.EPIPE}:
                    return
                raise
            if n <= 0:
                return
            view = view[n:]

    def resize(self, cols: int, rows: int) -> None:
        """Forward a terminal resize to the child via ``TIOCSWINSZ``.

        Dimensions are clamped to a sane range first.  Some hosts report
        garbage window sizes — the motivating case is WSL2, where xterm.js
        in the dashboard ``/chat`` tab can pick up ``columns=131072,
        rows=1`` from a broken winsize probe.  ``struct winsize`` packs each
        field as an unsigned short (max 65535), so an unclamped 131072 would
        raise ``struct.error`` (not ``OSError``) and break the resize path,
        leaving the TUI laid out for a one-row / absurdly-wide screen —
        which is what shows up as blank / disappearing text.
        """
        if self._closed:
            return
        cols = _clamp_dimension(cols, _MAX_COLS)
        rows = _clamp_dimension(rows, _MAX_ROWS)
        if self._backend == "windows":
            try:
                self._proc.setwinsize(rows, cols)  # type: ignore[attr-defined]
            except Exception:
                pass
            return

        # struct winsize: rows, cols, xpixel, ypixel (all unsigned short)
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)  # type: ignore[union-attr]
        except OSError:
            pass

    # -- teardown ---------------------------------------------------------

    def close(self) -> None:
        """Terminate the child (SIGTERM → 0.5s grace → SIGKILL) and close fds.

        Idempotent.  Reaping the child is important so we don't leak
        zombies across the lifetime of the dashboard process.
        """
        if self._closed:
            return
        self._closed = True

        if self._backend == "windows":
            try:
                self._proc.close(force=True)  # type: ignore[attr-defined]
            except Exception:
                pass
            return

        # SIGHUP is the conventional "your terminal went away" signal.
        # We escalate if the child ignores it.
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):  # type: ignore[union-attr]
            if not self._proc.isalive():
                break
            try:
                self._proc.kill(sig)
            except Exception:
                pass
            deadline = time.monotonic() + 0.5
            while self._proc.isalive() and time.monotonic() < deadline:
                time.sleep(0.02)

        try:
            self._proc.close(force=True)
        except Exception:
            pass

    # Context-manager sugar — handy in tests and ad-hoc scripts.
    def __enter__(self) -> "PtyBridge":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
