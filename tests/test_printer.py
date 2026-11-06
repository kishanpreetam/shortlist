import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tailor import printer, resume

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class PipeTest(unittest.TestCase):
    """The message framing over a fake pipe: no Chrome needed."""

    def setUp(self):
        self.c = printer.Chrome("/nonexistent/chrome")
        self.c._rfd, self.ours = os.pipe()  # what "Chrome" says, read by the class
        self.theirs, self.c._wfd = os.pipe()  # what the class says, read by "Chrome"
        os.set_blocking(self.c._wfd, False)

    def tearDown(self):
        self.c.close()
        for fd in (self.ours, self.theirs):
            try:
                os.close(fd)
            except OSError:
                pass

    def test_answer_found_by_id_past_events_and_other_answers(self):
        self.c._session = "S"
        os.write(self.ours, b'{"method":"Page.lifecycleEvent","sessionId":"S","params":{"name":"load","frameId":"F",'
                            b'"loaderId":"L"}}\0{"method":"Page.lifecycleEvent","sessionId":"other","params":{"name":"load",'
                            b'"frameId":"G","loaderId":"M"}}\0{"id":99,"result":{}}\0{"id":1,"res')
        threading.Timer(0.05, os.write, (self.ours, b'ult":{"ok":true}}\0')).start()  # the answer split across reads
        self.assertEqual(self.c._call("Page.enable", end=time.monotonic() + 5), {"ok": True})
        self.assertEqual(self.c._loads, {("F", "L")})  # only the open page's load is noted
        sent = os.read(self.theirs, 4096)
        self.assertTrue(sent.endswith(b"\0"))
        self.assertEqual(json.loads(sent[:-1]), {"id": 1, "method": "Page.enable", "params": {}, "sessionId": "S"})

    def test_an_error_answer_or_a_closed_page_raises(self):
        os.write(self.ours, b'{"id":1,"error":{"message":"No target"}}\0')
        with self.assertRaisesRegex(printer.ChromeError, "No target"):
            self.c._call("Target.closeTarget", end=time.monotonic() + 5)
        self.c._session = "S"
        os.write(self.ours, b'{"method":"Inspector.detached","sessionId":"S","params":{"reason":"Render process gone."}}\0')
        with self.assertRaisesRegex(printer.ChromeError, "Render process gone"):
            self.c._read(time.monotonic() + 5)

    def test_chrome_quitting_or_not_answering_raises_promptly(self):
        t = time.monotonic()
        with self.assertRaisesRegex(printer.ChromeError, "didn't answer"):
            self.c._read(time.monotonic() + 0.2)
        os.close(self.ours)
        with self.assertRaisesRegex(printer.ChromeError, "quit"):
            self.c._read(time.monotonic() + 5)
        self.assertLess(time.monotonic() - t, 2)


class StartTest(unittest.TestCase):
    def test_a_bad_chrome_path_fails_fast_and_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            page, pdf = Path(d) / "page.html", Path(d) / "out.pdf"
            page.write_text("<p>Hi</p>")
            c = printer.Chrome(str(Path(d) / "no-chrome"))
            t = time.monotonic()
            with self.assertRaises(printer.ChromeError):
                c.print_pdf(page, pdf)
            self.assertLess(time.monotonic() - t, 5)
            self.assertIsNone(c.pid)
            self.assertIsNone(c._profile)  # its temporary profile is removed
            self.assertEqual((c._rfd, c._wfd), (-1, -1))
            with self.assertRaises(printer.ChromeError):  # stopped for good: the caller prints the old way
                c.print_pdf(page, pdf)
            c.close()
            c.close()

    def test_close_twice_and_print_after_close(self):
        c = printer.Chrome(CHROME)
        c.close()
        c.close()
        with self.assertRaisesRegex(printer.ChromeError, "closed"):
            c.print_pdf(Path("page.html"), Path("out.pdf"))
        self.assertIsNone(c.pid)


@unittest.skipUnless(os.path.exists(CHROME), "Chrome isn't installed")
class RealChromeTest(unittest.TestCase):
    def test_prints_one_page_then_leaves_nothing_running(self):
        with tempfile.TemporaryDirectory() as d:
            page, pdf = Path(d) / "page.html", Path(d) / "out.pdf"
            page.write_text("<!doctype html><title>t</title><style>@page{size:Letter}</style><p>Hello</p>")
            with printer.Chrome(CHROME) as c:
                c.print_pdf(page, pdf)
                pid, profile = c.pid, c._profile
                c.print_pdf(page, pdf)  # the same Chrome prints again
                self.assertEqual(c.pid, pid)
            self.assertEqual(resume._pages(pdf), 1)
            self.assertFalse(Path(profile).exists())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            with self.assertRaises(ProcessLookupError):  # nor any of its helpers
                os.killpg(pid, 0)


if __name__ == "__main__":
    unittest.main()
