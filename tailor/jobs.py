"""Shortlist's files, read only: today's queue, the lists it saved, every posting it scored, the jobs you applied to or
skipped, and the job descriptions it fetched from the job boards. Tailor never writes them and works without them (a
job you paste needs none), so Shortlist doesn't need to be running.
"""

from __future__ import annotations

import csv
import json
import re
import threading
from pathlib import Path

from .paths import DATA, RESULTS

QUEUE = RESULTS / "today.json"
LISTS = RESULTS / "lists"  # python -m shortlist list saves each day's list in <date>/jobs.json, descriptions included
POSTINGS = RESULTS / "postings.csv"
APPS = RESULTS / "applications.csv"
SKIPS = RESULTS / "skipped.csv"
BOARDS = DATA / "boards"
_INDEX: dict = {"key": None, "files": {}}  # by posting url, the board file that holds it, for the boards as of `key`
_INDEX_LOCK = threading.Lock()
_SAVED: dict[str, tuple] = {}  # by jobs.json path: (its mtime, the list read from it), so a list is read once a change
_SAVED_LOCK = threading.Lock()
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}(?:-\d{1,2})?")  # a day, or a later list that day: 2026-10-29-2


def mtime(path: Path) -> int | None:
    """When a file or folder last changed (in nanoseconds), or None when it isn't there."""
    try:
        return Path(path).stat().st_mtime_ns
    except OSError:
        return None


def _rows(path: Path) -> list[dict]:
    try:
        with Path(path).open(newline="") as f:
            return list(csv.DictReader(f))
    except (OSError, ValueError, csv.Error):
        return []


def _postings(path: Path) -> list[dict]:
    """The postings in one board file; [] when it can't be read."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    return [j for j in data if isinstance(j, dict)] if isinstance(data, list) else []


def queue() -> dict:
    """Today's queue as Shortlist picked it (results/today.json), or {"items": []} when there's none to read."""
    try:
        q = json.loads(QUEUE.read_text())
    except (OSError, ValueError):
        return {"items": []}
    if not isinstance(q, dict):
        return {"items": []}
    items = q.get("items")
    return {**q, "items": [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []}


def days() -> list[str]:
    """The days with a saved list, newest first."""
    return sorted((p.parent.name for p in Path(LISTS).glob("*/jobs.json") if _DAY.fullmatch(p.parent.name)), reverse=True)


def _saved(day: str) -> dict | None:
    path = Path(LISTS) / day / "jobs.json"
    when = mtime(path)
    if when is None:
        return None
    with _SAVED_LOCK:  # the page's requests and the warm-up read lists at the same time
        hit = _SAVED.get(str(path))
        if hit and hit[0] == when:
            return hit[1]
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            doc = None
        if isinstance(doc, dict):
            items = doc.get("items")
            doc = {**doc, "items": [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []}
        else:
            doc = None
        _SAVED[str(path)] = (when, doc)
        while len(_SAVED) > 30:  # a month of lists at most stays read
            _SAVED.pop(next(iter(_SAVED)))
        return doc


def _day(day: str | None) -> str | None:
    """The day asked for, or the newest list's; None when there's no list or it isn't a date (YYYY-MM-DD)."""
    if day is None:
        return next(iter(days()), None)  # the newest only: an older list's numbers are other jobs
    return day if _DAY.fullmatch(str(day)) else None


def saved(day: str | None = None) -> dict | None:
    """A list Shortlist saved (python -m shortlist list): that day's (YYYY-MM-DD), or the newest when `day` is None.
    None when there's no such list, or it can't be read."""
    day = _day(day)
    return _saved(day) if day else None


def list_job(n: int, day: str | None = None) -> dict | None:
    """Job number `n` on a saved list (that day's, or the newest), with its saved description (`text`) and the list's
    day (`list`). None when there's no such job."""
    day = _day(day)
    hit = next((i for i in (_saved(day) or {}).get("items", []) if i.get("n") == n), None) if day else None
    return {**hit, "list": day} if hit else None


def gone() -> set[str]:
    """The postings you applied to or skipped (results/applications.csv and skipped.csv)."""
    return {r.get("url") or "" for path in (APPS, SKIPS) for r in _rows(path)} - {""}


def open_jobs() -> list[dict]:
    """Today's queue minus the jobs you applied to or skipped, in queue order: the jobs Tailor offers next, each with
    whether its description is saved (`has_text`)."""
    done = gone()
    return [{"url": i["url"], "n": i.get("n") or 0, "company": i.get("company") or "", "title": i.get("title") or "",
             "resume": i.get("resume") or None, "spare": bool(i.get("spare")), "has_text": len(posting_text(i["url"])) >= 200}
            for i in queue()["items"] if isinstance(i.get("url"), str) and i["url"] and i["url"] not in done]


def find(url: str) -> dict | None:
    """A posting Shortlist knows, by its exact URL: from today's queue, with its number there and its resume version;
    else from the lists it saved, newest first, with its number on that list and the list's day (`list`); else from
    every posting it scored (results/postings.csv), with number 0 and no resume version. None when Shortlist hasn't
    seen it."""
    if not url:
        return None
    for i in queue()["items"]:
        if i.get("url") == url:
            return {"url": url, "company": i.get("company") or "", "title": i.get("title") or "",
                    "n": i.get("n") or 0, "resume": i.get("resume") or None}
    for day in days():
        for i in (_saved(day) or {}).get("items", []):
            if i.get("url") == url:
                return {"url": url, "company": i.get("company") or "", "title": i.get("title") or "",
                        "n": i.get("n") or 0, "resume": i.get("resume") or None, "list": day}
    for r in _rows(POSTINGS):
        if r.get("url") == url:  # titled as the queue titles them
            return {"url": url, "company": r.get("company") or "", "title": " ".join((r.get("title") or "").split()).strip(" -,"),
                    "n": 0, "resume": None}
    return None


def posting_text(url: str) -> str:
    """The full job description Shortlist fetched for this posting, from its cached job boards; when they no longer
    have it (the posting closed, or a newer scan replaced it), from the newest list Shortlist saved it on. Which board
    file holds which posting is looked up again whenever the boards folder changes: Shortlist's Refresh rewrites the
    files while Tailor runs (each one written whole, then renamed into place, which moves the folder's mtime)."""
    f = _board_file(url) if url else None
    text = next((str(j.get("text") or "") for j in _postings(f) if j.get("url") == url), "") if f else ""
    if len(text) < 200 and url:
        kept = (str(i.get("text") or "") for day in days() for i in (_saved(day) or {}).get("items", []) if i.get("url") == url)
        text = next((t for t in kept if len(t) >= 200), text)
    return text


def _board_file(url: str) -> str | None:
    with _INDEX_LOCK:  # one rebuild at a time; about half a second for 30,000 postings
        key = (str(BOARDS), mtime(BOARDS))
        if _INDEX["key"] != key:
            files: dict[str, str] = {}
            for f in Path(BOARDS).glob("*.json"):
                for j in _postings(f):
                    files.setdefault(str(j.get("url") or ""), str(f))
            _INDEX.update(key=key, files=files)
        return _INDEX["files"].get(url)


def stamp() -> tuple:
    """When the queue and your applied and skipped lists last changed: a new value means Shortlist picked a new queue,
    or you marked a job applied, skipped it or undid that."""
    return tuple(mtime(p) for p in (QUEUE, APPS, SKIPS))
