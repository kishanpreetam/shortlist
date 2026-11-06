import contextlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from tailor import __main__ as cli
from tailor import app, jobs, lexicon, rewrite
from tailor import resume as engine

MINE = "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows."
FACTS = {
    "contact": {"name": "Sam Example", "lines": ["Boston, MA"], "links": [], "status": "approved"},
    "titles": {"items": ["Data Engineer"], "status": "approved"},
    "summaries": [{"id": "s1", "variant": "data-engineer", "text": "Data engineer who builds ETL pipelines.", "status": "approved"}],
    "education": [{"school": "State University", "degree": "M.S. Data Science", "date": "2025", "status": "approved"}],
    "skills": [{"id": "sk", "label": "Data", "items": ["ETL", "Airflow", "SQL"], "status": "approved"}],
    "roles": [{"id": "r1", "title": "Data Engineer", "org": "Acme", "place": "", "dates": "2023 – 2024", "status": "approved",
               "tools": ["Python", "SQL", "Airflow", "Spark"], "bullets": [
                   {"id": "r1a", "text": MINE, "status": "approved"},
                   {"id": "r1b", "text": "Built Tableau dashboards for 500 users.", "status": "approved"}]}],
    "projects": [{"id": "p1", "title": "FirstCall", "subtitle": "Job search tool", "status": "approved", "bullets": [
        {"id": "p1a", "text": "Built a job search tool in Python that ranks 33,000+ postings.", "status": "approved"}]}],
    "variants": {"data-engineer": ["r1a"]},
}
JD = "We need a data engineer with SQL, Airflow and Spark to build ETL pipelines and dashboards. " * 4
LISTED = JD + "Terraform and Kafka are required. 3+ years of experience with Kubernetes."  # saved with the list
NEW, OLD = "2026-10-29", "2026-10-28"
Q1 = "https://job-boards.greenhouse.io/acme/jobs/11?gh_jid=11&src=x"  # today's queue #3, and #2 on the newest list
C = "https://jobs.lever.co/beta/22"  # its board now has a stub; the list saved it whole
CLOSED = "https://jobs.ashbyhq.com/gamma/33"  # #7 on the newest list; the posting has closed (no board has it)
SHARED = "https://jobs.ashbyhq.com/delta/44"  # on both lists: short on the newest, whole on the older
GONE = "https://jobs.ashbyhq.com/epsilon/55"  # #5 on the newest list, but no description anywhere
P = "https://job-boards.greenhouse.io/zeta/jobs/66"  # only in postings.csv
REQS = [{"id": "R1", "text": "Build ETL pipelines in Python and SQL with Airflow", "kind": "required"},
        {"id": "R2", "text": "3+ years of experience with Kubernetes", "kind": "required"},
        {"id": "R3", "text": "Experience building Tableau dashboards", "kind": "preferred"},
        {"id": "L1", "text": "Must be authorized to work in the US", "kind": "logistics"}]
GOOD = "Engineered Airflow ETL pipelines in Python and SQL to process 1M+ rows."
KEYS = {"job", "path", "note", "pages", "failures", "title_line", "summary", "rewritten", "rejected", "repaired", "rewrite_note",
        "requirements", "required_shown", "required_total", "skills_to_answer", "learning", "assumed", "generated", "logistics", "advice", "page_url"}
PASTE = "Paste it on the Tailor page"


def item(n, company, title, url, text, resume="data-engineer"):
    return {"n": n, "company": company, "title": title, "url": url, "location": "Boston, MA", "resume": resume, "text": text}


class SavedLists(unittest.TestCase):
    """Shortlist's files in a temp folder: today's queue, two saved lists, the job boards and postings.csv. Nothing
    here reads your real files or calls Claude, and every resume goes to a temp folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "today.json").write_text(json.dumps({"items": [
            {"n": 3, "company": "Acme", "title": "Data Engineer", "url": Q1, "resume": "data-engineer"},
            {"n": 4, "company": "Gamma", "title": "Data Analyst", "url": CLOSED, "resume": "data-analyst"},
            {"n": 5, "company": "Epsilon", "title": "Data Engineer", "url": GONE, "resume": "data-engineer"}]}))
        self.lists = self.tmp / "lists"
        self.save_list(NEW, [item(1, "Beta", "Data Engineer", C, JD + "Beta."), item(2, "Acme", "Data Engineer", Q1, JD + "Listed."),
                             item(4, "Delta", "Data Engineer", SHARED, "Too short."), item(5, "Epsilon", "Data Engineer", GONE, ""),
                             item(7, "Gamma", "Data Engineer", CLOSED, LISTED)])
        self.save_list(OLD, [item(1, "Old Co", "Data Analyst", "https://jobs.lever.co/old/1", JD, "data-analyst"),
                             item(2, "Delta", "Data Engineer", SHARED, JD + "Delta, whole.")])
        (self.lists / "archive").mkdir()
        (self.lists / "archive" / "jobs.json").write_text(json.dumps({"items": []}))  # not a day: never a list
        boards = self.tmp / "boards"
        boards.mkdir()
        (boards / "greenhouse__acme.json").write_text(json.dumps([{"url": Q1, "text": JD}]))
        (boards / "lever__beta.json").write_text(json.dumps([{"url": C, "text": "Apply on our site."}]))
        (self.tmp / "postings.csv").write_text(f"score,company,title,url\n8,Zeta,Data Engineer,{P}\n")
        (self.tmp / "results").mkdir()
        (self.tmp / "facts.json").write_text(json.dumps(FACTS))
        self.docs = self.tmp / "Documents"
        self.profile = {"resume_facts": str(self.tmp / "facts.json"), "tailored_dir": str(self.docs),
                        "claude_path": "/fake/claude", "tailor_port": 8790}

        def no_learn(profile):
            raise AssertionError("a test reached the real learn, which writes data/lexicon.json")
        for p in (mock.patch.object(jobs, "QUEUE", self.tmp / "today.json"), mock.patch.object(jobs, "LISTS", self.lists),
                  mock.patch.object(jobs, "POSTINGS", self.tmp / "postings.csv"),
                  mock.patch.object(jobs, "APPS", self.tmp / "applications.csv"),
                  mock.patch.object(jobs, "SKIPS", self.tmp / "skipped.csv"), mock.patch.object(jobs, "BOARDS", boards),
                  mock.patch.object(app, "RESULTS", self.tmp / "results"),
                  mock.patch.object(app, "relearn_if_stale", mock.Mock(return_value=None)),
                  mock.patch.object(lexicon, "PATH", self.tmp / "lexicon.json"), mock.patch.object(lexicon, "update", no_learn)):
            p.start()
            self.addCleanup(p.stop)

    def save_list(self, day, items):
        folder = self.lists / day
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "jobs.json").write_text(json.dumps({"date": day, "made_at": f"{day}T09:00", "hours": 24, "items": items}))

    def pdfs(self):
        return sorted(self.docs.rglob("*.pdf"))


class ListLookupTest(SavedLists):
    """The lists Shortlist saved: found by number, by URL, and as the description of record once a posting closes."""

    def test_days_are_newest_first_and_only_dates(self):
        self.assertEqual(jobs.days(), [NEW, OLD])

    def test_saved_lists_and_their_jobs(self):
        self.assertEqual(jobs.saved()["date"], NEW)  # the newest
        self.assertEqual(jobs.saved(OLD)["date"], OLD)
        for day in ("2026-10-27", "../today", "archive", ""):
            self.assertIsNone(jobs.saved(day), day)
        got = jobs.list_job(7)
        self.assertEqual((got["company"], got["url"], got["text"], got["list"]), ("Gamma", CLOSED, LISTED, NEW))
        self.assertEqual((jobs.list_job(1, OLD)["company"], jobs.list_job(1, OLD)["list"]), ("Old Co", OLD))
        self.assertEqual(jobs.list_job(1)["company"], "Beta")
        for n, day in ((99, None), (7, OLD), (1, "2026-10-27"), (1, "nope")):
            self.assertIsNone(jobs.list_job(n, day), (n, day))

    def test_an_unreadable_newest_list_is_never_swapped_for_an_older_one(self):
        (self.lists / NEW / "jobs.json").write_text("{half a list")
        self.assertIsNone(jobs.saved())
        self.assertIsNone(jobs.list_job(1))  # #1 on the older list is another job
        self.assertEqual(jobs.list_job(1, OLD)["company"], "Old Co")

    def test_find_looks_in_the_queue_then_the_saved_lists_then_the_latest_scan(self):
        self.assertEqual(jobs.find(Q1), {"url": Q1, "company": "Acme", "title": "Data Engineer", "n": 3, "resume": "data-engineer"})
        self.assertEqual(jobs.find(C), {"url": C, "company": "Beta", "title": "Data Engineer", "n": 1, "resume": "data-engineer",
                                        "list": NEW})
        self.assertEqual((jobs.find(SHARED)["n"], jobs.find(SHARED)["list"]), (4, NEW))  # the newest list first
        self.assertEqual(jobs.find("https://jobs.lever.co/old/1")["list"], OLD)
        self.assertEqual(jobs.find(P), {"url": P, "company": "Zeta", "title": "Data Engineer", "n": 0, "resume": None})
        self.assertIsNone(jobs.find("https://example.com/unknown"))

    def test_posting_text_falls_back_to_the_saved_description(self):
        self.assertEqual(jobs.posting_text(Q1), JD)  # the job boards first
        self.assertEqual(jobs.posting_text(C), JD + "Beta.")  # its board has only a stub now
        self.assertEqual(jobs.posting_text(CLOSED), LISTED)  # closed: only the list has it
        self.assertEqual(jobs.posting_text(SHARED), JD + "Delta, whole.")  # the newest list that saved it whole
        self.assertEqual((jobs.posting_text(GONE), jobs.posting_text("https://example.com/unknown")), ("", ""))

    def test_open_jobs_say_whether_a_description_is_saved(self):
        self.assertEqual({j["url"]: j["has_text"] for j in jobs.open_jobs()}, {Q1: True, CLOSED: True, GONE: False})

    def test_a_job_link_opens_a_closed_posting_from_its_list(self):
        data = app.page_data(self.profile, "t", "job=" + quote(CLOSED, safe=""))
        self.assertEqual(data["job"], {"url": CLOSED, "company": "Gamma", "title": "Data Analyst", "n": 4,
                                       "resume": "data-analyst", "has_text": True})  # today's queue, with the list's text
        data = app.page_data(self.profile, "t", "job=" + quote(SHARED, safe=""))
        self.assertEqual((data["job"]["n"], data["job"]["list"], data["job"]["has_text"]), (4, NEW, True))
        self.assertEqual(app._job_of({"url": SHARED})[4], JD + "Delta, whole.")


class Fakes(SavedLists):
    """No Chrome and no Claude: a one-page PDF where Chrome would print it, and Claude's answer for one job (one
    rewrite that keeps your facts, one that changes a number, and which requirements each line shows)."""

    def setUp(self):
        super().setUp()
        self.asked = []

        def printed(html, folder, name, chrome):  # stands in for Chrome: a one-page PDF where Chrome would put it
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(b"%PDF-1.4")
            return folder / name

        def fake(points, title, jd, claude, cache_dir, **kw):  # Claude's answer for one job
            self.asked.append(jd)
            out = rewrite.Rewrites({"r1a": GOOD, "r1b": "Built Tableau dashboards for 5,000 users."})  # 5,000: a changed fact
            out.requirements, out.keywords, out.shows = REQS, [], {"r1a": ["R1"], "r1b": ["R3"]}
            out.summary, out.repaired, out.note = None, [], ""
            return out
        for p in (mock.patch.object(engine, "_print_pdf", printed), mock.patch.object(engine, "_pages", lambda pdf: 1),
                  mock.patch.object(rewrite, "rewrite", fake), mock.patch.object(rewrite, "requirements", lambda *a: REQS),
                  mock.patch.object(rewrite, "keywords", lambda *a: [])):
            p.start()
            self.addCleanup(p.stop)


class TailorJobTest(Fakes):
    """tailor_job: the page's preview and Save PDF for one job, with the page's first choices."""

    def test_a_job_from_a_list_is_saved_when_every_check_passes(self):
        out = app.tailor_job(jobs.list_job(7), self.profile)
        self.assertEqual(set(out), KEYS)
        self.assertEqual(self.asked, [LISTED])  # the description saved with the list: the posting has closed
        self.assertEqual(self.pdfs(), [Path(out["path"])])
        self.assertEqual(Path(out["path"]).parent, self.docs / date.today().isoformat() / "07 Gamma - Data Engineer")
        self.assertEqual((out["note"], out["pages"], out["failures"], out["title_line"]), ("", 1, [], "Data Engineer"))
        self.assertEqual(out["job"], {"n": 7, "company": "Gamma", "title": "Data Engineer", "url": CLOSED, "list": NEW})
        self.assertEqual(out["summary"], "Data engineer who builds ETL pipelines.")
        self.assertEqual(out["rewritten"], [{"id": "r1a", "original": engine.sentence(MINE), "text": engine.sentence(GOOD)}])
        self.assertEqual((out["rejected"], out["repaired"], out["rewrite_note"]), (1, 0, ""))  # the changed number stays yours
        status = {q["id"]: q["status"] for q in out["requirements"]}
        self.assertEqual((status["R1"], status["R2"]), ("full", "partial"))  # Kubernetes on the skills rows, in no line
        self.assertNotIn("L1", status)  # logistics: no line shows them
        self.assertEqual(set(out["requirements"][0]), {"id", "text", "kind", "status", "where", "gaps"})
        self.assertIn("experience", out["requirements"][0]["where"])
        self.assertEqual((out["required_shown"], out["required_total"]), (1, 2))
        # skills the job names that you never answered count as yours (skills rows only); nothing is asked
        self.assertTrue({"Kafka", "Terraform"} <= set(out["assumed"]))
        self.assertEqual((out["skills_to_answer"], out["learning"]), ([], []))
        self.assertEqual(out["logistics"], [{"id": "L1", "text": "Must be authorized to work in the US"}])
        self.assertTrue(any(a["level"] == "change" for a in out["advice"]))
        self.assertEqual(out["page_url"], f"http://127.0.0.1:8790/?job={quote(CLOSED, safe='')}")

    def test_a_failed_check_saves_nothing(self):
        with mock.patch.object(engine, "check", lambda facts, sel, pages: ["The summary names a tool your lines don't show."]):
            out = app.tailor_job(jobs.list_job(7), self.profile)
        self.assertEqual((out["path"], out["failures"]), ("", ["The summary names a tool your lines don't show."]))
        self.assertEqual(self.pdfs(), [])

    def test_the_phone_copy_note_comes_back(self):
        with mock.patch.object(engine, "keep", return_value=(str(self.docs / "x.pdf"), "macOS blocked iCloud Drive.")):
            self.assertEqual(app.tailor_job(jobs.list_job(7), self.profile)["note"], "macOS blocked iCloud Drive.")

    def test_without_a_description_it_says_to_paste_it(self):
        with self.assertRaises(ValueError) as e:
            app.tailor_job(jobs.list_job(5), self.profile)
        self.assertIn(PASTE, str(e.exception))
        self.assertEqual((self.asked, self.pdfs()), ([], []))

    def test_without_rewrites_claude_is_never_asked(self):
        with mock.patch.object(rewrite, "requirements", side_effect=AssertionError("asked Claude")), \
                mock.patch.object(rewrite, "keywords", side_effect=AssertionError("asked Claude")):
            out = app.tailor_job(jobs.list_job(7), self.profile, rewrite=False)
        self.assertEqual((self.asked, out["rewritten"], out["requirements"], out["required_total"]), ([], [], [], 0))
        self.assertTrue(out["path"])
        self.profile["rewrite_points"] = False  # turned off in profile.yaml: the same
        self.assertEqual(app.tailor_job(jobs.list_job(7), self.profile)["rewritten"], [])
        self.assertEqual(self.asked, [])

    def test_a_queue_job_by_url_keeps_its_queue_number(self):
        out = app.tailor_job(jobs.find(Q1), self.profile)
        self.assertEqual(Path(out["path"]).parent.name, "03 Acme - Data Engineer")
        self.assertEqual(self.asked, [JD])  # the job board's description

    def test_tailor_takes_a_saved_description_and_rewrites_on_request(self):
        built = []
        real = engine.build
        with mock.patch.object(engine, "build", lambda *a, **k: built.append((a[1], k.get("rewrites"))) or real(*a, **k)):
            app.tailor(jobs.list_job(7), self.profile, text=LISTED)
            app.tailor(jobs.list_job(7), self.profile, text=LISTED, rewrite=True)
            app.tailor_all(self.profile, [jobs.find(Q1) | {"resume": "data-engineer"}])
        self.assertEqual([(jd, type(r).__name__) for jd, r in built], [(LISTED, "NoneType"), (LISTED, "Rewrites"), (JD, "NoneType")])
        self.assertEqual(self.asked, [LISTED])  # Claude only when asked; tailor_all as it was
        self.assertEqual(len(self.pdfs()), 2)  # the same folder: the second resume replaced the first


class JobCliTest(Fakes):
    """python -m tailor job: what you ask for in chat ("tailor #7") and what comes back."""

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "load_profile", return_value=self.profile), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = cli.main(["job", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_a_job_is_tailored_saved_and_summed_up(self):
        code, out, _ = self.run_cli("7")
        self.assertEqual(code, 0)
        for said in (f"#7 Gamma, Data Engineer (the {NEW} list)", f"Saved: {self.pdfs()[0]}", "Required: 1 of 2 on the page",
                     "partly (required): 3+ years of experience with Kubernetes", f"yours: {engine.sentence(MINE)}",
                     f"new:   {engine.sentence(GOOD)}", "changed a fact", "Added to your skills from this job", "Kafka",
                     "Check these yourself", f"http://127.0.0.1:8790/?job={quote(CLOSED, safe='')}"):
            self.assertIn(said, out)
        self.assertNotIn("Have you used", out)  # no questions when you tailor
        code, out, _ = self.run_cli("7", "--json")
        self.assertEqual((code, set(json.loads(out))), (0, KEYS))
        code, out, _ = self.run_cli("1", "--day", OLD)
        self.assertEqual((code, json.loads(self.run_cli("--url", Q1, "--json")[1])["job"]["n"]), (0, 3))

    def test_a_job_that_cannot_be_tailored_exits_1_with_the_reason(self):
        for argv, why in ((["99"], f"There's no job #99 on the {NEW} list (it has #1 to #7)."),
                          (["1", "--day", "2026-10-27"], f"There's no list saved for 2026-10-27. Saved lists: {NEW}, {OLD}."),
                          (["--url", "https://example.com/unknown"], "That job isn't in Shortlist's queue, saved lists or latest scan."),
                          (["--url", "javascript:alert(1)"], "That job isn't in Shortlist's queue"),
                          (["5"], PASTE)):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(why, err, argv)
        (self.lists / OLD / "jobs.json").write_text("{half a list")
        self.assertIn(f"The list saved for {OLD} can't be read", self.run_cli("1", "--day", OLD)[2])
        shutil.rmtree(self.lists)
        self.assertEqual(self.run_cli("1")[::2], (1, "No saved list yet. Save today's with: python -m shortlist list\n"))
        self.assertEqual(self.pdfs(), [])

    def test_a_failed_check_exits_1_and_saves_nothing(self):
        with mock.patch.object(engine, "check", lambda facts, sel, pages: ["The summary names a tool your lines don't show."]):
            code, out, _ = self.run_cli("7")
        self.assertEqual(code, 1)
        self.assertIn("Nothing was saved, because a check failed: The summary names a tool your lines don't show.", out)
        self.assertEqual(self.pdfs(), [])

    def test_the_command_needs_one_job(self):
        for argv in ([], ["7", "--url", Q1], ["--url", Q1, "--day", NEW], ["7", "--day", "29-10-2026"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.main(["job", *argv])


class SkillCliTest(SavedLists):
    """python -m tailor skill: the page's skills question, answered once from the command line."""

    def run_cli(self, *argv, profile=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "load_profile", return_value=profile or self.profile), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = cli.main(["skill", *argv])
        return code, out.getvalue(), err.getvalue()

    def added(self):
        return {a["term"]: a for a in json.loads((self.tmp / "facts.json").read_text()).get("added_skills") or []}

    def test_an_answer_is_saved_as_the_page_saves_it(self):
        code, out, _ = self.run_cli("KAFKA", "used", "--where", "r1, p1")
        self.assertEqual((code, out.split(":")[0]), (0, "Saved"))
        self.assertIn("Kafka: yes, at Acme, FirstCall.", out)
        entry = self.added()["kafka"]
        self.assertEqual((entry["level"], entry["where"], entry["status"], entry["label"]), ("used", ["p1", "r1"], "approved", "Kafka"))
        self.assertEqual(self.run_cli("terraform", "no")[0], 0)
        self.assertEqual(self.added()["terraform"]["level"], "no")
        code, out, _ = self.run_cli("Power-BI", "learning")
        self.assertEqual((code, self.added()["power_bi"]["level"]), (0, "learning"))
        self.assertIn("Power BI: learning", out)
        self.assertIn("yes, on your skills line", self.run_cli("spark", "used")[1])

    def test_a_skill_by_its_key_or_any_spelling_of_its_name(self):
        for text, term in (("kafka", "kafka"), ("Kafka", "kafka"), ("power bi", "power_bi"), ("power_bi", "power_bi"),
                           ("scikit learn", "scikit_learn"), ("CI/CD", "ci_cd"), ("c++", "cpp"), ("R", "r_lang"),
                           ("Kafak", None), ("theme:pipelines", None), ("", None)):
            self.assertEqual(cli.skill_term(text), term, text)

    def test_unknown_terms_levels_and_places_are_refused_with_the_reason(self):
        before = (self.tmp / "facts.json").read_text()
        for argv, why in ((["Kafak", "used"], 'Tailor doesn\'t know the skill "Kafak"'),
                          (["Kafak", "used"], "(maybe Kafka)"),
                          (["kafka", "used", "--where", "r9"], "None of your roles or projects has the id r9."),
                          (["kafka", "no", "--where", "r1"], "--where goes with used"),
                          (["kafka", "learning", "--where", "r1"], "--where goes with used")):
            code, out, err = self.run_cli(*argv)
            self.assertEqual((code, out), (1, ""), argv)
            self.assertIn(why, err, argv)
        code, _, err = self.run_cli("kafka", "used", profile={"claude_path": "/fake/claude"})
        self.assertEqual(code, 1)
        self.assertIn("Set resume_facts in profile.yaml", err)
        for argv in (["kafka", "yes"], ["kafka"], []):  # a level the page doesn't offer, or none
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.main(["skill", *argv])
        self.assertEqual((self.tmp / "facts.json").read_text(), before)  # nothing written

    def test_places_lists_your_roles_and_projects_for_where(self):
        code, out, _ = self.run_cli("--places")
        self.assertEqual(code, 0)
        self.assertEqual([line.split(None, 1) for line in out.splitlines()], [["r1", "Acme"], ["p1", "FirstCall"]])


if __name__ == "__main__":
    unittest.main()


class UnansweredSkillsTest(unittest.TestCase):
    """A job's skills you never answered count as yours for that resume (asking for it says you have them): on the
    skills rows, never in an experience or project line; nothing is asked or saved, and an answer you gave stands."""

    def test_assumed_skills(self):
        from tailor import app
        facts = {"added_skills": [{"id": "as_kafka", "term": "kafka", "label": "Kafka", "level": "used", "status": "approved"},
                                  {"id": "as_java", "term": "java", "label": "Java", "level": "no", "status": "approved"}]}
        got = app.assumed_skills(facts, ["kafka", "java", "sap"])
        self.assertEqual([(a["term"], a["level"], a.get("where")) for a in got["added_skills"]],
                         [("kafka", "used", None), ("java", "no", None), ("sap", "used", None)])
        self.assertEqual(engine.mapped_tools(got, "r1"), engine.mapped_tools(facts, "r1"))  # no role may claim them
        self.assertEqual(app.assumed_skills(facts, ["sap"], "learning")["added_skills"][-1]["level"], "learning")
        self.assertEqual(len(facts["added_skills"]), 2)  # the facts you have stay as they are
        self.assertIs(app.assumed_skills(facts, ["kafka"]), facts)
