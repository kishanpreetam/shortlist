"""Tailor: a one-page resume for one job posting, from lines you approved, on a local page.

    python -m tailor        open it at http://127.0.0.1:8766 (tailor_port in profile.yaml to change it)

Tailor runs on its own: Shortlist doesn't need to be running. A job you paste needs nothing from Shortlist; for a
job from Shortlist's queue (opened as /?job=<posting url> from a card there, or picked on the page) Tailor reads
Shortlist's files, read only, through jobs.py, and never fetches the posting itself. In the background Claude
rewrites your points for the next few jobs in the queue (the warm-up), and Tailor learns the tools postings name
once Shortlist has read new job boards (lexicon.py).

A resume is saved only when you press Save PDF; previews stay in results/tailor-preview. The lines you approve, add
or reword under Resume lines go to your facts file (resume_facts in profile.yaml). Without the page, `python -m
tailor job N` does what a preview and Save PDF do for job N on a list Shortlist saved (tailor_job), from the
description saved with the list, so it works after the posting has closed.

The page listens on 127.0.0.1 only and answers only requests addressed to it there, and every write needs the
token the page got when it loaded, new each run. Claude runs locked down as in rewrite.py (no tools, no MCP
servers, an empty folder), and nothing it writes reaches a resume unless it keeps your facts.
"""

from __future__ import annotations

import http.client
import json
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from . import jobs, lexicon, page, rewrite
from . import resume as engine
from .paths import RESULTS

CHROME_DEFAULT = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PREVIEWS: dict[str, dict] = {}
PROBE_S = 2  # how long serve() waits for whatever holds its port to say whether it's Tailor


def _facts_path(profile: dict) -> Path:
    path = profile.get("resume_facts")
    if not path:
        raise ValueError("Set resume_facts in profile.yaml to your facts file to tailor resumes.")
    return Path(str(path)).expanduser()


def variant_for_title(title: str) -> str:
    t = engine.choose_title(title, [t for _, t in engine.TITLE_RULES], "ai-engineer")
    return {"Analytics Engineer": "data-engineer", "Data Engineer": "data-engineer", "Machine Learning Engineer": "data-scientist",
            "Data Scientist": "data-scientist", "Business Analyst": "data-analyst", "Data Analyst": "data-analyst"}.get(t, "ai-engineer")


def _job_url(value) -> str:
    """A posting URL to look up in Shortlist's files: http or https and at most 2,000 characters, else ""."""
    url = str(value or "").strip()
    return url if url.startswith(("https://", "http://")) and len(url) <= 2000 else ""


def _job_of(d: dict) -> tuple[str, str, int, str, str]:
    """The job a preview is for, from Shortlist's results (by its posting URL) or pasted: (company, title, n, resume
    version, description). A URL is only looked up in Shortlist's files, never fetched."""
    if d.get("paste"):
        pst = d["paste"] if isinstance(d["paste"], dict) else {}
        company, title = str(pst.get("company", "")).strip()[:120], str(pst.get("title", "")).strip()[:200]
        jd = str(pst.get("jd", ""))[:60_000]
        if not company or not title or len(jd) < 200:
            raise ValueError("Add the company, the job title and the whole job description.")
        n, variant = 0, variant_for_title(title)
    else:
        url = _job_url(d.get("url"))
        item = jobs.find(url) if url else None
        if not item:
            raise ValueError("That job isn't in Shortlist's latest results. Paste it under Tailor a job.")
        # a job from postings.csv has no resume version yet: the title picks one, as for a pasted job
        company, title, n, variant = item["company"], item["title"], item["n"], item["resume"] or variant_for_title(item["title"])
        jd = jobs.posting_text(url)
        if len(jd) < 200:
            raise ValueError("The job description for this posting isn't saved. Paste it under Tailor a job.")
    return company, title, n, variant, jd


def exact(profile: dict) -> bool:
    """Exact mode (`tailor_exact: true` in profile.yaml): you said that asking for a job's resume means you have every
    skill and kind of experience it asks for, and to tailor exactly to it (2026-10-29). Rewrites and the summary then
    use the posting's skills, tools, words and ownership as yours; your numbers, employers, titles, dates, limits,
    fields and results stay exactly as your lines have them (resume.posting_words)."""
    return bool(profile.get("tailor_exact"))


def _summary_ctx(facts: dict, job_title: str, variant: str, phrases=(), posting: str = "") -> dict:
    """What the summary written for a job starts from: its opening, your evidence, the tools you can name and the
    posting's keywords (`phrases`), which it uses in the posting's words where your evidence shows the work."""
    title = engine.choose_title(job_title, facts["titles"]["items"] if engine.ok(facts["titles"]) else [], variant)
    opening = engine.summary_opening(facts, title, variant)
    return {"opening": opening, "evidence": engine.summary_evidence(facts),
            "tools": sorted({engine.item_label(t) for t in engine.placed(facts) | engine.evidence(facts)}
                            | {it for x in facts.get("skills") or [] if engine.ok(x) for it in x["items"]}),
            "keywords": list(phrases)[:engine.KEYWORDS_MAX],
            "check": lambda text, uses: engine.summary_problems(text, facts, opening, uses, phrases, posting)}


def _job_reading(facts: dict, title: str, jd: str, company: str, variant: str, claude: str | None,
                 exact_mode: bool = False) -> tuple[list, dict, list]:
    """Your points for one job, each with the posting's keywords for what it shows; the summary's context; and the
    posting's ranked keywords. The posting is read first (once, cached), so its keywords reach every rewrite."""
    given, reqs = [], []
    if claude and jd.strip():
        try:
            reqs = rewrite.requirements(title, jd, claude, RESULTS / "requirements")
            given = rewrite.keywords(title, jd, claude, RESULTS / "requirements")
        except rewrite.RewriteError:
            pass
    kws = engine.job_keywords(title, jd, given, company, reqs)
    posting = engine.posting_words(jd) if exact_mode else ""
    return (rewrite.points_for(facts, title, jd, company, kws, exact_mode),
            _summary_ctx(facts, title, variant, [k["text"] for k in kws], posting), kws)


def _point_check(facts: dict, kws: list, profile: dict, jd: str = ""):
    """The fact check each rewrite of your points must pass, with the posting's keywords as the resume engine reads
    them, so the ones that fail go back to Claude once (`rewrite_repair`, on unless false); None when that's off."""
    if not profile.get("rewrite_repair", True):
        return None
    return rewrite.point_check(facts, [k["text"] for k in kws], engine.posting_words(jd) if exact(profile) else "")


def resume_suggest(d: dict, profile: dict) -> dict:
    """Suggested lines for one requirement of a job: two or three, each for one of your roles or projects, labeled
    by what it adds beyond that place's facts, with one recommended (one that only rewords your lines when any does)."""
    facts = engine.load_facts(_facts_path(profile))
    company, title, n, variant, jd = _job_of(d)
    claude = rewrite.claude_path(profile)
    if not claude:
        raise ValueError("Suggestions need Claude Code installed and signed in (claude auth login).")
    requirement = " ".join(str(d.get("requirement") or "").split())[:200]
    if len(requirement) < 5:
        raise ValueError("Pick a requirement to suggest lines for.")
    places = engine.place_evidence(facts)
    own = {b["id"]: b["text"] for grp in ("roles", "projects") for g in facts[grp] for b in g["bullets"]}
    page = [own[i] for i in d.get("on_page") or [] if isinstance(i, str) and i in own]  # the lines this preview shows
    try:
        got = rewrite.suggest_lines(requirement, title, jd, places, claude, RESULTS / "suggestions", page)
    except rewrite.RewriteError as exc:
        raise ValueError(str(exc)) from exc
    names = {x["id"]: x["name"] for x in places}
    roles = {g["id"] for g in facts["roles"]}
    out = []
    for x in got:
        check = engine.suggestion_check(x["text"], facts, x["place"])
        if not check["hard"]:
            rank = engine.rank_draft(x["text"], requirement, page, x["place"] in roles)
            out.append({**x, "name": names.get(x["place"], ""), "adds": check["adds"], "grounded": not check["adds"], "rank": rank,
                        "in_experience": x["place"] in roles})
    # a draft the page already shows adds nothing; the recommendation then goes, in order, to one that addresses the
    # requirement at all, is true to your lines, names the skill the requirement names, and covers the most of it,
    # adds what the page lacks and lands in your experience (rank_draft's score)
    out = [o for o in out if o["rank"]["dup"] < 0.8]
    for o in out:  # how much you'd be confirming: nothing, a few words, or facts only you know (blanks or numbers)
        o["truth"] = 0 if o["grounded"] else 2 if any(a.startswith(("blanks", "numbers")) for a in o["adds"]) else 1
    strongest = min(out, key=lambda o: (o["rank"]["share"] == 0, -o["rank"]["named"], -o["rank"]["score"]), default=None)
    out.sort(key=lambda o: (o["rank"]["share"] == 0, o["truth"], -o["rank"]["named"], -o["rank"]["score"]))
    for i, o in enumerate(out):
        o["recommended"] = i == 0  # the strongest line you can truthfully use now
        o["strongest"] = o is strongest  # covers the most of the requirement: always one, often the recommended one
        o["why"] = _draft_why(o)
    return {"requirement": requirement, "suggestions": out}


def _draft_why(o: dict) -> str:
    """Why a draft ranks where it does, in plain words."""
    r = o["rank"]
    parts = [["only rewords your lines", "adds a few words for you to confirm", "has facts only you can fill in"][o["truth"]]]
    if r["covers"]:
        parts.append(f"covers {len(r['covers'])} key words of the requirement ({', '.join(r['covers'])})")
    parts.append("new to this page" if r["dup"] < 0.5 else "close to a line already on the page")
    parts.append("in your experience" if o["in_experience"] else "in a project")
    return ", ".join(parts)


def _rewritten(facts: dict, title: str, jd: str, company: str, variant: str, profile: dict, wanted: bool = True):
    """Your points for one job and Claude's rewrites of them, as a preview makes them: (points, rewrites, note,
    failed). With `wanted` false, or rewrite_points false in profile.yaml, Claude isn't asked; the rewrites are {} when
    Claude can't answer, and `note` says why."""
    # your points reworded for this job by Claude; the engine keeps only rewrites that keep your facts
    claude = rewrite.claude_path(profile) if wanted and profile.get("rewrite_points", True) else None
    points, ctx, kws = _job_reading(facts, title, jd, company, variant, claude, exact(profile))
    rewrites, note, failed = {}, "", False
    if wanted and profile.get("rewrite_points", True):
        if not claude:
            note = "Claude Code isn't installed, so your own wording is used."
        else:
            try:
                rewrites = rewrite.rewrite(points, title, jd, claude, RESULTS / "rewrites", summary_ctx=ctx,
                                           check=_point_check(facts, kws, profile, jd), exact=exact(profile))
                note = getattr(rewrites, "note", "")
            except rewrite.RewriteError as exc:
                note, failed = f"{exc} Your own wording is used for now.", True
    return points, rewrites, note, failed


def resume_preview(d: dict, profile: dict) -> dict:
    """Pick approved lines for one job (from Shortlist's results, or pasted), print and check the page; nothing is
    saved yet."""
    relearn_if_stale(profile)  # in the background: this preview never waits for it
    facts = engine.load_facts(_facts_path(profile))
    return _preview(facts, _job_of(d), d, profile)


def assumed_skills(facts: dict, terms, level: str = "used") -> dict:
    """`facts` with the job skills in `terms` that you never answered added at `level`, for this resume only. You said
    that asking for a job's resume means you've checked you have the skills and experience it asks for (2026-10-29),
    and not to be asked each time; so they go on your skills rows ("used") or, with `unanswered_skills: learning` in
    profile.yaml, on the Currently learning line. They name no role, so no experience or project line ever claims them
    (mapped_tools): your lines keep your employers, numbers and scope. Nothing is saved, and a skill you did answer
    keeps your answer. `unanswered_skills: off` leaves them off."""
    answered = {a["term"] for a in facts.get("added_skills") or []}
    extra = [{"id": "as_" + t, "term": t, "label": engine.item_label(t), "level": level, "status": "approved", "assumed": True,
              "source": "a skill this job asks for; asking for its resume says you have it"} for t in terms if t not in answered]
    return dict(facts, added_skills=list(facts.get("added_skills") or []) + extra) if extra else facts

def places(facts: dict) -> list[dict]:
    """Your roles and projects as the point engine reads them: where and when, the tense of the work, how many new
    points to write for each (experience is the main section), and the lines and details you have there."""
    out = []
    for grp in ("roles", "projects"):
        for g in facts[grp]:
            if not engine.ok(g):
                continue
            lines = [b["text"] for b in g["bullets"] if engine.ok(b)]
            details = [x for b in g["bullets"] if engine.ok(b) for x in engine.supporting_facts(b)]
            role = grp == "roles"
            tense = ("present" if re.search(r"present|current", g.get("dates", ""), re.I) else "past") if role else None
            out.append({"id": g["id"], "kind": "role" if role else "project",
                        "where": f'{g.get("org") or g.get("title", "")} | {g.get("title", "") if role else g.get("subtitle", "")}',
                        "dates": g.get("dates", ""), **({"tense": tense} if tense else {}), "want": 3 if role else 1,
                        "points": lines + details, "tools": list(g.get("tools") or [])})
    return out


def with_generated(facts: dict, points: list[dict], job_text: str = "", rewritten: dict | None = None) -> tuple[dict, dict]:
    """`facts` with the point engine's points that pass resume.generated_problems, each an approved line of its role
    or project for this resume only (nothing is saved), and {line id: the requirement ids it shows}."""
    import copy
    out = copy.deepcopy(facts)
    by = {g["id"]: g for grp in ("roles", "projects") for g in out[grp] if engine.ok(g)}
    read = {pl["id"]: pl for pl in places(facts)}
    shows: dict[str, list[str]] = {}
    kept: dict[str, list[str]] = {}
    for x in points:
        g, pl = by.get(x.get("place")), read.get(x.get("place"))
        if not g or not pl:
            continue
        text = engine.sentence(x["text"])
        seen = [rewritten[b["id"]] for b in g["bullets"] if b["id"] in (rewritten or {})]  # your lines as reworded for this job
        if engine.generated_problems(text, pl, seen + kept.get(g["id"], []), job_text):
            continue
        n = len(kept.setdefault(g["id"], [])) + 1
        bid = f"{g['id']}_gen{n}"
        g["bullets"].append({"id": bid, "text": text, "status": "approved", "generated": True,
                             "source": "written for this job by Tailor's point engine"})
        kept[g["id"]].append(text)
        shows[bid] = list(x.get("shows") or [])
    return out, shows


def _line_requirements(title: str, jd: str, claude: str) -> list[dict]:
    """The posting's requirements as rewrite.rewrite works from them (logistics left out), or [] when there's no
    posting left to read or Claude can't read it."""
    if not rewrite.clean_posting(jd)[0].strip():
        return []
    try:
        return [r for r in rewrite.requirements(title, jd, claude, RESULTS / "requirements") if r.get("kind") != "logistics"]
    except rewrite.RewriteError:
        return []


def _points_ahead(facts: dict, title: str, jd: str, profile: dict):
    """The point engine's call for this job, started with the preview so Claude writes new points while it rewrites
    yours instead of after: a future of (the requirements and places it wrote from, its points), or None when the
    preview won't ask for new points. It waits for the posting's read like the rewrites do (one read, cached)."""
    claude = rewrite.claude_path(profile)
    if not profile.get("generate_points") or not profile.get("rewrite_points", True) or not claude:
        return None

    def write():
        reqs, pl = _line_requirements(title, jd, claude), places(facts)
        return reqs, pl, rewrite.generate_points(title, jd, reqs, pl, claude, RESULTS / "generated") if reqs else []
    pool = ThreadPoolExecutor(1, thread_name_prefix="new-points")
    try:
        return pool.submit(write)
    finally:
        pool.shutdown(wait=False)


def _generated(facts: dict, title: str, jd: str, rewrites, profile: dict, ahead=None) -> tuple[dict, str]:
    """The point engine (generate_points in profile.yaml): new points for your roles and projects from this job's
    requirements, the passing ones added to `facts` for this resume, and what they show added to the rewrites.
    `ahead` (_points_ahead): the same call started with the preview, used when it wrote from the requirements and
    places these rewrites came from, so its points are the ones asking now would get (the same answer, cached)."""
    claude = rewrite.claude_path(profile)
    reqs = [r for r in getattr(rewrites, "requirements", None) or [] if r.get("kind") != "logistics"]
    if not profile.get("generate_points") or not claude or not reqs or not isinstance(rewrites, rewrite.Rewrites):
        return facts, ""
    try:
        early = ahead.result() if ahead else None
    except rewrite.RewriteError:
        early = None  # asked again below, as without it: a failure under a minute old comes back at once
    try:
        got = early[2] if early and early[:2] == (reqs, places(facts)) else \
            rewrite.generate_points(title, jd, reqs, places(facts), claude, RESULTS / "generated")
    except rewrite.RewriteError as exc:
        return facts, f"New points couldn't be written for this job ({exc})."
    facts, shows = with_generated(facts, got, jd, dict(rewrites))
    rewrites.shows = {**(getattr(rewrites, "shows", None) or {}), **shows}
    return facts, ""


def _preview(facts: dict, job: tuple, d: dict, profile: dict) -> dict:
    """The preview of one job, `job` as _job_of gives it, with the choices in `d` (exclude, include, title_skip,
    original, skill_skip, summary_id, rewrite)."""
    company, title, n, variant, jd = job
    pid = engine.new_preview_id()
    ids = lambda key: set(map(str, d.get(key) or []))
    ahead = _points_ahead(facts, title, jd, profile) if d.get("rewrite", True) else None  # written alongside the rewrites
    points, rewrites, note, failed = _rewritten(facts, title, jd, company, variant, profile, d.get("rewrite", True))
    if d.get("rewrite", True):  # new points for your experience, written from this job (the point engine)
        facts, gen_note = _generated(facts, title, jd, rewrites, profile, ahead)
        note = " ".join(x for x in (note, gen_note) if x)
    fit = RESULTS / "fit.json"  # the page size that fit last time, kept across restarts so a page prints once or twice
    try:
        engine._FIT.update(json.loads(fit.read_text()))
    except (OSError, ValueError):
        pass
    chrome = profile.get("chrome_path") or CHROME_DEFAULT
    make = lambda facts: engine.build(facts, jd, title, variant, RESULTS / "tailor-preview" / pid, None, chrome,
                                      exclude=ids("exclude"), include=ids("include"), summary_id=d.get("summary_id") or None,
                                      title_skip=ids("title_skip"), original=ids("original"), skill_skip=ids("skill_skip"),
                                      employer=company, rewrites=rewrites, exact=exact(profile),
                                      proven=bool(profile.get("skills_proven")), fill=bool(profile.get("fill_page")))
    with engine.printing(chrome):  # one Chrome for every print of this preview
        out = make(facts)
        asked = [x["term"] for x in out["selection"].get("skills_detail") or [] if x["state"] == "missing"]
        level = profile.get("unanswered_skills", "used")
        if asked and level in ("used", "learning"):
            facts = assumed_skills(facts, asked, level)  # the job's skills you never answered: yours, never asked about
            out = make(facts)
    assumed = {a["term"] for a in facts.get("added_skills") or [] if a.get("source", "").startswith("a skill this job asks for")}
    try:
        fit.write_text(json.dumps(engine._FIT))
    except OSError:
        pass
    sel = out["selection"]
    mine, kept = {x["id"]: x["text"] for x in points}, engine.faithful_rewrites(facts, rewrites, sel.get("phrases") or (),
                                                                                  sel.get("exact_posting") or "")
    # the preview shows each line as the page prints it; actions use ids, never this text
    for b in [b for g in sel["experience"] + sel["projects"] for b in g["bullets"]] + list(sel["available"]):
        b["text"] = engine.sentence(b["text"])
        if b.get("original"):
            b["original"] = engine.sentence(b["original"])
    if sel["summary"]:
        sel["summary"] = dict(sel["summary"], text=engine.printed_summary(sel["summary"]["text"]))
    PREVIEWS[pid] = {"pdf": out["pdf"] if not out["failures"] else None, "n": n, "company": company, "title": title}
    while len(PREVIEWS) > 60:
        PREVIEWS.pop(next(iter(PREVIEWS)))
    return {"id": pid, "ok": bool(PREVIEWS[pid]["pdf"]), "failures": out["failures"], "pages": out["pages"],
            "trimmed": out["trimmed"], "title_line": sel["title_line"], "summary": sel["summary"],
            "summaries": [{"id": x["id"], "variant": x.get("variant", "")} for x in facts["summaries"] if engine.ok(x)],
            "experience": sel["experience"], "projects": sel["projects"], "skills": sel["skills"],
            "covered": sel["covered"], "learning": sel["learning"], "not_on_page": sel["not_on_page"], "missing": sel["missing"],
            "skills_detail": sel["skills_detail"], "coverage": sel["coverage"], "available": sel["available"],
            "pending": engine.pending_count(facts), "pending_versions": engine.pending_versions(facts),
            "requirements": sel.get("requirements") or [], "advice": sel.get("advice") or [], "logistics": sel.get("logistics") or [],
            "keywords": sel.get("keywords") or [],
            "places": [{"id": g["id"], "name": g.get("org") or g.get("title", "")} for grp in ("roles", "projects")
                       for g in facts[grp] if engine.ok(g)],
            "generated": [{"id": b["id"], "text": b["text"], "where": g.get("heading", "")} for g in sel["experience"] + sel["projects"]
                          for b in g["bullets"] if "_gen" in b["id"]],
            "assumed": [engine.item_label(t) for t in sorted(assumed)
                        if engine.item_label(t) in {it for row in sel["skills"] for it in row["items"]}],
            "rewrite_note": note, "rewrite_failed": failed, "rewritten": len(sel.get("rewrites") or {}),
            "rejected": len([k for k, v in rewrites.items() if k in mine and v != mine[k] and k not in kept]),
            "repaired": len([k for k in getattr(rewrites, "repaired", ()) if k in kept]),
            "originals": {k: engine.sentence(mine[k]) for k in sel.get("rewrites") or {} if k in mine}}


# ---------- the warm-up ----------

WARM = {"note": ""}
_WARM_KICK = threading.Event()
WATCH_S = 30  # how often Tailor checks whether Shortlist's queue or your applied and skipped lists changed


def kick_warmup() -> None:
    """Have the warm-up look ahead again (when the page starts, and when Shortlist's queue or your applied and skipped
    lists change: watch_shortlist)."""
    _WARM_KICK.set()


def warm_ahead(profile: dict) -> int:
    """How many of the next jobs to rewrite ahead of time: `rewrite_warmup` (5 by default, 0 or false for none)."""
    v = profile.get("rewrite_warmup", 5)
    return (5 if v else 0) if isinstance(v, bool) else max(0, int(v or 0))


def warm_rewrites(profile: dict, rounds: int | None = None) -> None:
    """Have Claude rewrite your points ahead of time for the next few jobs in today's queue, one call at a time, so
    Tailor opens in seconds. Each round stops at the first error (signed out, offline); Tailor then says why."""
    ahead, claude = warm_ahead(profile), rewrite.claude_path(profile)
    if not claude or not ahead or not profile.get("rewrite_points", True):
        return
    while rounds is None or rounds > 0:
        _WARM_KICK.wait()
        _WARM_KICK.clear()
        try:
            _warm_round(profile, claude, ahead)
        except Exception as exc:  # never let the warm-up die: Tailor still works without it
            WARM["note"] = f"The warm-up stopped: {exc}"
        if rounds is not None:
            rounds -= 1


def _warm_round(profile: dict, claude: str, ahead: int) -> None:
    WARM["note"] = ""
    for item in jobs.open_jobs()[:ahead]:  # today's queue minus what you applied to or skipped
        jd = jobs.posting_text(item["url"])
        if len(jd) < 200:
            continue
        facts = engine.load_facts(_facts_path(profile))  # fresh for each job, so lines you just approved count
        try:  # the rewrites for this job, ready before you click Tailor
            points, ctx, kws = _job_reading(facts, item["title"], jd, item["company"], item.get("resume") or variant_for_title(item["title"]),
                                            claude, exact(profile))
            rewrite.rewrite(points, item["title"], jd, claude, RESULTS / "rewrites", parallel=False, summary_ctx=ctx,
                            check=_point_check(facts, kws, profile, jd), exact=exact(profile))
        except rewrite.RewriteError as exc:
            WARM["note"] = str(exc)
            return


def watch_shortlist(stop: threading.Event | None = None, every: float = WATCH_S) -> None:
    """Kick the warm-up when Shortlist picks a new queue or you mark a job applied, skip it or undo that on Shortlist's
    page, so the jobs you'll tailor next are rewritten ahead. Shortlist doesn't tell Tailor, so Tailor looks every 30
    seconds at when those files last changed. Runs until `stop` is set."""
    stop = stop or threading.Event()
    last = jobs.stamp()
    while not stop.wait(every):
        now = jobs.stamp()
        if now != last:
            last = now
            kick_warmup()


# ---------- the tools postings name ----------

_LEARNING = threading.Lock()  # one learn at a time
_LEARN = {"boards": None}  # the boards (their folder's mtime) the last learn started from


def relearn_if_stale(profile: dict) -> threading.Thread | None:
    """Learn the tools postings name again (lexicon.py) when Shortlist has read job boards since the last learn.
    Shortlist's scan no longer learns them, so Tailor does, when it starts and with each preview. The learn takes
    about 20 seconds, so it runs in the background and this returns at once; a learn already running isn't started
    twice, and the same boards aren't learned from twice in one run, so a learn that fails isn't retried until
    Shortlist reads the boards again. A failure prints one line and leaves the tools learned before in use.
    The learn's thread when one started, else None."""
    if not profile.get("learn_keywords", True):
        return None
    boards, learned = jobs.mtime(jobs.BOARDS), jobs.mtime(lexicon.PATH)
    if boards is None or (learned is not None and learned >= boards) or boards == _LEARN["boards"]:
        return None
    if not _LEARNING.acquire(blocking=False):
        return None
    _LEARN["boards"] = boards
    t = threading.Thread(target=_relearn, args=(profile,), name="learn-tools", daemon=True)
    try:
        t.start()
    except RuntimeError as exc:  # no thread to spare right now: a later preview tries again
        _LEARN["boards"] = None
        _LEARNING.release()
        print(f"  ! learning tools from the postings: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    return t


def _relearn(profile: dict) -> None:
    try:
        engine.use_lexicon(lexicon.update(profile))
    except Exception as exc:  # learning is extra: it never fails a preview, and the tools learned before stay in use
        print(f"  ! learning tools from the postings: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        _LEARNING.release()


# ---------- saving, and your facts ----------

def resume_save(d: dict, profile: dict) -> dict:
    """Keep a checked preview: Documents (tailored_dir) and a phone copy (tailored_copy_dir)."""
    prev = PREVIEWS.get(str(d.get("id", "")))
    if not prev or not prev["pdf"]:
        raise ValueError("Preview it again; only a preview that passed every check can be saved.")
    folder = tailored_folder(profile) / date.today().isoformat() / job_folder_name(prev)
    root = profile.get("tailored_copy_dir")
    copy_folder = Path(str(root)).expanduser() / date.today().isoformat() / folder.name if root else None
    path, note = engine.keep(prev["pdf"], folder, copy_folder)
    return {"path": path, "note": note}


_DEMAND: dict = {"mtime": None, "counts": {}}


def skill_demand(facts: dict) -> list[dict]:
    """How many of today's jobs name each skill, and where it stands on your resume."""
    mtime = jobs.mtime(jobs.QUEUE)
    if mtime is None:
        return []
    if _DEMAND["mtime"] != mtime:
        counts: dict[str, int] = {}
        for it in jobs.queue().get("items", []):
            for t in engine.terms(engine.without_employer(f"{it.get('title', '')}\n{jobs.posting_text(it.get('url', ''))}", it.get("company", ""))):
                if not t.startswith("theme:"):
                    counts[t] = counts.get(t, 0) + 1
        _DEMAND.update(mtime=mtime, counts=counts)
    on_lines = engine._item_terms([it for s in facts["skills"] if engine.ok(s) for it in s["items"]])
    backed = engine.evidence(facts)
    added = {a["term"]: a.get("level") for a in facts.get("added_skills") or [] if engine.ok(a)}
    def state(t):
        return ("on your skills lines" if t in on_lines else "backed by your lines" if t in backed else
                {"used": "added by you", "learning": "on your learning line"}.get(added.get(t), "not added"))
    return [{"term": t, "label": engine.item_label(t), "n": c, "state": state(t)}
            for t, c in sorted(_DEMAND["counts"].items(), key=lambda kv: (-kv[1], kv[0]))]


def facts_view(profile: dict) -> dict:
    """Your facts file for the Resume lines tab, with each suggested version's checks and today's skill demand."""
    facts = engine.load_facts(_facts_path(profile))
    for grp in ("roles", "projects"):
        for g in facts[grp]:
            for b in g["bullets"]:
                for a in b.get("alts") or []:
                    a["problems"] = engine.faithful(b["text"], a)
                    a["adds_text"] = engine.adds_text(a)
    facts["demand"] = skill_demand(facts)
    facts["pending_versions"] = engine.pending_versions(facts)
    return facts


def facts_update(d: dict, profile: dict) -> dict:
    """Approve, remove, restore or reword one of your lines; approve or remove a suggested version; add a skill.
    Rewording is yours alone: a suggested version is used only after you approve it, and only while it keeps your facts."""
    path = _facts_path(profile)
    facts = engine.load_facts(path)
    section, ident, status, text = d.get("section"), d.get("id"), d.get("status"), d.get("text")
    if status not in (None, "approved", "pending", "removed"):
        raise ValueError("Unknown status.")
    stamp = date.today().isoformat()
    if section in ("alt", "alts"):
        if status is None or text is not None:
            raise ValueError("A suggested version can be approved or removed, not edited. Edit your own line instead.")
        found = 0
        for grp in ("roles", "projects"):
            for g in facts[grp]:
                for b in g["bullets"]:
                    for a in b.get("alts") or []:
                        if (section == "alt" and a["id"] == ident) or (section == "alts" and a.get("status") == "pending"):
                            problems = engine.faithful(b["text"], a)
                            if status == "approved" and problems:
                                if section == "alt":
                                    raise ValueError("This version changes a fact, so it can't be approved: " + "; ".join(problems))
                                continue
                            a["status"] = status
                            a["source"] = f"{a.get('source', '')}; {status} by you {stamp}".strip("; ")
                            found += 1
        if section == "alt" and not found:
            raise ValueError("That version isn't in your facts file.")
        engine.save_facts(path, facts)
        return facts_view(profile)
    if section == "new_line":  # a line you write, for work a job asks for that your lines don't show yet
        g = next((g for grp in ("roles", "projects") for g in facts[grp] if g["id"] == str(d.get("group") or "") and engine.ok(g)), None)
        if g is None:
            raise ValueError("Pick one of your roles or projects.")
        clean = " ".join(str(text or "").split())
        if not 20 <= len(clean) <= 450:
            raise ValueError("Write the line in 20 to 450 characters.")
        if any(x in clean for x in engine.BANNED):
            raise ValueError("Use a comma or a period instead of a dash.")
        if engine.NEVER.search(clean):
            raise ValueError(f'"{engine.NEVER.search(clean).group(0)}" never goes on your resume.')
        if "[" in clean or "]" in clean:
            raise ValueError("Fill in the [blanks] with your own facts first.")
        # a line you already have, word for word or nearly, isn't added twice: that one is used instead
        roots = engine._ask_roots(clean)
        twin = next((b for b in g["bullets"] if engine.ok(b) and (b["text"] == clean or (roots and len(roots & engine._ask_roots(b["text"]))
                     / len(roots | engine._ask_roots(b["text"])) >= 0.8))), None)
        if twin is None:
            n = 1 + sum(b["id"].startswith(f"{g['id']}_you") for b in g["bullets"])
            why = str(d.get("requirement") or "")[:160]
            twin = {"id": f"{g['id']}_you{n}", "text": clean, "status": "approved",
                    "source": f"written by you {stamp}" + (f", for: {why}" if why else "")}
            g["bullets"].append(twin)
            engine.save_facts(path, facts)
        return {**facts_view(profile), "added": twin["id"]}  # the page pins it to the resume you're tailoring
    if section == "detail":  # a supporting fact for one of your lines: rewrites may use it for any job
        b = next((b for grp in ("roles", "projects") for g in facts[grp] for b in g["bullets"] if b["id"] == ident), None)
        if b is None or not engine.ok(b):
            raise ValueError("That isn't one of your approved lines.")
        details = b.setdefault("supporting_facts", [])
        if status == "removed":
            i = d.get("index")
            if not isinstance(i, int) or not 0 <= i < len(details):
                raise ValueError("That detail isn't on this line.")
            details[i]["status"] = "removed"
        else:
            clean = " ".join(str(text or "").split())
            if not 10 <= len(clean) <= 300:
                raise ValueError("Keep a detail between 10 and 300 characters.")
            if any(x in clean for x in engine.BANNED):
                raise ValueError("Use a comma or a period instead of a dash.")
            if engine.NEVER.search(clean):
                raise ValueError(f'"{engine.NEVER.search(clean).group(0)}" never goes on your resume.')
            if any(x.get("text") == clean and engine.ok(x) for x in details):
                raise ValueError("This line already has that detail.")
            details.append({"text": clean, "status": "approved", "source": f"added by you {stamp}"})
        engine.save_facts(path, facts)
        return facts_view(profile)
    if section == "impact":
        entry = next((i for i in facts.get("impacts") or [] if i["id"] == ident), None)
        if entry is None or status is None or text is not None:
            raise ValueError("An impact sentence can be approved or removed here.")
        entry["status"] = status
        engine.save_facts(path, facts)
        return facts_view(profile)
    if section == "added_skill":
        term, level = str(d.get("term", "")), d.get("level")
        if term not in engine.vocabulary():
            raise ValueError("That skill isn't one the tailoring knows.")
        if level not in ("used", "learning", "no", "removed"):
            raise ValueError("Answer yes, learning or no.")
        added = facts.setdefault("added_skills", [])
        entry = next((a for a in added if a["term"] == term), None)
        if entry is None:
            entry = {"id": "as_" + term, "term": term, "label": engine.item_label(term)}
            added.append(entry)
        if level == "removed":
            entry["status"] = "removed"
        else:
            entry.update(level=level, status="approved", source=f"{'you said no' if level == 'no' else 'added by you'} {stamp}")
            # where you used it: the rewrite may then name it in those roles' and projects' lines, for every job
            places = {g["id"] for grp in ("roles", "projects") for g in facts[grp]}
            where = [w for w in d.get("where") or [] if isinstance(w, str) and w in places]
            if level == "used" and where:
                entry["where"] = sorted(set(entry.get("where") or []) | set(where))
            elif level != "used":
                entry.pop("where", None)
        engine.save_facts(path, facts)
        return facts_view(profile)
    if section in ("contact", "titles"):
        entry = facts[section]
    elif section in ("summaries", "skills"):
        entry = next((x for x in facts[section] if x["id"] == ident), None)
    elif section == "education":
        entry = facts["education"][int(ident)] if str(ident).isdigit() and int(ident) < len(facts["education"]) else None
    elif section in ("roles", "projects"):
        entry = next((g for g in facts[section] if g["id"] == ident), None)
    elif section == "bullet":
        entry = next((b for grp in ("roles", "projects") for g in facts[grp] for b in g["bullets"] if b["id"] == ident), None)
    else:
        entry = None
    if entry is None:
        raise ValueError("That line isn't in your facts file.")
    if text is not None:
        if section not in ("bullet", "summaries"):
            raise ValueError("Only bullets and summaries can be reworded here.")
        clean = " ".join(str(text).split())
        if any(x in clean for x in engine.BANNED):
            raise ValueError("Use a comma or a period instead of a dash.")
        if not 10 <= len(clean) <= 450:
            raise ValueError("Keep a line between 10 and 450 characters.")
        entry["text"] = clean
        entry["status"] = "approved"
        entry["source"] = f"{entry.get('source', '')}; reworded by you {date.today().isoformat()}".strip("; ")
    if status:
        entry["status"] = status
    engine.save_facts(path, facts)
    return facts_view(profile)


# ---------- every job at once (python -m tailor all) ----------

def tailor(item: dict, profile: dict, text: str | None = None, rewrite: bool = False) -> str:
    """Tailor and keep one job's resume as the engine picks it, with no changes from you (python -m tailor all).
    `text`: the job description, when it's kept somewhere other than the job boards (a saved list keeps it after the
    posting closes); `rewrite`: with Claude's rewrites of your points, as a preview has them."""
    facts = engine.load_facts(_facts_path(profile))
    jd = jobs.posting_text(item["url"]) if text is None else text
    rewrites = None  # as the engine picks it: your own wording
    if rewrite:
        _, rewrites, note, _ = _rewritten(facts, item["title"], jd, item["company"], item["resume"], profile)
        if note:
            print(f"  ! #{item['n']} {item['company']}: {note}")
    chrome = profile.get("chrome_path") or CHROME_DEFAULT
    with engine.printing(chrome):  # one Chrome for this resume's prints
        out = engine.build(facts, jd, item["title"], item["resume"], RESULTS / "tailor-preview" / engine.new_preview_id(), None,
                           chrome, employer=item["company"], rewrites=rewrites)
    if out["failures"] or not out["pdf"]:
        raise ValueError("; ".join(out["failures"]) or "not built")
    folder = tailored_folder(profile) / date.today().isoformat() / job_folder_name(item)
    root = profile.get("tailored_copy_dir")
    copy_folder = Path(str(root)).expanduser() / date.today().isoformat() / folder.name if root else None
    path, note = engine.keep(out["pdf"], folder, copy_folder)
    if note:
        print(f"  ! #{item['n']} {item['company']}: {note}")
    return path


def tailored_folder(profile: dict):
    """Where tailored resumes go: profile `tailored_dir` (for example a folder in Documents), else results/tailored."""
    return Path(str(profile.get("tailored_dir") or RESULTS / "tailored")).expanduser()


def job_folder_name(item: dict) -> str:
    """'03 Snowflake - Business Intelligence Analyst': safe as a folder name; the PDF inside keeps a clean name."""
    raw = f"{int(item['n']):02d} {item['company']} - {item['title']}"
    return " ".join(re.sub(r"[/\\:*?\"<>|\x00-\x1f]+", " ", raw).split())[:120].rstrip(" .")


def tailor_all(profile: dict, items: list[dict]) -> list[str]:
    """Build a tailored resume for each item (on request only; the page tailors one job at a time)."""
    made = []
    for it in items:
        try:
            pdf = Path(tailor(it, profile))
        except (ValueError, subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
            print(f"  ! #{it['n']} {it['company']}: {exc}")
            continue
        made.append(str(pdf))
    return made


# ---------- one job, from the command line (python -m tailor job) ----------

def profile_port(profile: dict, key: str = "tailor_port", default: int = 8766) -> int | None:
    """A port from profile.yaml (tailor_port or shortlist_port), read as Shortlist reads tailor_port for its cards'
    links (shortlist.apply.tailor_url), so the two agree: `default` when it isn't set, None when it isn't a port."""
    try:
        port = int(profile.get(key) or default)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


CHOICES = RESULTS / "job-choices.json"  # per job: the lines you pinned on or took off, kept for every rebuild


def job_choices(url: str, include=(), exclude=(), reset: bool = False) -> dict:
    """The lines you pinned (`include`) or took off (`exclude`) for one job, saved so every later build of that job
    keeps them; new ones are added to what was saved, and a line pinned again comes off the other list. `reset` clears
    them. Returns {"include": [...], "exclude": [...]}."""
    try:
        saved = json.loads(CHOICES.read_text())
    except (OSError, ValueError):
        saved = {}
    mine = {"include": [], "exclude": []} if reset else dict(saved.get(url) or {"include": [], "exclude": []})
    for key, other, new in (("include", "exclude", include), ("exclude", "include", exclude)):
        for bid in new:
            if bid not in mine[key]:
                mine[key] = list(mine.get(key) or []) + [bid]
            mine[other] = [x for x in mine.get(other) or [] if x != bid]
    if include or exclude or reset:
        saved[url] = mine
        CHOICES.parent.mkdir(parents=True, exist_ok=True)
        tmp = CHOICES.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved, indent=1))
        tmp.replace(CHOICES)
    return {"include": list(mine.get("include") or []), "exclude": list(mine.get("exclude") or [])}


def tailor_job(item: dict, profile: dict, rewrite: bool = True, include=(), exclude=(), reset: bool = False) -> dict:
    """What the page's preview and Save PDF do for one job, with the page's first choices: Claude's rewrites of your
    points (when Claude Code is installed and signed in, and rewrite_points isn't false), each kept only when it passes
    the same fact checks, and the PDF saved only when every check passes, to the same folders as Save PDF. `item`: a
    job from a saved list (jobs.list_job; its saved description is used) or one jobs.find knows. A summary of what was
    made comes back, with the link to the Tailor page for this job, where you can change it."""
    facts = engine.load_facts(_facts_path(profile))
    text = str(item.get("text") or "")
    if len(text) < 200:
        text = jobs.posting_text(item["url"])
    if len(text) < 200:
        raise ValueError("The job description for this posting isn't saved. Paste it on the Tailor page (python -m tailor), "
                         "under Tailor a job.")
    company, title = str(item.get("company") or ""), str(item.get("title") or "")
    known = {b["id"] for grp in ("roles", "projects") for g in facts[grp] for b in g["bullets"]}
    unknown = sorted((set(include) | set(exclude)) - known)
    if unknown:
        raise ValueError("Not one of your lines: " + ", ".join(unknown) + " (each line's id is in your facts file).")
    chosen = job_choices(item["url"], include, exclude, reset)
    r = _preview(facts, (company, title, int(item.get("n") or 0), item.get("resume") or variant_for_title(title), text),
                 {"rewrite": rewrite, **chosen}, profile)
    path, note = "", ""
    if r["ok"]:  # every check passed: saved as Save PDF saves it
        kept = resume_save({"id": r["id"]}, profile)
        path, note = kept["path"], kept["note"]
    reqs = [{k: q.get(k) for k in ("id", "text", "kind", "status", "where", "gaps")} for q in r["requirements"]]
    required = [q for q in reqs if q["kind"] == "required"]
    return {"job": {k: item.get(k) for k in ("n", "company", "title", "url", "list")},
            "path": path, "note": note, "pages": r["pages"], "failures": r["failures"], "title_line": r["title_line"],
            "summary": (r["summary"] or {}).get("text", ""),
            "rewritten": [{"id": b["id"], "original": r["originals"].get(b["id"], ""), "text": b["text"]}
                          for g in r["experience"] + r["projects"] for b in g["bullets"] if b.get("version") == "job"],
            "rejected": r["rejected"], "repaired": r["repaired"], "rewrite_note": r["rewrite_note"],
            "requirements": reqs, "required_shown": sum(q["status"] == "full" for q in required),
            "required_total": len(required),
            "skills_to_answer": [{"term": x["term"], "label": x["label"]} for x in r["skills_detail"] if x["state"] == "missing"],
            "learning": [x["label"] for x in r["skills_detail"] if x["state"] == "learning"],
            "assumed": r.get("assumed") or [], "generated": r.get("generated") or [],
            "logistics": r["logistics"], "advice": r["advice"],
            "page_url": f"http://127.0.0.1:{profile_port(profile) or 8766}/?job={quote(item['url'], safe='')}"}


# ---------- the local page ----------

def page_data(profile: dict, token: str, query: str = "") -> dict:
    """What the page starts with (page.html): the job asked for as /?job=<posting url> when Shortlist's files know it
    (looked up there only, never fetched), and today's queue minus the jobs you applied to or skipped."""
    job, note = None, ""
    asked = parse_qs(query, keep_blank_values=True).get("job")
    if asked is not None:
        url = _job_url(asked[0])  # the first one only
        item = jobs.find(url) if url else None
        if item:
            job = {**item, "resume": item["resume"] or variant_for_title(item["title"]),
                   "has_text": len(jobs.posting_text(url)) >= 200}
        else:
            note = "That job isn't in Shortlist's latest results. Paste it below."
    shortlist_port = profile_port(profile, "shortlist_port", 8765) or 8765  # a bad port: Shortlist's default
    return {"token": token, "facts": bool(profile.get("resume_facts")), "job": job, "job_note": note,
            "jobs": [{**j, "resume": j["resume"] or variant_for_title(j["title"])} for j in jobs.open_jobs()],
            "shortlist_url": f"http://127.0.0.1:{shortlist_port}/", "warm": WARM["note"]}


def make_server(profile: dict, port: int = 8766) -> tuple[ThreadingHTTPServer, str, str]:
    """Tailor's page on 127.0.0.1:`port`, bound but not serving yet: (the server, its address, the run's token, which
    every write must send). OSError when the port is taken; port 0 takes a free one."""
    token = secrets.token_urlsafe(24)
    hosts: set[str] = set()  # filled in below, once the port is bound and before anything is served
    origins: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        server_version = "tailor"

        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                             "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self):
            return self.headers.get("Host", "") in hosts

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b'{"error":"forbidden"}')
            parts = urlsplit(self.path)
            if parts.path == "/":
                return self._send(200, page.html(page_data(profile, token, parts.query)).encode(), "text/html; charset=utf-8")
            return self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self._host_ok() or self.headers.get("X-Token") != token or (origin and origin not in origins):
                return self._send(403, b'{"error":"forbidden"}')
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length > 200_000:
                return self._send(413, b'{"error":"too large"}')
            if length < 0:  # never read to the end of a body of no stated size
                return self._send(400, b'{"error":"bad request"}')
            try:
                d = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(d, dict):
                    raise ValueError("Send a JSON object.")
                if self.path == "/api/resume/preview":
                    return self._send(200, json.dumps(resume_preview(d, profile)).encode())
                if self.path == "/api/resume/suggest":
                    return self._send(200, json.dumps(resume_suggest(d, profile)).encode())
                if self.path == "/api/resume/save":
                    return self._send(200, json.dumps(resume_save(d, profile)).encode())
                if self.path == "/api/facts/get":
                    return self._send(200, json.dumps(facts_view(profile)).encode())
                if self.path == "/api/facts/update":
                    return self._send(200, json.dumps(facts_update(d, profile)).encode())
                return self._send(404, b'{"error":"not found"}')
            except (ValueError, KeyError, subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
                return self._send(400, json.dumps({"error": str(exc)}).encode())

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    port = httpd.server_address[1]
    hosts.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    origins.update(f"http://{h}" for h in hosts)
    return httpd, f"http://127.0.0.1:{port}/", token


def _is_tailor(port: int) -> bool:
    """Whether the program holding 127.0.0.1:`port` is Tailor, from an earlier run: its Server header starts with
    "tailor". Asked directly (no proxy, no redirects), waiting at most PROBE_S seconds."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=PROBE_S)
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        if not (resp.getheader("Server") or "").lower().startswith("tailor"):
            return False
        try:
            resp.read()  # let it finish sending the page, so it has no broken connection to report
        except (OSError, http.client.HTTPException):
            pass
        return True
    except (OSError, http.client.HTTPException):  # no answer in time, or not one in HTTP
        return False
    finally:
        conn.close()


def serve(profile: dict, port: int = 8766, open_browser: bool = True) -> None:
    """Tailor's page at http://127.0.0.1:`port` until Ctrl+C. Always that port, never the next free one, since
    Shortlist's cards link to it: when Tailor already runs there, this opens that one instead of starting another;
    when another program holds the port, this stops and says so."""
    try:
        httpd, url, _ = make_server(profile, port)
    except OSError:
        url = f"http://127.0.0.1:{port}/"
        if not _is_tailor(port):
            raise SystemExit(f"Port {port} is in use by another program. Start Tailor with --port.")
        print(f"Tailor is already running at {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return
    if profile.get("resume_facts"):  # rewrite the next few jobs in the background so a preview is quick
        threading.Thread(target=warm_rewrites, args=(profile,), name="warm-rewrites", daemon=True).start()
        threading.Thread(target=watch_shortlist, name="watch-shortlist", daemon=True).start()
        kick_warmup()
        relearn_if_stale(profile)
    print(f"\nTailor: {url}  (Ctrl+C to stop; nothing is saved until you press Save PDF)", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
