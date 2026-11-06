import ast
import http.client
import io
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from shortlist import apply, startups
from shortlist.apply import add_business_days, band, field_of, pick, title_key
from shortlist.paths import ROOT


def posting(company, title, score, age, url=None, excluded=""):
    return {"company": company, "title": title, "score": str(score), "age_days": str(age),
            "url": url or f"https://example.com/{company}/{title_key(title).replace(' ', '-')}/{age}",
            "excluded": excluded, "role": "data_engineer"}


def young(*names):
    """Founding facts that make each name a startup (founded recently, few people)."""
    return lambda: {n: {"founded": 2023, "employees": 50, "source": "test"} for n in names}


class PickTest(unittest.TestCase):
    today = date(2026, 10, 12)

    def test_score_weighted_by_freshness(self):
        rows = [posting("A", "Data Engineer", 40, 20), posting("B", "Data Engineer", 10, 1),
                posting("C", "Data Engineer", 30, 2)]  # weighted: A 24, B 20, C 60
        self.assertEqual([r["company"] for r in pick(rows, [], [], today=self.today)], ["C", "A", "B"])

    def test_senior_titles_the_classifier_misses(self):
        rows = [posting("A", "Leader, Solutions Engineer", 30, 1), posting("B", "Principle Robotics Software Engineer", 30, 1),
                posting("C", "Systems Analyst Advisor", 30, 1), posting("D", "Data Engineer 5", 30, 1),
                posting("E", "Data Engineer 1", 30, 1)]
        self.assertEqual([r["company"] for r in pick(rows, [], [], today=self.today)], ["E"])

    def test_age_offset_and_title_exclusions(self):
        rows = [posting("A", "Data Engineer", 30, 59), posting("B", "IT Systems Engineer, macOS", 30, 1),
                posting("C", "Software Engineer, Agent (Spanish speaking)", 30, 1)]
        self.assertEqual(pick(rows, [], [], today=self.today, age_offset=2), [])

    def test_cap_per_employer_counts_recent_applications(self):
        rows = [posting("A", f"Data Engineer {i}", 50 - i, 1) for i in range(5)]
        applied = [{"company": "A", "title": "Old role", "url": "u", "applied_on": "2026-10-01"}]
        self.assertEqual(len(pick(rows, applied, [], cap=3, today=self.today)), 2)

    def test_one_per_title_across_locations(self):
        rows = [posting("A", "Software Engineer, Early Career (NYC)", 30, 1),
                posting("A", "Software Engineer, Early Career (SF)", 29, 1)]
        self.assertEqual(len(pick(rows, [], [], today=self.today)), 1)

    def test_skips_excluded_applied_skipped_and_stale(self):
        done = posting("A", "Data Engineer", 30, 1)
        rows = [done, posting("B", "Data Engineer", 30, 1, excluded="senior title"),
                posting("C", "Data Engineer", 30, 90), posting("D", "Data Engineer", 30, 1)]
        skipped = [{"url": rows[3]["url"]}]
        applied = [{"company": "A", "title": "Data Engineer", "url": done["url"], "applied_on": "2026-10-12"}]
        self.assertEqual(pick(rows, applied, skipped, today=self.today), [])

    def test_startups_come_first_then_established_employers(self):
        rows = [posting("Bigco", "Data Engineer", 60, 1), posting("Seed", "Data Engineer", 20, 1),
                posting("Ent", "Data Engineer", 50, 1, url="https://ent.wd1.myworkdayjobs.com/jobs/job/1"),
                posting("Series B", "Data Engineer", 30, 1)]
        with mock.patch.object(startups, "facts", young("Seed", "Series B")), \
                mock.patch.object(startups, "established", lambda: {"Bigco"}):
            self.assertEqual([r["company"] for r in pick(rows, [], [], today=self.today)], ["Series B", "Seed", "Bigco", "Ent"])
            self.assertEqual([r["company"] for r in pick(rows, [], [], n=2, today=self.today)], ["Series B", "Seed"])

    def test_workday_pages_on_myworkdaysite_count_as_workday(self):
        url = "https://wd5.myworkdaysite.com/recruiting/guidewire/external/job/United-States/AI-Engineer_JR1"
        self.assertEqual(apply.board_of(url), "Workday")
        rows = [posting("Guidewire", "AI Engineer", 50, 1, url=url), posting("Seed", "Data Engineer", 20, 1)]
        with mock.patch.object(startups, "facts", young("Seed")), mock.patch.object(startups, "established", set):
            self.assertEqual([r["company"] for r in pick(rows, [], [], today=self.today)], ["Seed", "Guidewire"])


class HelpersTest(unittest.TestCase):
    def test_business_days_skip_weekends(self):
        self.assertEqual(add_business_days(date(2026, 10, 9), 4), date(2026, 10, 15))  # Fri + 4 = Thu

    def test_bands(self):
        self.assertEqual([band(a) for a in (0, 3, 4, 14, 15, 30, 31, 60, 61)], [0, 0, 1, 1, 2, 2, 3, 3, 4])

    def test_title_key_ignores_parentheses_and_punctuation(self):
        self.assertEqual(title_key("Software Engineer, New Grad (2027)"), "software engineer new grad")


class FieldTest(unittest.TestCase):
    def test_fields(self):
        f = field_of
        with mock.patch.object(startups, "facts", young("Cursor", "Ambience Healthcare Inc")), \
                mock.patch.object(startups, "established", lambda: {"Acme", "Robinhood", "Okta"}):
            self.assertEqual(f("Cursor", "Software Engineer", "", "Ashby"), "startup")
            self.assertEqual(f("Ambience Healthcare Inc", "Data Engineer", "patients clinical HIPAA patients", "Ashby"), "startup")
            self.assertEqual(f("Okta", "Software Engineer", "", "Greenhouse"), "big")
            self.assertEqual(f("Intel", "Software Engineer", "", "Workday"), "big")
            self.assertEqual(f("University of Chicago", "Business Analyst", "", "Workday"), "big")  # no cap-exempt group
            self.assertEqual(f("Elevance Health", "Business Analyst", "", "Workday"), "healthcare")
            self.assertEqual(f("Acme", "Data Analyst", "patients clinical HIPAA patients clinical", "Greenhouse"), "healthcare")
            self.assertEqual(f("Robinhood", "Software Engineer", "", "Greenhouse"), "finance")

    def test_the_established_list_is_plain_unique_names(self):
        import yaml
        listed = yaml.safe_load(startups.ESTABLISHED_FILE.read_text())
        self.assertTrue(all(isinstance(n, str) and n == n.strip() for n in listed))
        self.assertEqual(len(listed), len(set(listed)))
        self.assertTrue({"HubSpot", "Toast", "Wayfair"} <= set(listed))
        self.assertFalse({"Anthropic", "Cursor", "OpenAI"} & set(listed))


class ExcludeTest(unittest.TestCase):
    def test_lab_science_and_business_development_titles(self):
        import re
        from shortlist.apply import EXCLUDE_TITLES
        rx = re.compile("|".join(f"(?:{p})" for p in EXCLUDE_TITLES), re.I)
        for title in ("Research Scientist I/II: Pharmacology", "Market Consultant BD", "Business Development Analyst"):
            self.assertTrue(rx.search(title), title)
        for title in ("Bioinformatics / Omics Data Expert", "Data Engineer", "Analytics Engineer"):
            self.assertFalse(rx.search(title), title)


class FreshTest(unittest.TestCase):
    def test_hours_old_and_the_24_hour_window(self):
        from datetime import datetime, timedelta, timezone
        from shortlist.apply import hours_old
        now = datetime.now(timezone.utc)
        fresh = posting("A", "Data Engineer", 40, 0) | {"posted_at": (now - timedelta(hours=5)).isoformat()}
        old = posting("B", "Data Engineer", 40, 1) | {"posted_at": (now - timedelta(hours=30)).isoformat()}
        undated = posting("C", "Data Engineer", 40, 0)
        self.assertLess(abs(hours_old(fresh) - 5), 0.1)
        picked = [r["company"] for r in pick([fresh, old, undated], [], [], max_age_hours=24)]
        self.assertEqual(sorted(picked), ["A", "C"])  # C has no time, but its board says today

    def test_a_date_without_a_time_counts_from_noon_eastern(self):
        from datetime import datetime, timedelta, timezone
        from shortlist.apply import date_only, hours_old
        day = datetime(2026, 10, 14, tzinfo=timezone.utc)
        r = posting("W", "Data Engineer", 40, 0) | {"posted_at": day.isoformat()}
        self.assertTrue(date_only(r))
        self.assertAlmostEqual(hours_old(r, now=day + timedelta(hours=36)), 20.0)  # 8 a.m. Eastern the next day

    def test_search_links_are_last_24_hours(self):
        from shortlist.apply import search_links
        role, links = search_links(["Data Engineer"])[0]
        urls = dict(links)
        self.assertIn("f_TPR=r86400", urls["LinkedIn"])
        self.assertIn("fromage=1", urls["Indeed"])


QUEUE = {"items": [], "target": 0}
STATE = {"applied": {}, "skipped": [], "applied_today": 0, "followups": [], "recent": []}
TAILOR = "http://127.0.0.1:8766/"


def page_data(html: str) -> dict:
    """What the page's script starts from: its `const D=...` line, as the server wrote it."""
    return json.loads(re.search(r"const D=(\{.*?\});let S=D\.state", html, re.S).group(1))


def page_script(html: str) -> str:
    return re.search(r"<script>(.*?)</script>", html, re.S).group(1)


class TailorLinkTest(unittest.TestCase):
    """Shortlist searches and tracks. Tailor, a separate tool with its own page, tailors the resumes; a card links to it."""

    def test_the_address_needs_resume_facts_and_follows_tailor_port(self):
        facts = {"resume_facts": "facts.json"}
        self.assertIsNone(apply.tailor_url({}))
        self.assertIsNone(apply.tailor_url({"tailor_port": 9000}))  # nothing to tailor from yet
        self.assertEqual(apply.tailor_url(facts), TAILOR)
        self.assertEqual(apply.tailor_url(facts | {"tailor_port": 9000}), "http://127.0.0.1:9000/")
        self.assertEqual(apply.tailor_url(facts | {"tailor_port": "9001"}), "http://127.0.0.1:9001/")
        self.assertEqual(apply.tailor_url(facts | {"tailor_port": "not a port"}), TAILOR)  # a typo doesn't take the page down

    def test_the_page_carries_the_address_only_when_given(self):
        self.assertEqual(page_data(apply.page(QUEUE, STATE, "tok", TAILOR))["tailor"], TAILOR)
        self.assertIsNone(page_data(apply.page(QUEUE, STATE, "tok"))["tailor"])


class SearchOnlyPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = apply.page(QUEUE, STATE, "tok", TAILOR)
        cls.script = page_script(cls.html)

    def test_no_paste_or_resume_lines_views_and_no_resume_calls(self):
        for gone in ("Paste a job", "Resume lines", "view-paste", "view-lines", "Preview my resume", "Tailor to this job",
                     "/api/resume", "/api/facts"):
            self.assertNotIn(gone, self.html)

    def test_every_id_the_script_looks_up_is_on_the_page(self):  # a lookup that finds nothing stops the whole script
        markup = self.html.split("<script>")[0]
        looked_up = set(re.findall(r"\$\('([\w-]+)'\)", self.script))
        self.assertTrue({"queue", "refresh", "li-go", "tailor-nav"} <= looked_up)
        for name in looked_up:
            self.assertIn(f'id="{name}"', markup)

    def test_a_card_links_to_tailor_in_one_named_tab(self):
        (link,) = re.findall(r"el\('a',\{[^{}]*Tailor this job[^{}]*\}\)", self.script)
        self.assertIn("href:D.tailor+'?job='+encodeURIComponent(it.url)", link)
        self.assertIn("target:'tailor'", link)
        self.assertNotIn("rel:", link)  # noopener would open a new tab on every click instead of reusing the named one
        self.assertIn("D.tailor?el('a'", self.script)  # and only when there's an address to link to


@unittest.skipUnless(shutil.which("node"), "node isn't installed")
class PageScriptTest(unittest.TestCase):
    """The page is one script, so a slip anywhere leaves it blank: check it with node when node is there."""

    @classmethod
    def setUpClass(cls):
        cls.script = page_script(apply.page(QUEUE, STATE, "tok", TAILOR))

    def node(self, code: str) -> str:
        run = subprocess.run(["node", "-"], input=code, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def test_the_script_parses(self):
        self.node(f"new (require('vm').Script)({json.dumps(self.script)})")  # compiled, not run: no page needed

    def test_a_link_goes_only_to_https_or_the_exact_tailor_address(self):
        el = re.search(r"^function el\(.*$", self.script, re.M).group(0)
        hrefs = ["https://example.com/x", TAILOR + "?job=https%3A%2F%2Fa.example%2F1", "http://127.0.0.1:8766", "http://127.0.0.1:8767/",
                 "http://127.0.0.1:8766.evil.test/", "http://evil.test/", "javascript:alert(1)", "", None]

        def allowed(tailor):
            out = self.node("const D = %s;\n" % json.dumps({"tailor": tailor}) +
                            "const document = {createElement: () => ({attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, append() {},"
                            " addEventListener() {}, set href(v) { this.attrs.href = v; }, set textContent(v) {}})};\n" + el +
                            "\nconsole.log(JSON.stringify(%s.map(h => el('a', {href: h}).attrs.href !== undefined)));" % json.dumps(hrefs))
            return json.loads(out)

        self.assertEqual(allowed(TAILOR), [True, True] + [False] * 7)
        self.assertEqual(allowed(None), [True] + [False] * 8)
        self.assertEqual(allowed(""), [True] + [False] * 8)  # an empty address would otherwise match every link


class ServedPageTest(unittest.TestCase):
    """The real server on a free port, over temp files: what it serves, what it no longer serves, and what it still checks."""

    def serve(self, profile=None) -> int:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "today.json").write_text(json.dumps(QUEUE))
        servers = []

        class Recording(ThreadingHTTPServer):  # serve() keeps its server to itself; this lets the test stop it
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                servers.append(self)

            def serve_forever(self, poll_interval=0.5):
                super().serve_forever(0.01)  # so shutdown() returns at once instead of after the half-second poll

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]  # a free port, so the real ones (8765, 8766) are never touched
        for patch in (mock.patch.object(apply, "TODAY", tmp / "today.json"), mock.patch.object(apply, "APPS", tmp / "applications.csv"),
                      mock.patch.object(apply, "SKIPS", tmp / "skipped.csv"), mock.patch.object(apply, "ThreadingHTTPServer", Recording),
                      mock.patch("sys.stdout", io.StringIO())):
            patch.start()
            self.addCleanup(patch.stop)
        thread = threading.Thread(target=apply.serve, args=(profile or {}, port, False), daemon=True)  # False: no browser
        thread.start()
        deadline = time.time() + 10
        while len(servers) < 2 and time.time() < deadline:  # the first is serve() checking that the port is free
            time.sleep(0.01)
        self.assertEqual(len(servers), 2)

        def stop():
            if thread.is_alive():
                servers[-1].shutdown()
            thread.join(10)
        self.addCleanup(stop)
        return servers[-1].server_address[1]

    def ask(self, port, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request(method, path, None if body is None else json.dumps(body), headers or {})
            res = conn.getresponse()
            return res.status, res.read().decode()
        finally:
            conn.close()

    def test_the_page_links_tailor_only_when_resume_facts_is_set(self):
        for profile, want in (({}, None), ({"resume_facts": "facts.json"}, TAILOR),
                              ({"resume_facts": "facts.json", "tailor_port": 9123}, "http://127.0.0.1:9123/")):
            status, html = self.ask(self.serve(profile), "GET", "/")
            self.assertEqual(status, 200)
            self.assertEqual(page_data(html)["tailor"], want, profile)

    def test_the_routes_that_moved_to_tailor_are_gone(self):
        port = self.serve({"resume_facts": "facts.json"})
        token = page_data(self.ask(port, "GET", "/")[1])["token"]
        headers = {"X-Token": token, "Content-Type": "application/json"}
        for path in ("/api/resume/preview", "/api/resume/suggest", "/api/resume/save", "/api/facts/get", "/api/facts/update"):
            status, body = self.ask(port, "POST", path, {}, headers)
            self.assertEqual((status, json.loads(body)), (404, {"error": "not found"}), path)
        self.assertEqual(self.ask(port, "POST", "/api/refresh/status", {}, headers)[0], 200)  # a route that stayed answers

    def test_every_write_still_needs_the_token_the_host_and_the_origin(self):
        port = self.serve()
        token = page_data(self.ask(port, "GET", "/")[1])["token"]
        good = {"X-Token": token}
        # no body: a 403 is sent before the body is read, and a client still sending it can get a connection reset instead
        post = lambda headers: self.ask(port, "POST", "/api/refresh/status", None, headers)[0]
        self.assertEqual(post(good), 200)
        self.assertEqual(post({}), 403)  # no token
        self.assertEqual(post(good | {"X-Token": "not the token"}), 403)
        self.assertEqual(post(good | {"Origin": "http://evil.example"}), 403)
        self.assertEqual(post(good | {"Host": "evil.example"}), 403)
        self.assertEqual(self.ask(port, "GET", "/", headers={"Host": "evil.example"})[0], 403)


class DependencyTest(unittest.TestCase):
    """Shortlist stands on its own: Tailor reads its files, and never the other way around."""

    def test_importing_shortlist_loads_no_tailor_module(self):
        code = ("import sys, shortlist.__main__, shortlist.apply, shortlist.phone, shortlist.linkedin\n"
                "print(sorted(m for m in sys.modules if m == 'tailor' or m.startswith('tailor.')))")
        run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual((run.returncode, run.stdout.strip()), (0, "[]"), run.stderr)

    def test_no_import_in_shortlist_names_tailor(self):  # also catches one tucked inside a function, which importing wouldn't run
        for path in sorted((ROOT / "shortlist").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertNotEqual(name.split(".")[0], "tailor", f"{path.name} line {node.lineno} imports {name}")


if __name__ == "__main__":
    unittest.main()
