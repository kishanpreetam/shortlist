"""Print resume pages with one headless Chrome kept open, instead of starting Chrome for every print.

build() prints a page up to 24 times to find the fullest one that still fits, and `chrome --print-to-pdf` spends most
of each print starting Chrome. Here Chrome starts on the first print and stays open until close(). Each page is printed
over the DevTools protocol the way --print-to-pdf does it (a new tab, the page loaded, Page.printToPDF with the same
settings, the tab closed), so the PDFs are the same, byte for byte but for their creation and modification times.

How it stays safe: Chrome talks over two pipes (--remote-debugging-pipe), never a port that any program on this Mac
could reach, and gets nothing else of ours but /dev/null. It runs with its own temporary profile, which also holds
every temporary file it makes, so close() removes them all. Anything unexpected (Chrome missing, a crash, an error, no
answer within 60 seconds) raises ChromeError and stops this Chrome for good, so the caller prints the old way.
close() asks Chrome to quit and kills what is left of it after a few seconds; if this program dies first, Chrome sees
its pipe close and quits on its own.
"""

from __future__ import annotations

import atexit
import base64
import fcntl
import json
import os
import select
import shutil
import signal
import tempfile
import threading
import time
from pathlib import Path

# _print_pdf's command line less --print-to-pdf and the page. --no-pdf-header-footer is left out too: all it does is set
# displayHeaderFooter below, and on its own it starts Chrome's command handler, which closes the first tabs opened.
# A Chrome kept open more than about 13 seconds starts downloading Google's models (about 47 MB) and installing its
# default apps; the last two flags stop both (the profile stays near 3 MB, not 56 MB) and leave every PDF the same.
FLAGS = ("--headless=new", "--disable-gpu", "--disable-features=OptimizationHints", "--disable-default-apps")
# What --print-to-pdf asks for (Chromium's components/headless/command_handler/headless_command.js) with these flags
PDF = {"displayHeaderFooter": False, "generateTaggedPDF": True, "printBackground": True, "preferCSSPageSize": True}
TIMEOUT = 60.0  # seconds for one print, Chrome's start included, as _print_pdf allows
GRACE = 3.0  # seconds Chrome gets to quit, or a print in another thread to end, before Chrome is killed


class ChromeError(Exception):
    """Chrome couldn't print over its pipe. This Chrome is stopped; print the old way."""


class Chrome:
    """One headless Chrome for many prints: started on the first, kept until close(). Prints run one at a time."""

    def __init__(self, path: str, timeout: float = TIMEOUT):
        self.path, self.timeout = path, timeout
        self.pid: int | None = None
        self._lock, self._proc = threading.Lock(), threading.Lock()  # one print at a time; the process and its profile
        self._rfd = self._wfd = -1
        self._buf, self._next, self._session, self._loads = bytearray(), 0, None, set()
        self._profile: str | None = None
        self._failed = ""  # why this Chrome stopped for good

    def __enter__(self) -> Chrome:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def print_pdf(self, page: Path, pdf: Path) -> None:
        """Print the HTML file `page` to `pdf` as `chrome --print-to-pdf` does. On any failure, raises ChromeError and
        stops Chrome; every later print then raises at once."""
        with self._lock:
            if self._failed:
                raise ChromeError(self._failed)
            end = time.monotonic() + self.timeout
            try:
                if self.pid is None:
                    self._start(end)
                data = self._print(Path(page).absolute().as_uri(), end)
                Path(pdf).write_bytes(data)
            except BaseException as exc:
                self._failed = self._failed or f"Chrome stopped: {exc}"
                self._stop()
                if isinstance(exc, ChromeError) or not isinstance(exc, Exception):
                    raise
                raise ChromeError(f"Chrome stopped: {exc}") from exc

    def close(self) -> None:
        """Stop Chrome and remove its profile, leaving nothing of it running. Safe to call twice, or from another thread
        while a print runs (that print then fails)."""
        self._failed = self._failed or "Chrome was closed."
        if self._lock.acquire(timeout=GRACE):
            try:
                self._stop()
            finally:
                self._lock.release()
        else:  # a print is stuck: kill Chrome under it; that print then fails and closes the pipes
            self._stop(graceful=False, pipes=False)

    def _start(self, end: float) -> None:
        self._profile = tempfile.mkdtemp(prefix="tailor-chrome-")
        temp = Path(self._profile) / "t"  # Chrome's temporary files go here (MAC_CHROMIUM_TMPDIR), not the shared one
        temp.mkdir()
        to_chrome, from_chrome = os.pipe(), os.pipe()  # Chrome reads fd 3 and writes fd 4
        self._wfd, self._rfd = to_chrome[1], from_chrome[0]
        ends: list[int] = []  # Chrome's two ends and their copies above 4, so neither dup2 below lands on the other
        try:
            for fd in (to_chrome[0], from_chrome[1]):
                ends += [fd, fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 5)]
            stray = [fd for fd in _open_fds() if fd > 4 and _inheritable(fd)]  # what subprocess's close_fds would close
            with self._proc:  # a close() from another thread either comes first, or finds this pid to stop
                if self._failed:
                    raise ChromeError(self._failed)
                self.pid = os.posix_spawn(
                    self.path, [self.path, *FLAGS, f"--user-data-dir={self._profile}", "--remote-debugging-pipe", "about:blank"],
                    {**os.environ, "MAC_CHROMIUM_TMPDIR": str(temp)}, setpgroup=0, file_actions=[
                        (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0), (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
                        (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0), (os.POSIX_SPAWN_DUP2, ends[1], 3),
                        (os.POSIX_SPAWN_DUP2, ends[3], 4), *((os.POSIX_SPAWN_CLOSE, fd) for fd in stray)])
        finally:
            for fd in ends:
                os.close(fd)
        atexit.register(self.close)  # an exit mid-build still stops it
        os.set_blocking(self._wfd, False)
        self._call("Browser.getVersion", end=end)  # answered once Chrome is up

    def _print(self, url: str, end: float) -> bytes:
        target = self._call("Target.createTarget", {"url": "about:blank"}, end=end)["targetId"]
        self._session = self._call("Target.attachToTarget", {"targetId": target, "flatten": True}, end=end)["sessionId"]
        self._loads.clear()
        self._call("Page.enable", end=end)
        self._call("Page.setLifecycleEventsEnabled", {"enabled": True}, end=end)
        nav = self._call("Page.navigate", {"url": url}, end=end)
        if nav.get("errorText") or nav.get("isDownload") or not nav.get("loaderId"):
            raise ChromeError(f"Chrome couldn't open the page: {nav.get('errorText') or nav}")
        while (nav["frameId"], nav["loaderId"]) not in self._loads:  # this document's load, not about:blank's
            self._read(end)
        data = base64.b64decode(self._call("Page.printToPDF", PDF, end=end)["data"], validate=True)
        self._session = None
        self._call("Target.closeTarget", {"targetId": target}, end=end)
        if not data.startswith(b"%PDF-"):
            raise ChromeError("Chrome's answer isn't a PDF.")
        return data

    def _call(self, method: str, params: dict | None = None, *, end: float) -> dict:
        """Send one command (to the page's session while one is open) and return its result."""
        self._next += 1
        ident = self._next
        msg = {"id": ident, "method": method, "params": params or {}}
        if self._session:
            msg["sessionId"] = self._session
        self._send(json.dumps(msg).encode() + b"\0", end)
        while (got := self._read(end)).get("id") != ident:
            pass
        if "error" in got:
            raise ChromeError(f"{method} failed: {got['error'].get('message', got['error'])}")
        return got.get("result", {})

    def _send(self, data: bytes, end: float) -> None:
        view, poller = memoryview(data), select.poll()
        poller.register(self._wfd, select.POLLOUT)
        while view:
            if not poller.poll(_ms(end)):
                raise ChromeError(f"Chrome didn't read for {self.timeout:g} seconds.")
            try:
                view = view[os.write(self._wfd, view):]
            except BlockingIOError:
                continue

    def _read(self, end: float) -> dict:
        """The next message from Chrome. For the open page, a load is noted and its tab or renderer going away raises;
        other events are dropped."""
        start, poller = 0, select.poll()
        poller.register(self._rfd, select.POLLIN)
        while (cut := self._buf.find(b"\0", start)) < 0:
            if not poller.poll(_ms(end)):
                raise ChromeError(f"Chrome didn't answer within {self.timeout:g} seconds.")
            start = len(self._buf)
            if not (chunk := os.read(self._rfd, 1 << 20)):
                raise ChromeError("Chrome quit.")
            self._buf += chunk
        msg = json.loads(self._buf[:cut])
        del self._buf[:cut + 1]
        if not isinstance(msg, dict):
            raise ChromeError("Chrome sent something that isn't a message.")
        method, p = msg.get("method"), msg.get("params") or {}
        if self._session and self._session in (msg.get("sessionId"), p.get("sessionId")):
            if method == "Page.lifecycleEvent" and p.get("name") == "load":
                self._loads.add((p.get("frameId"), p.get("loaderId")))
            elif method in ("Inspector.detached", "Target.detachedFromTarget"):
                raise ChromeError(f"Chrome closed the page ({p.get('reason') or 'detached'}).")
        return msg

    def _stop(self, graceful: bool = True, pipes: bool = True) -> None:
        """Ask Chrome to quit (a killed Chrome leaves its code-signing clone behind), kill what is left of it after GRACE
        seconds, and remove its profile. Closes the pipes too, unless a print in another thread still reads them."""
        with self._proc:
            pid, self.pid = self.pid, None
            if pid is not None:
                if graceful:
                    try:
                        self._send(b'{"id":0,"method":"Browser.close","params":{}}\0', time.monotonic() + 1)
                    except Exception:
                        pass
                _end(pid, GRACE if graceful else 0)
            if pipes:
                for fd in (self._rfd, self._wfd):
                    if fd >= 0:
                        os.close(fd)
                self._rfd = self._wfd = -1
                self._buf.clear()
            if self._profile:
                shutil.rmtree(self._profile, ignore_errors=True)
                self._profile = None
        atexit.unregister(self.close)


def _ms(end: float) -> int:
    left = end - time.monotonic()
    if left <= 0:
        raise ChromeError("Chrome didn't answer in time.")
    return max(1, int(left * 1000))


def _open_fds() -> list[int]:
    try:
        return [int(n) for n in os.listdir("/dev/fd")]
    except OSError:
        return []


def _inheritable(fd: int) -> bool:
    try:
        return os.get_inheritable(fd)
    except OSError:
        return False


def _end(pid: int, wait: float) -> None:
    """Give Chrome `wait` seconds to quit, then kill its whole group (Chrome and its helpers) and reap it. The group is
    signaled only while Chrome itself is unreaped, so its number can't be anyone else's yet. Never blocks for long: one
    that somehow outlives SIGKILL is left to the system."""
    try:
        if not _quit(pid, wait):
            _kill(pid)
            _quit(pid, GRACE)
        _kill(pid)  # helpers it left
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:  # reaped elsewhere: its number may be someone else's now, so nothing more is sent
        pass


def _quit(pid: int, wait: float) -> bool:
    """Whether Chrome has exited, waiting up to `wait` seconds, without reaping it."""
    end = time.monotonic() + wait
    while not os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
        if time.monotonic() >= end:
            return False
        time.sleep(0.01)
    return True


def _kill(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # none left (a group of only Chrome's unreaped self answers EPERM)
        pass
