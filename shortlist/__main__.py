"""CLI.

  python -m shortlist setup                    download the public data files (once)
  python -m shortlist discover                 find more employers that sponsor your roles
  python -m shortlist run                      scan every employer, score, write results/report.html
  python -m shortlist run --roles data_engineer,ai_engineer
  python -m shortlist sponsors                 look up H-1B filings per employer (h1bdata.info, cached a week)
  python -m shortlist lexicon                  learn the tools postings name beyond the built-in list, and list them
  python -m shortlist lca FILE.xlsx [FILE.xlsx]  import the DOL file instead (takes precedence)
  python -m shortlist log --company HubSpot --title "Data Engineer" --role data_engineer --channel referral --outcome screen
  python -m shortlist today                    pick today's apply queue and open it at http://127.0.0.1:8765
  python -m shortlist applied URL|N            log an application (URL, or its number in today's queue)
  python -m shortlist update URL --outcome screen
  python -m shortlist followups                follow-ups due today, with the message to send
  python -m shortlist phone-export --out q.json  today's queue and tracker state for a phone page
  python -m shortlist phone-import DIR           record the marks a phone page saved
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from . import apply, discover, h1bdata, lca, lexicon, outcomes, phone, report, resume
from .boards import FETCHERS, Posting, host_for
from .paths import DATA, ROOT
from .roles import ROLE_BY_KEY
from .score import Company, now_utc, score_posting, summarize_roles


def load_profile(path: Path | None = None) -> dict:
    path = path or ROOT / "profile.yaml"
    if not path.exists():
        print("profile.yaml not found; using profile.example.yaml", file=sys.stderr)
        path = ROOT / "profile.example.yaml"
    profile = yaml.safe_load(path.read_text())
    profile.setdefault("roles", list(ROLE_BY_KEY))
    resume.never_words(profile.get("resume_never_words"))
    resume.use_lexicon(lexicon.load() if profile.get("learn_keywords", True) else None)
    return profile


def load_companies(profile: dict) -> list[Company]:
    raw = yaml.safe_load((Path(__file__).parent / "companies.yaml").read_text())
    if discover.OUT.exists():  # employers found by `discover`; curated entries win
        for d in yaml.safe_load(discover.OUT.read_text()) or []:
            raw.setdefault(d["name"], {"boards": [[d["ats"], d["slug"]]], "h1b": None,
                                       "tags": ["boston"] if d.get("boston", 0) >= 3 else [], "discovered": d})
    contacts = profile.get("contacts", {})
    out = []
    for name, spec in raw.items():
        if name in profile.get("exclude_companies", []):
            continue
        c = Company(name=name, boards=spec["boards"], lca_2026=spec.get("lca_2026"),
                    cap_exempt=spec.get("cap_exempt", False), tags=spec.get("tags", []),
                    h1b=spec.get("h1b"), portal=spec.get("portal"))
        if spec.get("discovered"):
            d = spec["discovered"]
            c.discovered = {"filings": d["filings"], "by_role": d["by_role"], "employers": [d["name"]],
                            "source": "h1bdata.info filings by job title, 2025-26"}
        c.alumni = contacts.get(name, {}).get("alumni", 0)
        c.warm = contacts.get(name, {}).get("warm", False)
        out.append(c)
    return out


BOARD_CACHE = DATA / "boards"
BOARD_TTL_S = 6 * 3600


def _cached_fetch(ats: str, slug: str, company: str, ttl_s: float = BOARD_TTL_S) -> list[Posting]:
    """Job boards are cached for 6 hours (or `ttl_s`) so repeated runs don't re-request them."""
    import json, time
    from datetime import datetime
    BOARD_CACHE.mkdir(parents=True, exist_ok=True)
    path = BOARD_CACHE / f"{ats}__{slug.replace('/', '__')}.json"  # Workday slugs are tenant/wdN/site
    if path.exists() and time.time() - path.stat().st_mtime < ttl_s:
        out = []
        for d in json.loads(path.read_text()):
            d["posted_at"] = datetime.fromisoformat(d["posted_at"]) if d["posted_at"] else None
            d["company"] = company
            out.append(Posting(**d))
        return out
    posts = FETCHERS[ats](slug, company)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps([p.__dict__ | {"posted_at": p.posted_at.isoformat() if p.posted_at else None} for p in posts]))
    tmp.replace(path)  # Tailor reads these while Refresh writes them: whole or not at all
    return posts


def fetch_all(companies: list[Company], ttl_s: float = BOARD_TTL_S, progress=None) -> dict[str, list]:
    """One thread per job-board host, so each host sees at most one request a second. `progress(done, total)` is
    called after each board."""
    by_host: dict[str, list[tuple[Company, str, str]]] = defaultdict(list)
    for c in companies:
        for ats, slug in c.boards:
            by_host[host_for(ats, slug)].append((c, ats, slug))
    results: dict[str, list] = defaultdict(list)
    total, done, lock = sum(len(v) for v in by_host.values()), [0], threading.Lock()

    def run_host(items):
        for c, ats, slug in items:
            try:
                posts = _cached_fetch(ats, slug, c.name, ttl_s)
            except Exception as exc:  # one broken board shouldn't stop the scan
                print(f"  ! {c.name} ({ats}/{slug}): {type(exc).__name__}", file=sys.stderr)
                posts = None
            if posts is not None and len(posts) > len(results[c.name]):
                results[c.name] = posts
            if progress:
                with lock:
                    done[0] += 1
                    progress(done[0], total)

    with ThreadPoolExecutor(max_workers=len(by_host) or 1) as pool:
        list(pool.map(run_host, by_host.values()))
    return results


def sponsor_records(companies: list[Company], quiet: bool = True) -> tuple[dict, str]:
    """{company name: record}: the DOL import if present, else h1bdata.info counts (cached)."""
    dol = lca.load()
    out = {}
    for c in companies:
        if dol is not None:
            rec = dol.get(lca.norm(c.h1b or c.name))
            if rec:
                out[c.name] = rec
            continue
        if getattr(c, "discovered", None):
            out[c.name] = c.discovered
            continue
        try:
            rec = h1bdata.counts(c.h1b or c.name.upper())
            if rec:
                out[c.name] = rec
        except Exception as exc:
            if not quiet:
                print(f"  ! {c.name}: {type(exc).__name__}", file=sys.stderr)
    return out, ("DOL H-1B filings (imported)" if dol is not None else "H-1B filings 2025-26 via h1bdata.info")


def cmd_sponsors(args) -> int:
    profile = load_profile(args.profile)
    companies = load_companies(profile)
    records, source = sponsor_records(companies, quiet=False)
    print(f"source: {source}\n{'company':<24} {'tech filings':>12}  top role families                      matched employer name")
    for c in sorted(companies, key=lambda c: -records.get(c.name, {}).get("filings", 0)):
        r = records.get(c.name)
        if not r:
            print(f"{c.name:<24} {'-':>12}")
            continue
        top = ", ".join(f"{k} {v}" for k, v in sorted(r["by_role"].items(), key=lambda kv: -kv[1])[:3])
        print(f"{c.name:<24} {r['filings']:>12}  {top:<38} {(r.get('employers') or [r.get('employer', '')])[0][:30]}")
    return 0


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


SETUP_FILES = [
    ("https://flag.dol.gov/sites/default/files/wages/OFLC_Wages_2026-27.zip", "OFLC_Wages_2026-27.zip"),
    *[(f"https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/{a}_companies.json", f"{a}_companies.json")
      for a in ("greenhouse", "lever", "ashby")],
]


def cmd_setup(args) -> int:
    """Downloads the public data files: OFLC prevailing wages (DOL) and job-board id lists (MIT-licensed)."""
    import requests
    from .paths import RAW
    RAW.mkdir(parents=True, exist_ok=True)
    for url, name in SETUP_FILES:
        path = RAW / name
        if path.exists() and not args.force:
            print(f"have {name}")
            continue
        resp = requests.get(url, headers={"User-Agent": "shortlist/0.1 (personal job search tool)"}, timeout=120)
        resp.raise_for_status()
        path.write_bytes(resp.content)
        print(f"downloaded {name} ({len(resp.content) / 1e6:.1f} MB)")
    return 0


def scan(profile: dict, ttl_s: float = BOARD_TTL_S, progress=None, say=lambda msg: None) -> dict:
    """Read every employer's job board (a board read in the last `ttl_s` seconds isn't read again), score each
    posting for you, write results/postings.csv, the report and targets, and learn the tools the postings name beyond
    the built-in list (lexicon.py; a failure there prints a warning, never fails the scan). The page's Refresh button
    runs this too. `progress(done, total)` follows the boards; `say` gets the stage."""
    companies = load_companies(profile)
    say(f"looking up H-1B filings for {len(companies)} employers (cached after the first run)...")
    records, data_note = sponsor_records(companies)
    say(f"scanning {len([c for c in companies if c.boards])} job boards...")
    postings = fetch_all(companies, ttl_s, progress)
    by_name = {c.name: c for c in companies}
    now = now_utc()
    mult = outcomes.role_multipliers()
    scored = []
    for name, posts in postings.items():
        for p in posts:
            s = score_posting(p, by_name[name], profile, records.get(name), now)
            if s:
                if s.role.key in mult:
                    s.score *= mult[s.role.key][0]
                scored.append(s)
    roles = summarize_roles(scored, profile)
    manual = [(c, records.get(c.name)) for c in companies if not c.boards]
    report.write(scored, roles, profile, mult, data_note, manual)
    report.write_targets(scored, records, profile, manual)
    if profile.get("learn_keywords", True):
        say("learning tools from the postings...")
        try:
            resume.use_lexicon(lexicon.update(profile))
        except Exception as exc:  # learning is extra; it shouldn't fail a scan that has already written its results
            print(f"  ! learning tools from the postings: {type(exc).__name__}: {exc}", file=sys.stderr)
    return {"postings": postings, "scored": scored, "roles": roles}


def cmd_run(args) -> int:
    profile = load_profile(args.profile)
    if args.roles:
        profile["roles"] = [r.strip() for r in args.roles.split(",")]
    out = scan(profile, say=lambda msg: print(msg, file=sys.stderr))
    postings, scored, roles = out["postings"], out["scored"], out["roles"]

    total = sum(len(v) for v in postings.values())
    eligible = [s for s in scored if not s.excluded]
    print(f"\n{total} postings from {len(postings)} employers; {len(scored)} in your role families; {len(eligible)} realistic\n")
    print(f"{'role family':<40} {'effort':>6} {'eligible':>9} {'top-10':>7} {'lottery':>8}")
    for r in roles:
        lot = f"{round(100 * r['avg_lottery'])}%" if r["avg_lottery"] is not None else "-"
        print(f"{r['role']:<40} {r['effort_share']:>5}% {r['eligible']:>4}/{r['seen']:<4} {r['top10_mean']:>7} {lot:>8}")
    print("\ntop postings:")
    for s in sorted(eligible, key=lambda s: -s.score)[: args.top]:
        print(f"  {s.score:5.1f}  {s.company.name:<16} {s.posting.title[:58]:<58} {s.posting.location[:28]}")
    print(f"\nreport: {ROOT / 'results' / 'report.html'}\ntargets: {ROOT / 'results' / 'targets.md'}")
    return 0


def cmd_today(args) -> int:
    import time
    stale = not apply.POSTINGS.exists() or time.time() - apply.POSTINGS.stat().st_mtime > 6 * 3600
    if stale and not args.no_scan:
        print("results are over 6 hours old; scanning first...", file=sys.stderr)
        cmd_run(argparse.Namespace(profile=args.profile, roles=None, top=0))
    profile = load_profile(args.profile)
    q = apply.make_queue(profile, n=args.n, spares=args.spares, cap=args.cap, max_age=args.max_age,
                         min_score=args.min_score, messages=args.messages, max_age_hours=args.max_age_hours or None)
    main_items = [i for i in q["items"] if not i["spare"]]
    fresh = sum((i.get("age_hours") if i.get("age_hours") is not None else 24 * i["age"]) <= 24 for i in main_items)
    due = apply.followups_due(profile)
    print(f"\ntoday's queue: {len(main_items)} jobs ({fresh} posted in the last 24 hours), "
          f"{len(q['items']) - len(main_items)} spares, {len(due)} follow-ups due")
    for i in main_items:
        print(f"  {i['n']:>2}. {i['company'][:18]:<18} {i['title'][:54]:<54} {i['age']:>3}d  {i['resume']}")
    if len(main_items) < args.n:
        print(f"\nonly {len(main_items)} realistic postings are left to apply to; add employers with "
              "`python -m shortlist discover --min 2`, or raise --cap, --max-age or --max-age-hours")
    if not args.no_serve:
        apply.serve(profile, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_applied(args) -> int:
    rows = {r["url"]: r for r in apply._read(apply.POSTINGS)}
    target = args.target
    if target.isdigit() and apply.TODAY.exists():
        items = json.loads(apply.TODAY.read_text())["items"]
        hit = next((i for i in items if i["n"] == int(target)), None)
        if not hit:
            print(f"no job #{target} in today's queue", file=sys.stderr)
            return 1
        target = hit["url"]
    r = rows.get(target, {})
    company, title = args.company or r.get("company"), args.title or r.get("title")
    if not company or not title:
        print("that URL isn't in the latest scan; add --company and --title", file=sys.stderr)
        return 1
    row = apply.record(company, title, target, args.role or r.get("role", ""), args.channel, args.contact or "")
    print(f"logged: {row['company']}, {row['title']}. Follow up on {row['follow_up_1']} and {row['follow_up_2']}.")
    return 0


def cmd_followups(args) -> int:
    due = apply.followups_due(load_profile(args.profile))
    if not due:
        print("no follow-ups due today")
    for a in due:
        print(f"\n{a['company']}, {a['title']} (applied {a['applied_on']}, follow-up {a['which']})\n  {a['url']}\n  {a['note']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="shortlist")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("setup", help="download the public data files (wages, job-board lists)")
    st.add_argument("--force", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--profile", type=Path, help="profile file (default: profile.yaml)")
    r.add_argument("--roles", help="comma-separated role keys (default: all in profile)")
    r.add_argument("--top", type=int, default=20)
    sp = sub.add_parser("sponsors", help="look up and show H-1B filing counts per employer")
    sp.add_argument("--profile", type=Path)
    lx = sub.add_parser("lexicon", help="learn the tools postings name beyond the built-in list")
    lx.add_argument("--profile", type=Path)
    dsc = sub.add_parser("discover", help="find more employers that sponsor your role families and have readable job boards")
    dsc.add_argument("--min", type=int, default=5, help="minimum 2025-26 filings for your role families")
    dsc.add_argument("--workable", action="store_true", help="also look for employers on Workable, keeping what's found already")
    dsc.add_argument("--limit", type=int, default=600, help="with --workable: most employers to check")
    i = sub.add_parser("lca")
    i.add_argument("files", nargs="+", type=Path)
    lg = sub.add_parser("log")
    for f in ("company", "title", "role", "channel", "outcome"):
        lg.add_argument(f"--{f}", required=True)
    td = sub.add_parser("today", help="pick today's apply queue and open it in your browser")
    td.add_argument("--profile", type=Path)
    td.add_argument("--n", type=int, default=30, help="applications to queue (default 30)")
    td.add_argument("--spares", type=int, default=10, help="extra postings in case you skip some")
    td.add_argument("--cap", type=int, default=2, help="most applications per employer in 60 days")
    td.add_argument("--messages", type=int, default=20, help="jobs that get a note for one person (LinkedIn allows about 100 invitations a week)")
    td.add_argument("--max-age", type=int, default=60, help="skip postings older than this many days")
    td.add_argument("--max-age-hours", type=float, default=24,
                    help="keep only postings published in the last this many hours (default 24; 0 turns it off)")
    td.add_argument("--min-score", type=float, default=3.0)
    td.add_argument("--port", type=int, default=8765)
    td.add_argument("--no-scan", action="store_true", help="use the latest results even if they're old")
    td.add_argument("--no-serve", action="store_true", help="print the queue without starting the page")
    td.add_argument("--no-browser", action="store_true", help="start the page without opening a browser")
    ad = sub.add_parser("applied", help="log an application")
    ad.add_argument("target", help="job URL, or its number in today's queue")
    ad.add_argument("--channel", default="cold", choices=outcomes.CHANNELS)
    ad.add_argument("--contact", help="person you messaged")
    ad.add_argument("--company")
    ad.add_argument("--title")
    ad.add_argument("--role")
    up = sub.add_parser("update", help="set an application's outcome")
    up.add_argument("url")
    up.add_argument("--outcome", required=True, choices=apply.STATUSES)
    fu = sub.add_parser("followups", help="follow-ups due today")
    fu.add_argument("--profile", type=Path)
    ta = sub.add_parser("tailor-all", help="build a tailored resume for every job in today's queue")
    ta.add_argument("--profile", type=Path)
    ta.add_argument("--spares", action="store_true", help="include the spares too")
    pe = sub.add_parser("phone-export", help="write today's queue and tracker state as one JSON document for a phone page")
    pe.add_argument("--profile", type=Path)
    pe.add_argument("--out", type=Path, required=True)
    pe.add_argument("--html", type=Path, help="also write a script-free page (for a phone's Files app)")
    pe.add_argument("--answers", type=Path, help="answer bank JSON to include in the --html page")
    pi = sub.add_parser("phone-import", help="record the marks a phone page saved (a folder of JSON files)")
    pi.add_argument("folder", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "tailor-all":
        profile = load_profile(args.profile)
        items = json.loads(apply.TODAY.read_text())["items"] if apply.TODAY.exists() else []
        items = [i for i in items if args.spares or not i.get("spare")]
        made = apply.tailor_all(profile, items)
        print(f"built {len(made)} tailored resumes in {apply.tailored_folder(profile)}")
        return 0
    if args.cmd == "phone-export":
        doc = phone.export(load_profile(args.profile), args.out)
        if args.html:
            answers = json.loads(args.answers.read_text()) if args.answers and args.answers.exists() else None
            phone.write_static(doc, args.html, answers)
            print(f"wrote {args.html}")
        print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KB): {len(doc['items'])} jobs, "
              f"{len(doc['followups'])} follow-ups, {doc['applied_today']} applied today")
        return 0
    if args.cmd == "phone-import":
        print("imported:", phone.import_marks(args.folder))
        return 0
    if args.cmd == "today":
        return cmd_today(args)
    if args.cmd == "applied":
        return cmd_applied(args)
    if args.cmd == "update":
        hit = apply.update(args.url, args.outcome)
        print(f"updated: {hit['company']}, {hit['title']} is now {args.outcome}" if hit else "no application with that URL")
        return 0 if hit else 1
    if args.cmd == "followups":
        return cmd_followups(args)
    if args.cmd == "setup":
        return cmd_setup(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "sponsors":
        return cmd_sponsors(args)
    if args.cmd == "lexicon":
        return cmd_lexicon(args)
    if args.cmd == "discover":
        raw = yaml.safe_load((Path(__file__).parent / "companies.yaml").read_text())
        known = set(raw) | {v.get("h1b") or "" for v in raw.values()}
        if args.workable:
            added = discover.add_workable(args.min, known=known, limit=args.limit)
            print(f"found {len(added)} more employers on Workable:")
            for d in added:
                print(f"  {d['filings']:>5}  {d['name'][:34]:<34} workable   {d['slug']}")
            print(f"saved to {discover.OUT}; the next `run` scans them")
            return 0
        found = discover.run(args.min, known=known)
        print(f"found {len(found)} more employers with readable job boards:")
        for d in found[:60]:
            top = ", ".join(f"{k} {n}" for k, n in sorted(d["by_role"].items(), key=lambda kv: -kv[1])[:3])
            print(f"  {d['filings']:>5}  {d['name'][:34]:<34} {d['ats']:<10} {d['slug']:<22} {top}")
        print(f"saved to {discover.OUT}; the next `run` scans them")
        return 0
    if args.cmd == "lca":
        rows, employers = lca.import_files(args.files)
        print(f"imported {rows:,} certified H-1B tech filings from {employers:,} employers")
        return 0
    if args.cmd == "log":
        outcomes.log(args.company, args.title, args.role, args.channel, args.outcome)
        print("logged; future runs will adjust that role family once it has 3+ outcomes")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
