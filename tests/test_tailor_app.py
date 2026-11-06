import ast
import contextlib
import csv
import http.client
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from shortlist.roles import ROLE_BY_KEY
from tailor import __main__ as cli
from tailor import app, jobs, lexicon, page
from tailor import resume as engine

ROOT = Path(__file__).resolve().parent.parent
JD = "We need a data engineer with SQL, Airflow and Spark to build ETL pipelines and dashboards. " * 4
Q1 = "https://job-boards.greenhouse.io/acme/jobs/11?gh_jid=11&src=shortlist"  # a ? and an & to survive the ?job= link
Q2 = "https://jobs.lever.co/beta/22"
Q3 = "https://jobs.ashbyhq.com/gamma/33"
Q4 = "https://jobs.ashbyhq.com/delta/44"
P5 = "https://job-boards.greenhouse.io/epsilon/jobs/55"
ML = "Senior Machine Learning Engineer"
QUEUE = {"date": "2026-10-29", "target": 3, "items": [
    {"n": 1, "company": "Acme", "title": "Data Engineer", "url": Q1, "resume": "data-engineer", "spare": False},
    {"n": 2, "company": "Beta", "title": "AI Engineer", "url": Q2, "resume": "ai-engineer", "spare": False},
    {"n": 3, "company": "Gamma", "title": "Data Analyst", "url": Q3, "resume": "data-analyst", "spare": False},
    {"n": 4, "company": "Delta", "title": "Data Scientist", "url": Q4, "resume": "data-scientist", "spare": True}]}
FACTS = {
    "contact": {"name": "Sam Example", "lines": ["Boston, MA"], "links": [], "status": "approved"},
    "titles": {"items": ["Data Engineer"], "status": "approved"},
    "summaries": [{"id": "s1", "variant": "data-engineer", "text": "Data engineer who builds ETL pipelines.", "status": "approved"}],
    "education": [{"school": "State University", "degree": "M.S. Data Science", "date": "2025", "status": "approved"}],
    "skills": [{"id": "sk", "label": "Data", "items": ["ETL", "Airflow", "SQL"], "status": "approved"}],
    "roles": [{"id": "r1", "title": "Data Engineer", "org": "Acme", "place": "", "dates": "2023 – 2024", "status": "approved",
               "tools": ["Python", "SQL", "Airflow", "Spark"], "bullets": [
                   {"id": "r1a", "text": "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows.", "status": "approved"},
                   {"id": "r1b", "text": "Built Tableau dashboards for 500 users.", "status": "approved"}]}],
    "projects": [],
    "variants": {"data-engineer": ["r1a"]},
}
ALLOWED = {"shortlist", "shortlist.classify", "shortlist.roles", "shortlist.claude"}  # what Tailor may use of Shortlist
NOT_FOUND = "That job isn't in Shortlist's latest results. Paste it under Tailor a job."
NO_TEXT = "The job description for this posting isn't saved. Paste it under Tailor a job."
LINK_NOTE = "That job isn't in Shortlist's latest results. Paste it below."


def write_csv(path: Path, fields: list[str], rows: list[list]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        w.writerows(rows)


def moved(path: Path, before: int | None) -> None:
    """After a write: on a coarse clock the mtime may not have moved, so move it on as a later write would."""
    if jobs.mtime(path) == before:
        later = (before or 0) + 10**9
        os.utime(path, ns=(later, later))


def run_briefly(fn, timeout: float = 10):
    """Run fn in a thread: (whether it returned within `timeout` seconds, what it raised)."""
    box = {}

    def go():
        try:
            fn()
        except BaseException as exc:  # SystemExit included
            box["exc"] = exc
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout)
    return not t.is_alive(), box.get("exc")


def wait_for(check, timeout: float = 3) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.01)
    return check()


class ShortlistFiles(unittest.TestCase):
    """Shortlist's files in a temp folder, with tailor.jobs pointed at them: a queue of four (one applied to, one
    skipped, and a spare whose description isn't saved), postings.csv with one job more, and the job boards. No test
    here reads your real files, and none starts a learn."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "today.json").write_text(json.dumps(QUEUE))
        write_csv(self.tmp / "postings.csv", ["score", "company", "title", "url"],
                  [[9, i["company"], i["title"], i["url"]] for i in QUEUE["items"]] + [[8, "Epsilon", f"  {ML.replace(' ', '   ')} - ", P5]])
        write_csv(self.tmp / "applications.csv", ["applied_on", "company", "title", "url"], [["2026-10-29", "Beta", "AI Engineer", Q2]])
        write_csv(self.tmp / "skipped.csv", ["skipped_on", "company", "title", "url", "reason"],
                  [["2026-10-29", "Gamma", "Data Analyst", Q3, "closed"]])
        self.boards = self.tmp / "boards"
        self.boards.mkdir()
        (self.boards / "greenhouse__acme.json").write_text(json.dumps([{"url": Q1, "title": "Data Engineer", "text": JD}]))
        (self.boards / "greenhouse__epsilon.json").write_text(json.dumps([{"url": P5, "title": ML, "text": JD}]))
        (self.boards / "ashby__delta.json").write_text(json.dumps([{"url": Q4, "title": "Data Scientist", "text": "Apply now."}]))
        (self.tmp / "results").mkdir()
        (self.tmp / "facts.json").write_text(json.dumps(FACTS))
        self.profile = {"resume_facts": str(self.tmp / "facts.json")}

        def no_learn(profile):
            raise AssertionError("a test reached the real learn, which writes data/lexicon.json")
        for p in (mock.patch.object(jobs, "QUEUE", self.tmp / "today.json"),
                  mock.patch.object(jobs, "LISTS", self.tmp / "lists"),  # none saved here
                  mock.patch.object(jobs, "POSTINGS", self.tmp / "postings.csv"),
                  mock.patch.object(jobs, "APPS", self.tmp / "applications.csv"),
                  mock.patch.object(jobs, "SKIPS", self.tmp / "skipped.csv"),
                  mock.patch.object(jobs, "BOARDS", self.boards),
                  mock.patch.object(app, "RESULTS", self.tmp / "results"),
                  mock.patch.object(app, "relearn_if_stale", mock.Mock(return_value=None)),
                  mock.patch.object(lexicon, "PATH", self.tmp / "lexicon.json"),
                  mock.patch.object(lexicon, "update", no_learn),
                  mock.patch.dict(app._DEMAND, {"mtime": None, "counts": {}})):
            p.start()
            self.addCleanup(p.stop)


class JobsTest(ShortlistFiles):
    """tailor.jobs reads Shortlist's files and never needs them."""

    def test_find_looks_in_todays_queue_then_in_every_scored_posting(self):
        self.assertEqual(jobs.find(Q1), {"url": Q1, "company": "Acme", "title": "Data Engineer", "n": 1, "resume": "data-engineer"})
        # scored but not in today's queue: no number and no resume version yet, titled as the queue titles them
        self.assertEqual(jobs.find(P5), {"url": P5, "company": "Epsilon", "title": ML, "n": 0, "resume": None})
        for unknown in ("https://example.com/unknown", Q1 + "x", "", None):
            self.assertIsNone(jobs.find(unknown))

    def test_open_jobs_drop_what_you_applied_to_or_skipped(self):
        self.assertEqual(jobs.gone(), {Q2, Q3})
        got = jobs.open_jobs()
        self.assertEqual([j["url"] for j in got], [Q1, Q4])  # in queue order
        self.assertEqual(got[1], {"url": Q4, "n": 4, "company": "Delta", "title": "Data Scientist", "resume": "data-scientist",
                                  "spare": True, "has_text": False})  # its description isn't saved
        self.assertTrue(got[0]["has_text"])

    def test_posting_text_sees_a_board_file_written_after_the_first_lookup(self):
        self.assertEqual(jobs.posting_text(Q1), JD)
        late = "https://jobs.lever.co/zeta/66"
        self.assertEqual(jobs.posting_text(late), "")
        before = jobs.mtime(self.boards)
        board = self.boards / "lever__zeta.json"
        part = board.with_name(board.name + ".tmp")
        part.write_text(json.dumps([{"url": late, "text": JD + "Zeta."}]))
        part.replace(board)  # as Shortlist's Refresh writes one: whole, then renamed into place
        moved(self.boards, before)
        self.assertEqual(jobs.posting_text(late), JD + "Zeta.")  # Tailor kept running meanwhile
        self.assertEqual(jobs.posting_text(Q1), JD)
        self.assertEqual(jobs.posting_text(""), "")

    def test_missing_or_broken_files_read_as_empty(self):
        before = jobs.mtime(self.boards)
        (self.boards / "broken.json").write_text("[{")
        (self.boards / "odd.json").write_text('{"url": "not a list"}')
        moved(self.boards, before)
        self.assertEqual(jobs.posting_text(Q1), JD)  # the other boards still read
        (self.tmp / "today.json").write_text("{half a queue")
        self.assertEqual((jobs.queue(), jobs.open_jobs()), ({"items": []}, []))
        self.assertEqual(jobs.find(Q1)["n"], 0)  # postings.csv still knows it
        gone = self.tmp / "nothing"
        with mock.patch.object(jobs, "QUEUE", gone / "today.json"), mock.patch.object(jobs, "POSTINGS", gone / "postings.csv"), \
                mock.patch.object(jobs, "APPS", gone / "applications.csv"), mock.patch.object(jobs, "SKIPS", gone / "skipped.csv"), \
                mock.patch.object(jobs, "BOARDS", gone / "boards"), mock.patch.object(jobs, "LISTS", gone / "lists"):
            self.assertEqual((jobs.queue(), jobs.open_jobs(), jobs.gone(), jobs.find(Q1)), ({"items": []}, [], set(), None))
            self.assertEqual((jobs.posting_text(Q1), jobs.stamp()), ("", (None, None, None)))

    def test_stamp_moves_when_shortlist_writes_a_file(self):
        first = jobs.stamp()
        self.assertEqual(len(first), 3)
        self.assertTrue(all(first))
        before = jobs.mtime(self.tmp / "skipped.csv")
        write_csv(self.tmp / "skipped.csv", ["skipped_on", "company", "title", "url", "reason"], [])  # Undo on Shortlist's page
        moved(self.tmp / "skipped.csv", before)
        self.assertNotEqual(jobs.stamp(), first)
        self.assertEqual(jobs.gone(), {Q2})


class JobOfTest(ShortlistFiles):
    """A preview's job: by its posting URL from Shortlist's files, or pasted."""

    def test_a_job_from_shortlist_or_why_not(self):
        self.assertEqual(app._job_of({"url": Q1}), ("Acme", "Data Engineer", 1, "data-engineer", JD))
        self.assertEqual(app._job_of({"url": P5}), ("Epsilon", ML, 0, "data-scientist", JD))  # its title picks the version
        for d in ({"url": "https://example.com/unknown"}, {"url": "javascript:alert(1)"}, {"url": ["x"]}, {"n": 1}, {}):
            with self.assertRaises(ValueError) as e:
                app._job_of(d)
            self.assertEqual(str(e.exception), NOT_FOUND)
        with self.assertRaises(ValueError) as e:
            app._job_of({"url": Q4})
        self.assertEqual(str(e.exception), NO_TEXT)

    def test_a_link_is_looked_up_never_fetched(self):
        with mock.patch.object(jobs, "find", wraps=jobs.find) as find:
            for bad in ("javascript:alert(1)", "file:///etc/passwd", "https://example.com/" + "a" * 2000):
                with self.assertRaises(ValueError):
                    app._job_of({"url": bad})
            find.assert_not_called()

    def test_a_pasted_job(self):
        self.assertEqual(app._job_of({"paste": {"company": "Beta", "title": "Data Engineer", "jd": JD}}),
                         ("Beta", "Data Engineer", 0, "data-engineer", JD))
        for paste in ({"company": "Beta", "title": "Data Engineer", "jd": "Too short."}, "not a job"):
            with self.assertRaises(ValueError) as e:
                app._job_of({"paste": paste})
            self.assertEqual(str(e.exception), "Add the company, the job title and the whole job description.")

    def test_skill_demand_counts_todays_jobs_and_follows_a_new_queue(self):
        demand = lambda: {d["term"]: d["n"] for d in app.skill_demand(json.loads(json.dumps(FACTS)))}
        self.assertEqual(demand()["airflow"], 1)  # the one description saved among today's jobs
        before = jobs.mtime(self.tmp / "today.json")
        more = dict(QUEUE, items=QUEUE["items"] + [{"n": 5, "company": "Epsilon", "title": ML, "url": P5, "resume": "data-scientist"}])
        (self.tmp / "today.json").write_text(json.dumps(more))  # Shortlist picked a new queue
        moved(self.tmp / "today.json", before)
        self.assertEqual(demand()["airflow"], 2)
        with mock.patch.object(jobs, "QUEUE", self.tmp / "none.json"):
            self.assertEqual(app.skill_demand(FACTS), [])


class ServerTest(ShortlistFiles):
    """The page server: 127.0.0.1 only, under its own Host, with the run's token and same-origin writes."""

    def setUp(self):
        super().setUp()
        self.seen = []  # every data dict the page was built from
        real = page.html
        p = mock.patch.object(page, "html", lambda data: self.seen.append(data) or real(data))
        p.start()
        self.addCleanup(p.stop)
        self.httpd, self.url, self.token = app.make_server(self.profile, 0)
        self.port = self.httpd.server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def ask(self, method, path, body=None, host=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers={"Host": self.host if host is None else host, **(headers or {})})
            resp = conn.getresponse()
            return resp.status, resp.headers, resp.read()
        finally:
            conn.close()

    def post(self, path, data=None, **headers):
        """A write as the page sends it. With no data the body is empty: a refused write is answered before its body
        is read, and a body left unread makes the connection reset before the answer can be read."""
        return self.ask("POST", path, b"" if data is None else json.dumps(data).encode(),
                        headers={"Content-Type": "application/json", "X-Token": self.token, **headers})

    def sized(self, length: str):
        """A POST that says it carries `length` bytes and sends none of them."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", "/api/resume/preview", skip_host=True)
            for k, v in (("Host", self.host), ("X-Token", self.token), ("Content-Length", length)):
                conn.putheader(k, v)
            conn.endheaders()
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read())
        finally:
            conn.close()

    def test_the_page_answers_only_under_its_own_host(self):
        status, headers, body = self.ask("GET", "/")
        self.assertEqual((status, headers["Content-Type"]), (200, "text/html; charset=utf-8"))
        self.assertTrue(headers["Server"].startswith("tailor"))
        for name, value in (("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer")):
            self.assertEqual(headers[name], value)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn(self.token, body.decode())  # the page carries the run's token for its writes
        data = self.seen[-1]
        self.assertEqual((data["token"], data["facts"], data["job"], data["job_note"]), (self.token, True, None, ""))
        self.assertEqual((data["shortlist_url"], data["warm"]), ("http://127.0.0.1:8765/", app.WARM["note"]))
        self.assertEqual([j["url"] for j in data["jobs"]], [Q1, Q4])  # applied to and skipped are gone
        self.assertEqual(set(data["jobs"][0]), {"url", "n", "company", "title", "resume", "spare", "has_text"})
        self.assertEqual(self.ask("GET", "/", host=f"localhost:{self.port}")[0], 200)
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}", ""):
            self.assertEqual(self.ask("GET", "/", host=host)[0], 403, host)  # a rebound DNS name gets nothing

    def test_writes_need_the_token_the_host_and_the_same_origin(self):
        status, _, body = self.post("/api/facts/get")
        self.assertEqual((status, json.loads(body)["roles"][0]["id"]), (200, "r1"))
        for origin in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
            self.assertEqual(self.post("/api/facts/get", Origin=origin)[0], 200)
        status, _, body = self.ask("POST", "/api/facts/get", b"", headers={"Content-Type": "application/json"})
        self.assertEqual((status, json.loads(body)), (403, {"error": "forbidden"}))  # no token
        for token in ("wrong", self.token + "x", ""):
            self.assertEqual(self.post("/api/facts/get", **{"X-Token": token})[0], 403)
        for origin in ("http://evil.example", "null", f"https://127.0.0.1:{self.port}"):
            self.assertEqual(self.post("/api/facts/get", Origin=origin)[0], 403)
        self.assertEqual(self.ask("POST", "/api/facts/get", b"", host="evil.example", headers={"X-Token": self.token})[0], 403)

    def test_a_body_over_200000_bytes_is_refused_unread(self):
        self.assertEqual(self.sized("200001"), (413, {"error": "too large"}))
        for nonsense in ("-1", "lots"):  # never read to the end of a body of no stated size
            self.assertEqual(self.sized(nonsense), (400, {"error": "bad request"}))
        whole = b'{"pad": "' + b"x" * (200_000 - 11) + b'"}'
        self.assertEqual(len(whole), 200_000)
        self.assertEqual(self.ask("POST", "/api/facts/get", whole, headers={"X-Token": self.token})[0], 200)

    def test_unknown_paths_are_not_found(self):
        for path in ("/nope", "/api/state", "/index.html"):
            self.assertEqual(self.ask("GET", path)[0], 404, path)
        for path in ("/api/nope", "/api/applied", "/api/refresh", "/api/linkedin/check"):  # Shortlist's are not Tailor's
            self.assertEqual(self.post(path, {"url": Q1})[0], 404, path)

    def test_a_job_link_opens_that_job_from_shortlists_files(self):
        def data(path):
            self.assertEqual(self.ask("GET", path)[0], 200)
            return self.seen[-1]
        link = lambda url: "/?job=" + quote(url, safe="")
        d = data(link(Q1))
        self.assertEqual((d["job"], d["job_note"]),
                         ({"url": Q1, "company": "Acme", "title": "Data Engineer", "n": 1, "resume": "data-engineer", "has_text": True}, ""))
        self.assertEqual(data(link(P5))["job"],  # scored by Shortlist, not in today's queue
                         {"url": P5, "company": "Epsilon", "title": ML, "n": 0, "resume": "data-scientist", "has_text": True})
        self.assertFalse(data(link(Q4))["job"]["has_text"])  # in the queue, but its description isn't saved
        self.assertEqual(data(f"{link(Q1)}&job={quote(P5, safe='')}")["job"]["url"], Q1)  # the first one only
        for path in (link("https://example.com/not-in-shortlist"), "/?job=javascript:alert(1)", link("javascript:alert(1)"),
                     link("https://example.com/" + "a" * 2000), "/?job="):
            d = data(path)
            self.assertEqual((d["job"], d["job_note"]), (None, LINK_NOTE), path)
        with mock.patch.object(jobs, "find", wraps=jobs.find) as find:
            data("/?job=javascript:alert(1)")
            find.assert_not_called()  # looked up only when it's a web address, and only in Shortlist's files

    def test_the_link_back_to_shortlist(self):
        for given, url in ((None, "http://127.0.0.1:8765/"), (8800, "http://127.0.0.1:8800/"), ("8801", "http://127.0.0.1:8801/"),
                           ("oops", "http://127.0.0.1:8765/")):
            self.assertEqual(app.page_data({"shortlist_port": given}, "t")["shortlist_url"], url, given)

    def test_a_queue_job_previews_and_saves_under_its_number(self):
        with mock.patch.object(engine, "_print_pdf", lambda html, wd, name, chrome: self.tmp / name), \
                mock.patch.object(engine, "_pages", lambda pdf: 1):  # no Chrome
            status, _, body = self.post("/api/resume/preview", {"url": Q1, "rewrite": False})
        r = json.loads(body)
        self.assertEqual((status, r["ok"], r["rewrite_note"]), (200, True, ""))
        app.relearn_if_stale.assert_called_with(self.profile)  # each preview looks for newly read boards
        Path(app.PREVIEWS[r["id"]]["pdf"]).write_bytes(b"%PDF-1.4")
        self.profile["tailored_dir"] = str(self.tmp / "Documents")
        status, _, body = self.post("/api/resume/save", {"id": r["id"]})
        self.assertEqual((status, Path(json.loads(body)["path"]).parent.name), (200, "01 Acme - Data Engineer"))

    def test_a_preview_that_cannot_be_made_says_why(self):
        for d, why in (({"url": "https://example.com/not-in-shortlist"}, NOT_FOUND), ({"url": Q4}, NO_TEXT),
                       ({"paste": {"company": "Beta", "title": "Data Engineer", "jd": "Too short."}},
                        "Add the company, the job title and the whole job description.")):
            status, _, body = self.post("/api/resume/preview", d)
            self.assertEqual((status, json.loads(body)), (400, {"error": why}))
        for bad in (b"[]", b"not json", b'"text"'):
            self.assertEqual(self.ask("POST", "/api/facts/update", bad, headers={"X-Token": self.token})[0], 400, bad)


class ServeTest(ShortlistFiles):
    """serve() keeps to its one port, since Shortlist's cards link there."""

    def held(self, httpd):
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def test_a_tailor_already_running_is_opened_not_started_again(self):
        port = self.held(app.make_server(self.profile, 0)[0])
        out = io.StringIO()
        with mock.patch.object(app.webbrowser, "open") as browser, mock.patch.object(app, "warm_rewrites") as warm, \
                mock.patch.object(app, "watch_shortlist") as watch, contextlib.redirect_stdout(out):
            done, exc = run_briefly(lambda: app.serve(self.profile, port=port, open_browser=True))
        self.assertEqual((done, exc), (True, None))  # returned: no second server
        self.assertIn(f"Tailor is already running at http://127.0.0.1:{port}/", out.getvalue())
        browser.assert_called_once_with(f"http://127.0.0.1:{port}/")
        for started in (warm, watch, app.relearn_if_stale):
            started.assert_not_called()

    def test_another_program_on_the_port_stops_tailor(self):
        class Other(BaseHTTPRequestHandler):
            server_version = "other"

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"hello")

            def log_message(self, *a):
                pass
        port = self.held(HTTPServer(("127.0.0.1", 0), Other))
        done, exc = run_briefly(lambda: app.serve(self.profile, port=port, open_browser=False))
        self.assertTrue(done)
        self.assertIsInstance(exc, SystemExit)
        self.assertEqual(str(exc), f"Port {port} is in use by another program. Start Tailor with --port.")

    def test_a_program_that_never_answers_stops_tailor_too(self):
        quiet = socket.socket()
        self.addCleanup(quiet.close)
        quiet.bind(("127.0.0.1", 0))
        quiet.listen(1)
        port = quiet.getsockname()[1]
        with mock.patch.object(app, "PROBE_S", 0.3):
            done, exc = run_briefly(lambda: app.serve(self.profile, port=port, open_browser=False))
        self.assertTrue(done)
        self.assertEqual(str(exc), f"Port {port} is in use by another program. Start Tailor with --port.")

    def test_the_background_work_starts_only_with_a_facts_file(self):
        self.addCleanup(app._WARM_KICK.clear)
        for profile, started in ((self.profile, True), ({}, False)):
            fake = mock.Mock()
            fake.serve_forever.side_effect = KeyboardInterrupt  # Ctrl+C right away
            out = io.StringIO()
            app.relearn_if_stale.reset_mock()
            with mock.patch.object(app, "make_server", return_value=(fake, "http://127.0.0.1:8766/", "t")), \
                    mock.patch.object(app, "warm_rewrites") as warm, mock.patch.object(app, "watch_shortlist") as watch, \
                    contextlib.redirect_stdout(out):
                app.serve(profile, port=8766, open_browser=False)
                self.assertEqual(wait_for(lambda: warm.called and watch.called, 3 if started else 0.2), started)
            self.assertEqual(app.relearn_if_stale.called, started)
            self.assertIn("Tailor: http://127.0.0.1:8766/  (Ctrl+C to stop; nothing is saved until you press Save PDF)", out.getvalue())
            self.assertIn("stopped", out.getvalue())
            fake.server_close.assert_called_once()


class WatcherTest(ShortlistFiles):
    """Shortlist doesn't tell Tailor when you apply or skip, so Tailor looks, and kicks the warm-up."""

    def test_a_change_in_shortlists_files_kicks_the_warmup(self):
        app._WARM_KICK.clear()
        self.addCleanup(app._WARM_KICK.clear)
        stop = threading.Event()
        t = threading.Thread(target=app.watch_shortlist, args=(stop, 0.02), daemon=True)
        t.start()
        self.addCleanup(t.join, 2)
        self.addCleanup(stop.set)
        time.sleep(0.2)
        self.assertFalse(app._WARM_KICK.is_set())  # nothing changed, nothing to do
        before = jobs.mtime(self.tmp / "applications.csv")
        write_csv(self.tmp / "applications.csv", ["applied_on", "company", "title", "url"],  # Mark applied on Shortlist's page
                  [["2026-10-29", "Beta", "AI Engineer", Q2], ["2026-10-29", "Acme", "Data Engineer", Q1]])
        moved(self.tmp / "applications.csv", before)
        self.assertTrue(app._WARM_KICK.wait(3))


class RelearnTest(unittest.TestCase):
    """Tailor learns the tools postings name once Shortlist has read new boards: in the background, one at a time."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.boards = self.tmp / "boards"
        self.boards.mkdir()
        self.lex = self.tmp / "lexicon.json"  # never data/lexicon.json
        self.learned, self.gate, self.fail = [], None, None
        self.result = {"terms": {"kubeflow": {"label": "Kubeflow", "pattern": "Kubeflow", "category": "ml"}}}

        def update(profile):  # stands in for lexicon.update, which reads every board and writes data/lexicon.json
            self.learned.append(profile)
            if self.gate:
                self.gate.wait(5)
            if self.fail:
                raise self.fail
            return self.result
        self.use = mock.Mock()
        for p in (mock.patch.object(jobs, "BOARDS", self.boards), mock.patch.object(lexicon, "PATH", self.lex),
                  mock.patch.object(lexicon, "update", update), mock.patch.object(engine, "use_lexicon", self.use),
                  mock.patch.dict(app._LEARN, {"boards": None})):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def at(path: Path, seconds: int) -> None:
        os.utime(path, (seconds, seconds))

    def learn(self, profile=None):
        t = app.relearn_if_stale({} if profile is None else profile)
        if t:
            t.join(5)
        return t

    def test_learns_only_when_the_boards_are_newer_than_the_last_learn(self):
        self.at(self.boards, 2_000_000)
        self.assertIsNotNone(self.learn())  # never learned here: learn now
        self.assertEqual(len(self.learned), 1)
        self.use.assert_called_once_with(self.result)  # and the engine uses what was learned
        self.lex.write_text("{}")
        self.at(self.lex, 3_000_000)
        self.assertIsNone(self.learn())  # the last learn is newer than the boards
        self.at(self.boards, 4_000_000)  # Shortlist read the boards again
        self.assertIsNotNone(self.learn())
        self.at(self.boards, 5_000_000)
        self.assertIsNone(self.learn({"learn_keywords": False}))  # turned off
        shutil.rmtree(self.boards)
        self.assertIsNone(self.learn())  # no boards: nothing to learn from
        self.assertEqual(len(self.learned), 2)

    def test_one_learn_at_a_time_and_a_failure_is_one_line(self):
        self.gate = threading.Event()
        self.at(self.boards, 2_000_000)
        first = app.relearn_if_stale({})
        self.assertIsNotNone(first)  # returned while the learn runs
        self.at(self.boards, 3_000_000)
        self.assertIsNone(app.relearn_if_stale({}))  # newer boards, but one learn at a time
        self.gate.set()
        first.join(5)
        self.gate, self.fail = None, RuntimeError("no boards today")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNotNone(self.learn())  # the boards from 3,000,000 get their learn now
        self.assertEqual(err.getvalue(), "  ! learning tools from the postings: RuntimeError: no boards today\n")
        self.assertEqual(self.use.call_count, 1)  # a failed learn leaves the tools learned before in use
        self.assertIsNone(self.learn())  # and isn't retried for the same boards
        self.fail = None
        self.at(self.boards, 4_000_000)
        self.assertIsNotNone(self.learn())  # the failure let go of the lock
        self.assertEqual(len(self.learned), 3)


class CliTest(ShortlistFiles):
    """python -m tailor: the page by default, `all` and `lexicon` as Shortlist had them."""

    def test_load_profile_falls_back_to_the_example_and_sets_up_the_engine(self):
        root = self.tmp / "repo"
        root.mkdir()
        (root / "profile.example.yaml").write_text("resume_never_words: [contract]\nlearn_keywords: false\n")
        err = io.StringIO()
        with mock.patch.object(cli, "ROOT", root), mock.patch.object(engine, "never_words") as never, \
                mock.patch.object(engine, "use_lexicon") as use, contextlib.redirect_stderr(err):
            profile = cli.load_profile()
        self.assertEqual(err.getvalue(), "profile.yaml not found; using profile.example.yaml\n")
        self.assertEqual(profile["roles"], list(ROLE_BY_KEY))
        never.assert_called_once_with(["contract"])
        use.assert_called_once_with(None)  # learn_keywords: false
        (root / "profile.yaml").write_text("roles: [data_engineer]\n")
        with mock.patch.object(cli, "ROOT", root), mock.patch.object(engine, "never_words"), \
                mock.patch.object(engine, "use_lexicon") as use, mock.patch.object(lexicon, "load", return_value={"terms": {}}):
            self.assertEqual(cli.load_profile()["roles"], ["data_engineer"])
        use.assert_called_once_with({"terms": {}})

    def test_no_command_opens_the_page_on_its_one_port(self):
        with mock.patch.object(cli, "load_profile", return_value={}), mock.patch.object(app, "serve") as serve:
            cli.main([])
            serve.assert_called_with({}, port=8766, open_browser=True)
            cli.main(["--no-browser", "--port", "8770"])
            serve.assert_called_with({}, port=8770, open_browser=False)
        # tailor_port read as Shortlist reads it for its cards' links, so the links and the page agree
        for given, port, noted in ((8780, 8780, False), ("8781", 8781, False), ("not a port", 8766, True), (70000, 8766, True)):
            profile, err = {"tailor_port": given}, io.StringIO()
            with mock.patch.object(cli, "load_profile", return_value=profile), mock.patch.object(app, "serve") as serve, \
                    contextlib.redirect_stderr(err):
                cli.main(["serve"])
            serve.assert_called_with(profile, port=port, open_browser=True)
            self.assertEqual("isn't a port" in err.getvalue(), noted, given)
        for argv in (["serve", "--port", "70000"], ["serve", "--port", "x"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.main(argv)

    def test_all_builds_a_resume_for_each_job_in_shortlists_queue(self):
        out = io.StringIO()
        with mock.patch.object(cli, "load_profile", return_value={"tailored_dir": str(self.tmp / "Documents")}), \
                mock.patch.object(app, "tailor_all", side_effect=lambda profile, items: [f"{i['n']}.pdf" for i in items]) as made, \
                contextlib.redirect_stdout(out):
            cli.main(["all"])
            self.assertEqual([i["n"] for i in made.call_args[0][1]], [1, 2, 3])  # the whole queue but its spares, as before
            cli.main(["all", "--spares"])
            self.assertEqual([i["n"] for i in made.call_args[0][1]], [1, 2, 3, 4])
        self.assertIn(f"built 3 tailored resumes in {self.tmp / 'Documents'}", out.getvalue())

    def test_lexicon_learns_and_lists_the_tools(self):
        lex = {"terms": {"kubeflow": {"label": "Kubeflow", "category": "ml", "postings": 7, "employers": 4}},
               "reads": 3, "postings": 1200, "skipped": {}}
        out = io.StringIO()
        with mock.patch.object(cli, "load_profile", return_value={}), mock.patch.object(lexicon, "load", return_value=None), \
                mock.patch.object(lexicon, "update", return_value=lex), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["lexicon"]), 0)
        self.assertIn("1 tools learned in all, 1 new this time, from 3 reads of postings and 1,200 postings", out.getvalue())
        self.assertIn("Kubeflow", out.getvalue())


class DependencyTest(unittest.TestCase):
    """Two tools: Tailor uses only Shortlist's shared pieces, and Shortlist never uses Tailor."""

    def test_tailor_loads_only_shortlists_shared_modules(self):
        code = ("import json, sys, tailor.app, tailor.__main__; "
                "print(json.dumps(sorted(m for m in sys.modules if m == 'shortlist' or m.startswith('shortlist.'))))")
        run = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        loaded = set(json.loads(run.stdout.strip().splitlines()[-1]))
        self.assertLessEqual(loaded, ALLOWED)
        self.assertTrue({"shortlist.claude", "shortlist.classify"} <= loaded)  # the check would catch them

    def test_no_import_crosses_the_line_even_inside_a_function(self):
        def imported(path: Path, top: str) -> set[str]:
            out = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    out |= {a.name for a in node.names if a.name.split(".")[0] == top}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == top:
                    out |= {f"{top}.{a.name}" for a in node.names} if node.module == top else {node.module}
            return out
        for path in sorted((ROOT / "tailor").glob("*.py")):
            self.assertLessEqual(imported(path, "shortlist"), ALLOWED, path.name)
        for path in sorted((ROOT / "shortlist").glob("*.py")):
            self.assertEqual(imported(path, "tailor"), set(), path.name)


if __name__ == "__main__":
    unittest.main()
