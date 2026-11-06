"""Today's list: every posting from the last 24 hours that fits your field and your visa status, saved to keep.

    python -m shortlist list              rescan if results are over 6 hours old, then save today's list
    python -m shortlist list --hours 48   a wider window (at most 72 hours)

The picking is the daily queue's (apply.pick and apply.build_items): realistic postings only (your degree's field,
your level, in the US, W-2, sponsorship not ruled out, pay at or above the H-1B minimum), nothing you've applied to or
skipped, at most `cap` per employer and one per job title, startups first, each group ranked by score times
freshness. Unlike the queue it has no daily target: every posting that qualifies is on it.

Each day's list is saved in results/lists/<date>/: jobs.html to read (each title opens the posting), jobs.csv for a
spreadsheet, and jobs.json with each posting's full description, so Tailor can still tailor a resume for it days
later, after the posting has closed or a newer scan has replaced it (python -m tailor job N). Numbers never change:
running it again the same day adds the new postings at the end.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
from collections import Counter
from datetime import date, datetime

from . import apply
from .paths import RESULTS
from .roles import off_field

LISTS = RESULTS / "lists"
MAX_HOURS = 72
CSV_FIELDS = ["n", "group", "company", "title", "location", "posted", "sponsorship", "lottery_odds", "wage_level",
              "pay", "pay_source", "years_asked", "resume", "score", "link"]
GROUPS = {key: label for key, label, _ in apply.FIELDS}
RESUME_NAMES = {"data-engineer": "Data Engineer", "ai-engineer": "AI Engineer", "data-scientist": "Data Scientist",
                "data-analyst": "Data Analyst"}


def folder(day: str | None = None):
    return LISTS / (day or date.today().isoformat())


def pick(profile: dict, hours: float = 24, cap: int = 2, max_age: int = 60, min_score: float = 3.0) -> list[dict]:
    """Every qualifying posting from the latest scan published in the last `hours`, as list items (no numbers yet),
    each with its full description from the job-board cache."""
    if not apply.POSTINGS.exists():
        raise ValueError("No scan yet. Run `python -m shortlist run` first.")
    rows = [r for r in apply._read(apply.POSTINGS) if not off_field(r.get("title", ""), profile.get("roles") or [])]
    apps, skips = apply._read(apply.APPS, apply.APP_FIELDS), apply._read(apply.SKIPS, apply.SKIP_FIELDS)
    offset = (date.today() - datetime.fromtimestamp(apply.POSTINGS.stat().st_mtime).date()).days
    exclude = tuple(apply.EXCLUDE_TITLES) + tuple(profile.get("exclude_titles") or ())
    picked = apply.pick(rows, apps, skips, 1_000_000, cap, max_age, min_score, age_offset=offset,
                        exclude_titles=exclude, max_age_hours=hours)
    out = []
    for r, it in zip(picked, apply.build_items(picked, profile, messages=0)):
        out.append({"company": it["company"], "title": it["title"], "url": it["url"], "location": it["location"],
                    "posted_at": r.get("posted_at") or "", "date_only": apply.date_only(r), "age": it["age"],
                    "age_hours": it["age_hours"], "board": it["board"], "field": it["field"], "role": it["role"],
                    "resume": it["resume"], "sponsor": it["sponsor"], "lottery": it["lottery"],
                    "wage_level": it["wage_level"], "salary": it["salary"], "salary_source": it["salary_source"],
                    "years": it["years"], "have": it["have"], "score": round(it["score"], 1),
                    "text": apply.posting_text(it["url"])})
    return out


def merge(old: list[dict], fresh: list[dict], cap: int = 2) -> list[dict]:
    """The day's list so far plus the new postings, numbered after it. A posting already listed keeps its number,
    and the employer cap and one-per-title rule count what's already listed."""
    seen = {it["url"] for it in old}
    titles = {(it["company"], apply.title_key(it["title"])) for it in old}
    per_company = Counter(it["company"] for it in old)
    n = max((it["n"] for it in old), default=0)
    out = list(old)
    for it in fresh:
        key = (it["company"], apply.title_key(it["title"]))
        if it["url"] in seen or key in titles or per_company[it["company"]] >= cap:
            continue
        n += 1
        seen.add(it["url"])
        titles.add(key)
        per_company[it["company"]] += 1
        out.append(it | {"n": n})
    return out


def make(profile: dict, hours: float = 24, cap: int = 2, day: str | None = None) -> tuple[dict, int]:
    """Pick, add to the day's list and save it. Returns the list and how many postings are new on it."""
    hours = max(1.0, min(float(hours), MAX_HOURS))
    path = folder(day) / "jobs.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    now = datetime.now().isoformat(timespec="minutes")
    rank = {key: i for i, key in enumerate(GROUPS)}  # numbered in reading order: by group, then by rank within it
    fresh = sorted(pick(profile, hours, cap), key=lambda it: rank.get(it["field"], len(rank)))
    items = merge(old.get("items") or [], [it | {"listed_at": now} for it in fresh], cap)
    doc = {"date": day or date.today().isoformat(), "made_at": old.get("made_at") or now, "updated_at": now,
           "hours": hours, "results_from": datetime.fromtimestamp(apply.POSTINGS.stat().st_mtime).isoformat(timespec="minutes"),
           "have": profile.get("years_experience"), "items": items}
    save(doc)
    return doc, len(items) - len(old.get("items") or [])


def load(day: str | None = None) -> dict | None:
    """A saved list: the given day's, or the latest when `day` is None."""
    if day is None:
        days = sorted(p.parent.name for p in LISTS.glob("*/jobs.json"))
        if not days:
            return None
        day = days[-1]
    path = folder(day) / "jobs.json"
    return json.loads(path.read_text()) if path.exists() else None


def save(doc: dict) -> None:
    out = folder(doc.get("name") or doc["date"])  # "2026-10-29-2": a later list the same day keeps its own numbers
    out.mkdir(parents=True, exist_ok=True)
    for name, text in (("jobs.json", json.dumps(doc, indent=1)), ("jobs.html", page(doc)), ("jobs.csv", table(doc))):
        tmp = out / (name + ".tmp")
        tmp.write_text(text)
        tmp.replace(out / name)  # Tailor may read the list while it's rewritten: whole or not at all


# ---------- how each fact reads ----------

def posted(it: dict) -> str:
    """'Oct 29 at 9:14 AM' in your time zone, or 'Oct 29' when the board gives only a date (Workday)."""
    try:
        t = datetime.fromisoformat(it.get("posted_at") or "")
    except ValueError:
        return f"{it['age']} days before the scan" if it.get("age") else "the day of the scan"
    if it.get("date_only"):
        return f"{t:%b} {t.day}"
    t = t.astimezone() if t.tzinfo else t
    return f"{t:%b} {t.day} at {t.hour % 12 or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'}"


def odds(it: dict) -> str:
    try:
        p = float(it.get("lottery") or "")
    except ValueError:
        return ""
    if p >= 0.999:
        return "no lottery (cap-exempt employer)"
    level = str(it.get("wage_level") or "")
    return f"{round(100 * p)}%" + (f" at wage level {level}" if level and level not in ("?", "0") else "")


def pay(it: dict) -> str:
    if not it.get("salary"):
        return ""
    amount = f"${int(it['salary']):,}"
    return f"{amount} (posted, midpoint)" if it.get("salary_source") == "posted" else f"about {amount} (typical for the role)"


def years(it: dict) -> str:
    asked, have = str(it.get("years") or ""), it.get("have")
    if not asked:
        return "no years asked"
    mine = f" (you have {have:g})" if isinstance(have, (int, float)) else ""
    return f"asks {asked}+ years{mine}"


def resume_name(it: dict) -> str:
    return RESUME_NAMES.get(it.get("resume") or "", (it.get("resume") or "").replace("-", " ").title()) + " resume"


def groups(doc: dict) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for it in doc["items"]:
        groups.setdefault(it.get("field") or "big", []).append(it)
    return [(GROUPS.get(k, k.title()), groups[k]) for k in list(GROUPS) + [k for k in groups if k not in GROUPS] if k in groups]


def _day(doc: dict) -> str:
    d = date.fromisoformat(doc["date"])
    return f"{d:%b} {d.day}, {d.year}"


def table(doc: dict) -> str:
    rows = []
    for label, items in groups(doc):
        for it in items:
            rows.append({"n": it["n"], "group": label, "company": it["company"], "title": it["title"],
                         "location": it["location"], "posted": posted(it), "sponsorship": it.get("sponsor", ""),
                         "lottery_odds": odds(it), "wage_level": it.get("wage_level", ""),
                         "pay": int(it["salary"]) if it.get("salary") else "", "pay_source": it.get("salary_source", ""),
                         "years_asked": it.get("years", ""), "resume": resume_name(it), "score": it.get("score", ""),
                         "link": it["url"]})
    rows.sort(key=lambda r: r["n"])
    buf = io.StringIO()
    w = csv.DictWriter(buf, CSV_FIELDS)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _link(url: str, text: str) -> str:
    if not re.match(r"https?://", url or ""):
        return html.escape(text)
    return f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{html.escape(text)}</a>'


def page(doc: dict) -> str:
    """A script-free page to keep: grouped like the queue, every title opens its posting."""
    e = html.escape
    n_items = len(doc["items"])
    first = min((it["n"] for it in doc["items"]), default=1)  # the example names a job this list has
    parts = []
    for label, items in groups(doc):
        parts.append(f"<h2>{e(label)} <span>{len(items)}</span></h2>")
        for it in sorted(items, key=lambda x: x["n"]):
            facts = [x for x in (it.get("location"), f"posted {posted(it)}", resume_name(it)) if x]
            visa = [x for x in (it.get("sponsor"), f"H-1B lottery odds {odds(it)}" if odds(it) else "") if x]
            money = [x for x in (pay(it), years(it)) if x]
            extra = [x for x in (it.get("alumni_note"), f"found on {it['found']}" if it.get("found") else "") if x]
            parts.append(
                f'<div class="job"><div class="top"><span class="n">#{it["n"]}</span> <b>{e(it["company"])}</b> '
                f'{_link(it["url"], it["title"])}</div>'
                f'<div class="row">{e(", ".join(facts))}</div>'
                + (f'<div class="row">{e("; ".join(visa))}</div>' if visa else "")
                + (f'<div class="row muted">{e("; ".join(money))}</div>' if money else "")
                + (f'<div class="row muted">{e("; ".join(extra))}</div>' if extra else "")
                + (f'<div class="row">{_link(it["alumni_url"], "Northeastern alumni there")}</div>' if it.get("alumni_url") else "")
                + ("" if len(it.get("text") or "") >= 200 else
                   '<div class="row warn">The description wasn\'t saved: copy it from the posting when you ask for a resume.</div>')
                + "</div>")
    hours = doc.get("hours", 24)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Jobs, {e(_day(doc))}</title>
<style>
:root{{--bg:#f5f5f7;--card:#fff;--ink:#1d1d1f;--sub:#6e6e73;--line:#e3e3e8;--blue:#0071e3}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif}}
main{{max-width:880px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:26px;letter-spacing:-.01em;margin:0 0 6px}}p{{margin:0 0 8px;color:var(--sub)}}
h2{{font-size:17px;margin:28px 0 10px}}h2 span{{color:var(--sub);font-weight:500}}
.job{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 16px;margin:0 0 10px}}
.top{{display:flex;gap:8px;flex-wrap:wrap;align-items:baseline}}.n{{color:var(--sub);font-weight:700;min-width:30px}}
a{{color:var(--blue);text-decoration:none;font-weight:600}}a:hover{{text-decoration:underline}}
.row{{font-size:14px;margin-top:4px}}.muted{{color:var(--sub)}}.warn{{color:#b25e00}}
@media print{{body{{background:#fff}}.job{{break-inside:avoid}}}}
</style></head><body><main>
<h1>Jobs posted in the last {hours:g} hours</h1>
<p>{e(_day(doc))}: {n_items} {"posting fits" if n_items == 1 else "postings fit"} your field and your visa status, at most 2 per employer and none you've applied to or skipped. Startups first, then established employers, each ranked by fit and freshness.</p>
<p>To tailor a resume for one, ask Claude: "tailor #{first} from the {e(_day(doc))} list" (or run python -m tailor job {first}).</p>
{"".join(parts) or "<p>No postings fit today. Try again after the next scan.</p>"}
</main></body></html>
"""
