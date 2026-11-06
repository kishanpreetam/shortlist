"""CLI.

  python -m tailor                                  open the Tailor page at http://127.0.0.1:8766 (same as `serve`)
  python -m tailor serve [--port N] [--no-browser]  the page on port N (default: tailor_port in profile.yaml, else 8766)
  python -m tailor job 7                            tailor job #7 on the newest saved list (python -m shortlist list) and
                                                    save its PDF when every check passes, as the page's Save PDF does
  python -m tailor job 7 --day 2026-10-29           #7 on that day's list
  python -m tailor job --url URL                    any job Shortlist knows: its queue, a saved list or its latest scan
                    [--no-rewrite] [--json]         your own wording without Claude; the summary as JSON
  python -m tailor skill Kafka used --where am,fc   answer the page's skills question once (used, learning or no)
  python -m tailor skill --places                   your role and project ids, for --where
  python -m tailor all [--spares]                   build a resume for every job in Shortlist's queue, as the engine picks it
  python -m tailor lexicon                          learn the tools postings name beyond the built-in list, and list them

Every command takes --profile FILE (default: profile.yaml).
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

from shortlist.roles import ROLE_BY_KEY

from . import app, jobs, lexicon
from . import resume as engine
from .paths import ROOT


def load_profile(path: Path | None = None) -> dict:
    path = path or ROOT / "profile.yaml"
    if not path.exists():
        print("profile.yaml not found; using profile.example.yaml", file=sys.stderr)
        path = ROOT / "profile.example.yaml"
    profile = yaml.safe_load(path.read_text())
    profile.setdefault("roles", list(ROLE_BY_KEY))
    engine.never_words(profile.get("resume_never_words"))
    engine.use_lexicon(lexicon.load() if profile.get("learn_keywords", True) else None)
    return profile


ROW_NAMES = {"code": "Programming", "data": "Data & Pipelines", "cloud": "Cloud & Deployment", "ml": "ML & Analytics",
             "ai": "AI & Agentic Systems"}  # the skills rows a learned tool joins (the kinds in resume.CATEGORY)


def cmd_lexicon(args) -> int:
    """Learns the tools postings name beyond the built-in list (lexicon.py), saves them, and lists them."""
    profile = load_profile(args.profile)
    before = set((lexicon.load() or {}).get("terms") or {})
    lex = lexicon.update(profile)
    tools = {k: t for k, t in lex["terms"].items() if t.get("status") != "removed"}  # a removed tool stays off
    new = {k for k in tools if k not in before}
    print(f"{len(tools)} tools learned in all, {len(new)} new this time, "
          f"from {lex['reads']} reads of postings and {lex['postings']:,} postings on the job boards")
    if tools:
        print(f"{'tool':<30} {'skills row':<20} {'postings':>8} {'employers':>9}")
    for k, t in sorted(tools.items(), key=lambda kv: (-kv[1].get("postings", 0), kv[0])):
        print(f"{(t.get('label') or k)[:30]:<30} {ROW_NAMES.get(t.get('category'), ROW_NAMES['data']):<20} "
              f"{t.get('postings', 0):>8} {t.get('employers', 0):>9}" + ("  new" if k in new else ""))
    skipped = list(lex["skipped"].items())
    if skipped:
        print(f"\nleft out ({len(skipped)} candidates):")
        for name, why in skipped[:20]:
            print(f"  {name[:28]:<28} {why}")
        if len(skipped) > 20:
            print(f"  ...and {len(skipped) - 20} more in {lexicon.PATH}")
    print(f"saved to {lexicon.PATH}")
    return 0


def profile_port(profile: dict) -> int:
    """tailor_port from profile.yaml, read as Shortlist reads it for its cards' links (shortlist.apply.tailor_url), so
    the two always agree: 8766 when it's missing or isn't a port."""
    port = app.profile_port(profile)
    if port is None:
        print("tailor_port in profile.yaml isn't a port from 1 to 65535; using 8766", file=sys.stderr)
    return port or 8766


def cmd_serve(args) -> int:
    profile = load_profile(args.profile)
    app.serve(profile, port=args.port or profile_port(profile), open_browser=not args.no_browser)
    return 0


def cmd_all(args) -> int:
    profile = load_profile(args.profile)
    items = [i for i in jobs.queue()["items"] if args.spares or not i.get("spare")]
    made = app.tailor_all(profile, items)
    print(f"built {len(made)} tailored resumes in {app.tailored_folder(profile)}")
    return 0


def _fail(reason: str) -> int:
    print(reason, file=sys.stderr)
    return 1


def _list_job(n: int, day: str | None) -> tuple[dict | None, str]:
    """Job #n on a saved list, or why there's none."""
    day = day or next(iter(jobs.days()), None)
    if day is None:
        return None, "No saved list yet. Save today's with: python -m shortlist list"
    doc = jobs.saved(day)
    if doc is None:
        if day in jobs.days():
            return None, f"The list saved for {day} can't be read ({jobs.LISTS / day / 'jobs.json'})."
        return None, f"There's no list saved for {day}. Saved lists: {', '.join(jobs.days()[:7]) or 'none'}."
    item = jobs.list_job(n, day)
    if item is None:
        ns = [i["n"] for i in doc["items"] if isinstance(i.get("n"), int)]
        return None, f"There's no job #{n} on the {day} list" + (f" (it has #{min(ns)} to #{max(ns)})." if ns else "; it's empty.")
    return item, ""


def cmd_job(args) -> int:
    """Tailors one job as the page's preview does with its first choices, saves the PDF when every check passes (as
    Save PDF does), and says what's on it and what's missing. Exit 1 when it can't: the reason goes to stderr."""
    profile = load_profile(args.profile)
    if args.url is not None:
        url = app._job_url(args.url)
        item = jobs.find(url) if url else None
        why = ("That job isn't in Shortlist's queue, saved lists or latest scan. Paste it on the Tailor page "
               "(python -m tailor), under Tailor a job.")
    else:
        item, why = _list_job(args.n, args.day)
    if item is None:
        return _fail(why)
    try:
        ids = lambda v: [x.strip() for x in (v or "").split(",") if x.strip()]
        out = app.tailor_job(item, profile, rewrite=not args.no_rewrite, include=ids(args.include), exclude=ids(args.exclude),
                             reset=args.reset)
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        return _fail(str(exc))
    print(json.dumps(out, indent=1) if args.json else summary_text(out))
    return 0 if out["path"] else 1


def summary_text(out: dict) -> str:
    """tailor_job's summary, to read: the saved PDF, what the page shows of the job's requirements, each rewritten
    line, the skills to answer, what to check yourself, and the link to change it on the page."""
    job, lines = out["job"], []
    lines.append((f"#{job['n']} " if job.get("n") else "") + f"{job.get('company') or ''}, {job.get('title') or ''}"
                 + (f" (the {job['list']} list)" if job.get("list") else ""))
    if out["path"]:
        lines.append(f"Saved: {out['path']}")
        if out["note"]:
            lines.append(f"Phone copy: {out['note']}")
    else:
        lines.append("Nothing was saved, because a check failed: " + "; ".join(out["failures"] or ["the page didn't print"]))
    if out["pages"]:
        lines.append(f"Pages: {out['pages']}")
    if out["title_line"]:
        lines.append(f"Title: {out['title_line']}")
    if out["summary"]:
        lines.append(f"Summary: {out['summary']}")
    if out["rewrite_note"]:
        lines.append(f"Note: {out['rewrite_note']}")
    reqs = out["requirements"]
    if reqs:
        partly = sum(q["kind"] == "required" and q["status"] == "partial" for q in reqs)
        lines.append(f"\nRequired: {out['required_shown']} of {out['required_total']} on the page"
                     + (f" ({partly} more partly)" if partly else "") + f"; {sum(q['status'] == 'full' for q in reqs)} of {len(reqs)} in all")
        order = {"required": 0, "responsibility": 1, "preferred": 2}
        for q in sorted((q for q in reqs if q["status"] != "full"), key=lambda q: order.get(q["kind"], 3)):
            short = f" (short: {'; '.join(q['gaps'])})" if q["status"] == "partial" and q.get("gaps") else ""
            lines.append(f"  {'partly' if q['status'] == 'partial' else 'missing'} ({q['kind']}): {q['text']}{short}")
    else:
        lines.append("\nRequirements: none read (Claude reads them from the posting), so none were checked.")
    if out["rewritten"]:
        lines.append(f"\nRewritten for this job ({len(out['rewritten'])}; your wording, then the new wording):")
        for x in out["rewritten"]:
            lines += [f"  yours: {x['original']}", f"  new:   {x['text']}"]
    if out["repaired"]:
        lines.append(f"{out['repaired']} of these passed the fact check on a second try.")
    if out["rejected"]:
        lines.append(f"{out['rejected']} rewrite{'s' if out['rejected'] != 1 else ''} changed a fact, so your own wording stays there.")
    if out.get("generated"):
        lines.append(f"\nNew points written for this job ({len(out['generated'])}, from its requirements):")
        lines += [f"  {x['where']}: {x['text']}" for x in out["generated"]]
    if out.get("assumed"):
        lines.append("\nAdded to your skills from this job (asking for its resume says you have them; no experience line "
                     "claims them): " + ", ".join(out["assumed"]))
    if out.get("learning"):
        lines.append("\nOn your Currently learning line (skills this job asks for that you haven't said you used): "
                     + ", ".join(out["learning"]))
    ask = out["skills_to_answer"]
    if ask:
        lines.append(f"\n{'Skills' if len(ask) > 1 else 'A skill'} this job asks for that your resume doesn't show: "
                     + ", ".join(x["label"] for x in ask) + f". Have you used {'them' if len(ask) > 1 else 'it'}? Answer once with:")
        lines.append(f"  python -m tailor skill \"{ask[0]['label']}\" used|learning|no  "
                     "(with used, --where ID,ID says where; python -m tailor skill --places lists them)")
        lines.append("  then run this command again to rebuild the resume with your answers.")
    if out["logistics"]:
        lines.append("\nCheck these yourself, no resume line shows them: " + "; ".join(x["text"] for x in out["logistics"]))
    if out["advice"]:
        words = {"change": "Change", "ok": "Good"}  # as the page heads each piece of advice
        lines += [""] + [f"{words.get(a.get('level'), 'Note')}: {a['text']}" for a in out["advice"]]
    lines.append(f"\nChange it on the Tailor page: {out['page_url']}  (start it with python -m tailor if it isn't running)")
    return "\n".join(lines)


def skill_term(text: str) -> str | None:
    """A skill as the page's question names it: its key in the engine's vocabulary ("power_bi") or its label in any
    case ("Power BI"), with or without spaces and punctuation ("power-bi"). None when Tailor doesn't know it."""
    vocab = engine.vocabulary()
    want = " ".join(str(text).split()).lower()
    names = {t: {t.lower(), engine.label(t).lower(), engine.item_label(t).lower()} for t in vocab}
    exact = [t for t, ns in names.items() if want in ns]
    if exact:
        return exact[0]
    squash = lambda s: re.sub(r"[^a-z0-9+#]", "", s)
    close = [t for t, ns in names.items() if squash(want) in {squash(x) for x in ns}]
    return close[0] if len(close) == 1 else None


def _places(facts: dict) -> list[tuple[str, str]]:
    """Your approved roles and projects, as the page lists them for "where did you use it?": (id, name)."""
    return [(g["id"], g.get("org") or g.get("title", "")) for grp in ("roles", "projects") for g in facts[grp] if engine.ok(g)]


def cmd_skill(args) -> int:
    """Answers the page's skills question for one skill, the same way the page saves it (facts_update, added_skill):
    used (with where, if you say), learning, or no. Saved in your facts file and used for every later job."""
    profile = load_profile(args.profile)
    try:
        facts = engine.load_facts(app._facts_path(profile))
    except (ValueError, OSError) as exc:
        return _fail(str(exc))
    places = _places(facts)
    if args.places:
        for pid, name in places:
            print(f"{pid:<14} {name}")
        return 0
    term = skill_term(args.term)
    if term is None:
        labels = sorted({engine.item_label(t) for t in engine.vocabulary()})
        near = difflib.get_close_matches(str(args.term), labels, n=3, cutoff=0.6)
        return _fail(f'Tailor doesn\'t know the skill "{args.term}". Use the name a job lists, as the page shows it'
                     + (f" (maybe {', '.join(near)})." if near else "."))
    where = [w.strip() for w in (args.where or "").split(",") if w.strip()]
    if where and args.level != "used":
        return _fail("--where goes with used: it says where you used the skill.")
    known = dict(places)
    unknown = [w for w in where if w not in known]
    if unknown:
        return _fail(f"None of your roles or projects has the id {', '.join(unknown)}. "
                     "See them with: python -m tailor skill --places")
    try:
        app.facts_update({"section": "added_skill", "term": term, "level": args.level, "where": where}, profile)
    except (ValueError, OSError) as exc:
        return _fail(str(exc))
    said = {"used": "yes, " + (f"at {', '.join(known[w] for w in where)}" if where else "on your skills line"),
            "learning": "learning, on your Currently learning line", "no": "no, so it isn't asked again"}[args.level]
    after = "" if args.level == "no" else " Every later job that asks for it gets it; run the job command again to rebuild one."
    print(f"Saved: {engine.item_label(term)}: {said}.{after}")
    return 0


def _port(text: str) -> int:
    try:
        port = int(text)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("use a port from 1 to 65535")
    return port


def _day(text: str) -> str:
    try:
        real = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-\d{1,2})?", text)) and bool(date.fromisoformat(text[:10]))
    except ValueError:
        real = False
    if not real:
        raise argparse.ArgumentTypeError("use a list's day as YYYY-MM-DD, or YYYY-MM-DD-2 for a later list that day")
    return text


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv = ["serve", *argv]  # no command: open the page
    ap = argparse.ArgumentParser(prog="tailor", description="A one-page resume for one job posting, from lines you approved.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="open the Tailor page (the default)")
    sv.add_argument("--port", type=_port, help="port on 127.0.0.1 (default: tailor_port in profile.yaml, else 8766)")
    sv.add_argument("--no-browser", action="store_true", help="start the page without opening a browser")
    sv.add_argument("--profile", type=Path, help="profile file (default: profile.yaml)")
    jb = sub.add_parser("job", help="tailor one job from a saved list, or by its URL, and save its PDF")
    jb.add_argument("n", nargs="?", type=int, help="the job's number on the list")
    jb.add_argument("--day", type=_day, help="the list's day, YYYY-MM-DD (default: the newest list)")
    jb.add_argument("--url", help="any job Shortlist knows instead: in its queue, a saved list or its latest scan")
    jb.add_argument("--no-rewrite", action="store_true", help="keep your own wording; Claude isn't asked")
    jb.add_argument("--include", help="line ids to pin on this job's page, comma-separated (kept for later builds)")
    jb.add_argument("--exclude", help="line ids to take off this job's page, comma-separated (kept for later builds)")
    jb.add_argument("--reset", action="store_true", help="forget this job's pinned and removed lines")
    jb.add_argument("--json", action="store_true", help="print the summary as JSON")
    jb.add_argument("--profile", type=Path)
    sk = sub.add_parser("skill", help="answer the page's skills question for one skill: used, learning or no")
    sk.add_argument("term", nargs="?", help='the skill as a job names it ("Kafka") or its key ("kafka")')
    sk.add_argument("level", nargs="?", choices=("used", "learning", "no"))
    sk.add_argument("--where", help="with used: the ids of the roles and projects where you used it, comma-separated")
    sk.add_argument("--places", action="store_true", help="list your role and project ids, for --where")
    sk.add_argument("--profile", type=Path)
    al = sub.add_parser("all", help="build a tailored resume for every job in Shortlist's queue")
    al.add_argument("--spares", action="store_true", help="include the spares too")
    al.add_argument("--profile", type=Path)
    lx = sub.add_parser("lexicon", help="learn the tools postings name beyond the built-in list")
    lx.add_argument("--profile", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        return cmd_serve(args)
    if args.cmd == "job":
        if (args.n is None) == (args.url is None):
            jb.error("give a job number from a saved list, or --url URL")
        if args.day and args.url is not None:
            jb.error("--day goes with a job number")
        return cmd_job(args)
    if args.cmd == "skill":
        if not args.places and not (args.term and args.level):
            sk.error("give a skill and used, learning or no (or --places)")
        return cmd_skill(args)
    if args.cmd == "all":
        return cmd_all(args)
    if args.cmd == "lexicon":
        return cmd_lexicon(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
