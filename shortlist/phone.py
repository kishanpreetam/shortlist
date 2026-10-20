"""Hand today's queue to a phone page, and bring back what was done there.

    python -m shortlist phone-export --out queue.json   today's queue, follow-ups and tracker state as one JSON document
    python -m shortlist phone-import DIR                record the marks a phone page saved (JSON files under DIR)

The phone page (any page that stores this document and writes "marks") is
the only thing that sees the export; nothing here talks to the network.
Marks are JSON objects with `kind` ("apply", "followup" or "status"):
  {kind: "apply", status: "applied" | "skipped" | "undone", url, company, title, role, channel, contact, reason, at}
  {kind: "followup", url, which, at}
  {kind: "status", url, status, at}
"""

from __future__ import annotations

import glob
import html
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from . import apply, outcomes
from .paths import DATA

MAX_BYTES = 240_000  # the phone store caps one document at 256 KiB


def _scan_stats() -> dict:
    postings, employers = 0, set()
    for f in glob.glob(str(DATA / "boards" / "*.json")):
        try:
            rows = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        postings += len(rows)
        employers.update(r.get("company", "") for r in rows)
    family = apply._read(apply.POSTINGS)
    realistic = sum(1 for r in family if not r.get("excluded") and float(r.get("score") or 0) > 0)
    return {"postings": postings, "employers": len(employers - {""}), "in_family": len(family), "realistic": realistic}


def export(profile: dict, out: Path) -> dict:
    """One document for the phone: the queue `today` last built, plus tracker state."""
    queue = json.loads(apply.TODAY.read_text()) if apply.TODAY.exists() else {"items": [], "target": 0}
    apps = apply._read(apply.APPS, apply.APP_FIELDS)
    skips = apply._read(apply.SKIPS, apply.SKIP_FIELDS)
    urls = {i["url"] for i in queue.get("items", [])}
    today = date.today()
    phone = profile.get("phone") or {}
    doc = {
        "version": 1,
        "date": queue.get("date") or today.isoformat(),
        "made_at": queue.get("made_at"),
        "exported_at": datetime.now().isoformat(timespec="minutes"),
        "results_from": queue.get("results_from"),
        "target": queue.get("target", 0),
        "scan": _scan_stats(),
        "items": queue.get("items", []),
        "applied": sorted(a["url"] for a in apps if a["url"] in urls),
        "skipped": sorted(s["url"] for s in skips if s["url"] in urls),
        "applied_today": sum(a["applied_on"] == today.isoformat() for a in apps),
        "followups": [{k: a[k] for k in ("company", "title", "url", "applied_on", "contact", "which", "note")}
                      for a in apply.followups_due(profile)],
        "recent": [{k: a[k] for k in ("company", "title", "url", "applied_on", "status")}
                   for a in apps if apply._d(a["applied_on"]) >= today - timedelta(days=7)][-200:],
        "resumes": phone.get("resume_assets") or {},
        "statuses": list(apply.STATUSES),
        "searches": apply.search_links(),
        "skip_reasons": list(apply.SKIP_REASONS),
    }
    body = json.dumps(doc, separators=(",", ":"))
    while len(body.encode()) > MAX_BYTES and doc["recent"]:
        doc["recent"] = doc["recent"][len(doc["recent"]) // 2 + 1:]
        body = json.dumps(doc, separators=(",", ":"))
    if len(body.encode()) > MAX_BYTES:
        raise ValueError(f"export is {len(body.encode())} bytes; lower --n or --spares")
    out.write_text(body)
    return doc


def _marks(folder: Path) -> list[tuple[str, dict, int | None]]:
    """(doc_id, mark, version) for every mark file under folder, oldest first."""
    found = []
    for f in sorted(folder.rglob("*.json")):
        try:
            raw = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        body = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if not isinstance(body, dict):
            continue
        version = raw.get("version") if isinstance(raw.get("version"), int) else None
        found.append((raw.get("id") or f.stem, body, version))
    return sorted(found, key=lambda t: str(t[1].get("at", "")))


def import_marks(folder: Path) -> dict:
    """Record each mark in the tracker. Safe to run twice: applications are keyed by URL.

    Writes processed-ids.json: every mark now reflected in the tracker or not usable at all,
    which the page can delete once the next queue document (carrying that state) is written.
    Marks that failed to record are left for the next run."""
    done = {"applied": 0, "skipped": 0, "undone": 0, "followed_up": 0, "status": 0, "ignored": 0, "failed": 0}
    processed = []
    for doc_id, m, _version in _marks(folder):
        url = str(m.get("url", ""))[:2000]
        kind, status = m.get("kind"), m.get("status")
        at = apply._d(str(m.get("day") or m.get("at", ""))[:10])
        on = at if at != date.min else date.today()
        try:
            if not url.startswith("https://"):
                done["ignored"] += 1
            elif kind == "apply" and status == "applied":
                channel = m.get("channel") if m.get("channel") in outcomes.CHANNELS else "cold"
                apply.record(str(m.get("company", ""))[:200], str(m.get("title", ""))[:300], url,
                             str(m.get("role", ""))[:40], channel, str(m.get("contact", ""))[:200], on=on)
                done["applied"] += 1
            elif kind == "apply" and status == "skipped":
                apply.skip(str(m.get("company", ""))[:200], str(m.get("title", ""))[:300], url, str(m.get("reason", "")))
                done["skipped"] += 1
            elif kind == "apply" and status == "undone":
                apply.undo(url)
                done["undone"] += 1
            elif kind == "followup":
                apply.followed_up(url, 2 if m.get("which") == 2 else 1)
                done["followed_up"] += 1
            elif kind == "status" and status in apply.STATUSES:
                apply.update(url, status)
                done["status"] += 1
            else:
                done["ignored"] += 1
        except ValueError:
            done["failed"] += 1
            continue
        processed.append(doc_id)
    (folder / "processed-ids.json").write_text(json.dumps(sorted(set(processed)), indent=1))
    return done | {"processed": len(set(processed))}


STATIC_CSS = """
:root{--bg:#f5f5f7;--card:#fff;--ink:#1d1d1f;--sub:#6e6e73;--line:#e2e2e7;--accent:#0071e3;--chip:#eeeef2;--good:#1a7f45;--goodbg:#e6f5ec;--warn:#a35600;--warnbg:#fff1de}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--ink:#f5f5f7;--sub:#a1a1a6;--line:#2e2e31;--accent:#0a84ff;--chip:#2c2c2e;--good:#32d74b;--goodbg:rgba(50,215,75,.15);--warn:#ff9f0a;--warnbg:rgba(255,159,10,.15)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif}
main{max-width:680px;margin:0 auto;padding:16px 16px 48px}h1{font-size:24px;margin:8px 0 4px}h2{font-size:15px;text-transform:uppercase;letter-spacing:.04em;color:var(--sub);margin:24px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px;margin:0 0 12px}
.co{font-weight:700}.ti{font-size:17px;font-weight:600;margin:2px 0}.ti a,.btn{color:var(--accent);text-decoration:none}
.where,.muted{color:var(--sub);font-size:13px}.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.chip{background:var(--chip);border-radius:99px;padding:2px 9px;font-size:13px}
.chip.new{background:var(--goodbg);color:var(--good);font-weight:600}.warn{background:var(--warnbg);color:var(--warn);border-radius:10px;padding:6px 10px;font-size:13px;margin:6px 0}
.btn{display:inline-block;background:var(--accent);color:#fff;border-radius:99px;padding:8px 16px;font-weight:600;margin:6px 0}
pre{white-space:pre-wrap;font:inherit;background:var(--bg);border-radius:10px;padding:10px;margin:8px 0;-webkit-user-select:text;user-select:text}
.links a{color:var(--accent);margin-right:14px;font-size:14px;text-decoration:none}
"""


def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def _link(url: str, text: str, cls: str = "") -> str:
    if not str(url).startswith("https://"):
        return _e(text)
    return f'<a class="{cls}" href="{_e(url)}">{_e(text)}</a>'


def write_static(doc: dict, out: Path, answers: dict | None = None) -> None:
    """A self-contained page with no scripts, for opening from a phone's Files app (iCloud Drive)."""
    def job(i: dict) -> str:
        exempt = str(i.get("sponsor", "")).startswith("cap-exempt")
        h = i.get("age_hours")
        when = (f"posted {h} hour{'' if h == 1 else 's'} ago" if h is not None and h < 48 else f"posted {i.get('age')} days ago")
        chips = [f'<span class="chip{" new" if i.get("age", 99) <= 2 else ""}">{when}</span>']
        chips.append(f'<span class="chip">{"No lottery (cap-exempt)" if exempt else _e(i.get("sponsor"))}</span>' if i.get("sponsor") else "")
        if i.get("lottery") and not exempt:
            chips.append(f'<span class="chip">Lottery {round(100 * float(i["lottery"]))}%</span>')
        if i.get("years"):
            chips.append(f'<span class="chip">asks {_e(i["years"])} yrs</span>')
        warn = ('<div class="warn">Graduate program: check the graduation window first.</div>' if i.get("grad_check") else "") + \
               ('<div class="warn">Over a week old: a note to someone there matters more than usual.</div>' if i.get("age", 0) > 7 else "")
        people = " ".join(_link(u, t) for t, u in i.get("people", []))
        words = f'<div class="muted"><b>Use their words:</b> {_e(", ".join(i.get("mirror", [])))}</div>' if i.get("mirror") else ""
        return (f'<div class="card"><div class="co">{i["n"]}. {_e(i["company"])}</div>'
                f'<div class="ti">{_link(i["url"], i["title"])}</div><div class="where">{_e(i.get("location"))}</div>'
                f'<div class="chips">{"".join(chips)}</div>{warn}{_link(i["url"], "Open job", "btn")}'
                f'<div class="muted"><b>Resume:</b> {_e(i.get("resume"))} version</div>{words}'
                + (f'<div class="muted"><b>Message one person:</b></div><div class="links">{people}</div><pre>{_e(i.get("note"))}</pre>' if i.get("message") else
                   f'<div class="links">{people}</div>') + '</div>')
    items = doc.get("items", [])
    main = [job(i) for i in items if not i.get("spare")]
    spares = [job(i) for i in items if i.get("spare")]
    ans = ""
    for g in (answers or {}).get("groups", []):
        ans += f'<h2>{_e(g["title"])}</h2>' + "".join(f'<div class="card"><div class="co">{_e(a["label"])}</div><pre>{_e(a["text"])}</pre></div>' for a in g["items"])
    searches = "".join(f'<div class="links"><b>{_e(role)}:</b> ' + " ".join(f'<a href="{_e(u)}">{_e(site)}</a>' for site, u in links) + '</div>'
                       for role, links in doc.get("searches") or [])
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Shortlist Today</title><style>{STATIC_CSS}</style></head><body><main>'
            f'<h1>Today, {_e(doc.get("date"))}</h1><div class="muted">Updated {_e(str(doc.get("exported_at", "")).replace("T", " at "))}. '
            f'Mark what you apply to on your Shortlist page or on your Mac. Press and hold a message to copy it.</div>'
            f'<h2>Search the big job sites: posted in the last 24 hours</h2><div class="card">{searches}</div>'
            f'<h2>Queue</h2>{"".join(main)}<h2>Spares</h2>{"".join(spares)}{ans}</main></body></html>')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
