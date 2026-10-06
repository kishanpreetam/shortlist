"""CLI.

  python -m shortlist setup                    download the public data files (once)
  python -m shortlist discover                 find more employers that sponsor your roles
  python -m shortlist run                      scan every employer, score, write results/report.html
  python -m shortlist run --roles data_engineer,ai_engineer
  python -m shortlist sponsors                 look up H-1B filings per employer (h1bdata.info, cached a week)
  python -m shortlist lca FILE.xlsx [FILE.xlsx]  import the DOL file instead (takes precedence)
  python -m shortlist log --company HubSpot --title "Data Engineer" --role data_engineer --channel referral --outcome screen
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from . import discover, h1bdata, lca, outcomes, report
from .boards import FETCHERS, Posting
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


def _cached_fetch(ats: str, slug: str, company: str) -> list[Posting]:
    """Job boards are cached for 6 hours so repeated runs don't re-request them."""
    import json, time
    from datetime import datetime
    BOARD_CACHE.mkdir(parents=True, exist_ok=True)
    path = BOARD_CACHE / f"{ats}__{slug}.json"
    if path.exists() and time.time() - path.stat().st_mtime < BOARD_TTL_S:
        out = []
        for d in json.loads(path.read_text()):
            d["posted_at"] = datetime.fromisoformat(d["posted_at"]) if d["posted_at"] else None
            d["company"] = company
            out.append(Posting(**d))
        return out
    posts = FETCHERS[ats](slug, company)
    path.write_text(json.dumps([p.__dict__ | {"posted_at": p.posted_at.isoformat() if p.posted_at else None} for p in posts]))
    return posts


def fetch_all(companies: list[Company]) -> dict[str, list]:
    """One thread per job-board host, so each host sees at most one request a second."""
    by_host: dict[str, list[tuple[Company, str, str]]] = defaultdict(list)
    for c in companies:
        for ats, slug in c.boards:
            by_host[ats].append((c, ats, slug))
    results: dict[str, list] = defaultdict(list)

    def run_host(items):
        for c, ats, slug in items:
            try:
                posts = _cached_fetch(ats, slug, c.name)
            except Exception as exc:  # one broken board shouldn't stop the scan
                print(f"  ! {c.name} ({ats}/{slug}): {type(exc).__name__}", file=sys.stderr)
                continue
            if len(posts) > len(results[c.name]):
                results[c.name] = posts

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


def cmd_run(args) -> int:
    profile = load_profile(args.profile)
    if args.roles:
        profile["roles"] = [r.strip() for r in args.roles.split(",")]
    companies = load_companies(profile)
    print(f"looking up H-1B filings for {len(companies)} employers (cached after the first run)...", file=sys.stderr)
    records, data_note = sponsor_records(companies)
    print(f"scanning {len([c for c in companies if c.boards])} job boards...", file=sys.stderr)
    postings = fetch_all(companies)
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
    dsc = sub.add_parser("discover", help="find more employers that sponsor your role families and have readable job boards")
    dsc.add_argument("--min", type=int, default=5, help="minimum 2025-26 filings for your role families")
    i = sub.add_parser("lca")
    i.add_argument("files", nargs="+", type=Path)
    lg = sub.add_parser("log")
    for f in ("company", "title", "role", "channel", "outcome"):
        lg.add_argument(f"--{f}", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "setup":
        return cmd_setup(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "sponsors":
        return cmd_sponsors(args)
    if args.cmd == "discover":
        raw = yaml.safe_load((Path(__file__).parent / "companies.yaml").read_text())
        found = discover.run(args.min, known=set(raw) | {v.get("h1b") or "" for v in raw.values()})
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
