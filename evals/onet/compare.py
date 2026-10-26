"""Would a big public skills list help Shortlist pick a posting's keywords?

Finds every software name from O*NET 31.0 Software Skills in each posting, then checks each one against Shortlist's
keyword list for that posting (resume.job_keywords). A match is in the built-in list when the engine's own tool list
recognizes it, caught by the posting read when only the cached read of the posting (rewrite.keywords) listed it, and
missed otherwise. Nothing is sent anywhere and Claude is never called: a posting without a saved read uses the
built-in list alone.

  python evals/onet/compare.py ONET_CSV POSTINGS_DIR [--reads results/requirements] [--profile profile.yaml] [--out results.json]

ONET_CSV is software_skills.csv from https://www.onetcenter.org/database.html (O*NET 31.0). POSTINGS_DIR holds
postings.json ([{key, company, title, family, url}]) and one <key>.txt per posting with its full text. Saved reads are
filed under your profile's never-words, as Tailor files them, so the profile is read for those words only; they are
never printed or written out.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from shortlist import resume as engine, rewrite  # noqa: E402

# vendors whose name a posting often drops ("Apache Spark" is written "Spark")
VENDORS = ["The MathWorks", "Microsoft", "Oracle", "IBM", "Autodesk", "Adobe", "SAP", "Apache", "Google", "Apple",
           "Siemens", "ESRI", "Cisco", "Amazon", "Atlassian", "Salesforce", "Intuit", "Informatica", "Teradata", "SAS",
           "Hewlett Packard", "HP", "Dell", "VMware", "Red Hat", "Meta", "Facebook", "Elastic", "Mozilla", "TIBCO",
           "Talend", "Splunk", "JetBrains", "Wolfram", "Qlik", "Alteryx", "Snowflake", "Databricks", "MongoDB", "Redis",
           "Docker", "Pivotal", "Bentley", "Trimble", "Corel", "Cadence", "Sage", "ADP", "GE", "Thomson Reuters"]
# words that name a kind of product, never a product on their own ("Microsoft Office" is never "Office")
GENERIC = {"office", "word", "access", "project", "teams", "outlook", "cloud", "analytics", "data", "studio", "server",
           "database", "forms", "reports", "designer", "manager", "explorer", "assistant", "connect", "enterprise",
           "business", "suite", "platform", "workbench", "builder", "developer", "insights", "intelligence", "pro",
           "online", "express", "live", "one", "edge", "drive", "sites", "groups", "meet", "chat", "notes", "mail",
           "calendar", "docs", "sheets", "slides", "keep", "search", "maps", "translate", "photos", "health", "pay",
           "store", "news", "people", "planner", "lists", "stream", "visual", "basic", "dynamics", "foundation"}
GENERIC_ACRONYMS = {"AI", "ML", "BI", "EEO", "ERP", "CRM", "CAD", "CAM", "CAE", "HR", "HRIS", "IT", "QA", "UI", "UX",
                    "API", "SDK", "GIS", "ETL", "OS", "DB", "PM", "HP", "FSA", "POS", "EDA", "ODS", "CMS", "LMS", "EHR",
                    "EMR", "PLM", "SCM", "WMS", "MES", "IDE", "VCS", "CI", "CD", "NI", "GE"}
COMPANIES = {"google", "linkedin", "facebook", "microsoft", "amazon", "apple", "ibm", "oracle", "meta", "e-verify"}
# ordinary words a 1934 dictionary lacks
MODERN = {"email", "online", "offline", "debug", "website", "webpage", "dataset", "workflow", "workspace", "software",
          "hardware", "login", "setup", "backend", "frontend", "upload", "download", "internet", "app", "apps", "blog",
          "spreadsheet", "dashboard", "toolkit", "framework", "pipeline", "metadata", "analytics", "smartphone",
          "username", "chatbot", "troubleshoot", "ecommerce", "e-commerce", "multimedia", "webinar", "cybersecurity",
          "database"}
CATEGORY_LIKE = re.compile(r"^[A-Za-z][a-z]+( [a-z]+)*$")  # "Migration", "Data modeling": kinds, not tools
PREFERRED_HEAD = re.compile(r"prefer|nice|bonus|plus\b|desired|ideal|great if|extra credit", re.I)
REQUIRED_HEAD = re.compile(r"require|qualif|must|basic|minimum|what you.{0,20}(bring|have|need)|you have|about you|"
                           r"skills|experience", re.I)
RESP_HEAD = re.compile(r"responsib|what you.{0,10}(will|ll) do|the role|day[- ]to[- ]day|you will|what you.{0,10}do", re.I)


def load_names(onet_csv: Path) -> dict:
    names: dict = collections.defaultdict(lambda: {"hot": False, "demand": False})
    for r in csv.DictReader(open(onet_csv, encoding="utf-8")):
        e = names[r["Workplace Example"].strip()]
        e["hot"] |= r["Hot Technology"] == "Y"
        e["demand"] |= r["In Demand"] == "Y"
    return names


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("onet_csv", type=Path)
    ap.add_argument("postings", type=Path)
    ap.add_argument("--reads", type=Path, default=ROOT / "results" / "requirements",
                    help="saved posting reads (Tailor's cache); a posting without one uses the built-in list alone")
    ap.add_argument("--profile", type=Path, default=ROOT / "profile.yaml",
                    help="your profile, read only for resume_never_words, which the saved reads are filed under")
    ap.add_argument("--words", type=Path, default=Path("/usr/share/dict/words"),
                    help="a plain word list, used to tell product names from ordinary words")
    ap.add_argument("--out", type=Path, default=Path(__file__).with_name("results.json"))
    a = ap.parse_args()
    if not a.words.exists():
        sys.exit(f"No word list at {a.words}. Pass one with --words (one word per line).")
    words = {w.strip().lower() for w in open(a.words, encoding="utf-8")}
    if a.profile.exists():
        import yaml
        engine.never_words((yaml.safe_load(a.profile.read_text()) or {}).get("resume_never_words"))
    names = load_names(a.onet_csv)

    def word(w: str) -> bool:
        """An ordinary English word, counting endings ("systems", "reporting"). An acronym ("AWS") never is."""
        if len(w) >= 2 and w.isupper():
            return False
        w = w.lower()
        if w in words or w in MODERN or w.rstrip("s") in words or w.rstrip("s") in MODERN or \
                (w.endswith("ies") and w[:-3] + "y" in words):
            return True
        for end in ("ing", "ed", "er"):
            stem = w[:-len(end)]
            if w.endswith(end) and any(x in words or x in MODERN for x in (stem, stem + "e", stem[:-1])):
                return True
        return False

    def plain(form: str) -> bool:
        return all(word(w) for w in re.split(r"[\s-]+", form) if w)

    def forms_of(name: str) -> tuple[set, str]:
        """The forms a posting writes a product in, and its vendor. Plain-word forms count only with their vendor
        ("Adobe Contribute", never "Contribute"), and a kind of software ("Email software") never counts."""
        e = names[name]
        n = re.sub(r"\s+(software|system)$", "", name).strip()
        toks, forms, vendor = n.split(), set(), ""
        if not toks or n.lower() in COMPANIES or (name.endswith(" software") and plain(n)):
            return forms, vendor
        last = toks[-1]
        if len(toks) > 1 and re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", last) and last not in GENERIC_ACRONYMS:
            initials = "".join(w[0] for w in toks[:-1] if w[:1].isalpha()).upper()
            if initials.endswith(last):  # "Structured query language SQL", "Amazon Web Services AWS"
                forms |= {last, " ".join(toks[-1 - len(last):-1])}
        for v in VENDORS:
            if n.startswith(v + " ") and len(n) > len(v) + 1:
                vendor, rest = v, n[len(v) + 1:]
                if not plain(rest) or (v == "Apache" and " " not in rest):
                    forms.add(rest)
                break
        if not (plain(n) and " " not in n and not (e["hot"] or e["demand"])):
            forms.add(n)
        if CATEGORY_LIKE.match(n) and len(toks) > 1 and not vendor:
            return set(), vendor
        return {f for f in forms if len(f) >= 2 and f.lower() not in GENERIC and f not in GENERIC_ACRONYMS
                and f.lower() not in COMPANIES}, vendor

    def rx_for(form: str):
        """Whole words; case-sensitive for one ordinary word or an acronym, any case otherwise."""
        if len(form) < 2 or (len(form) == 2 and not form.isupper()):
            return None
        exact = " " not in form and (word(form) or form.isupper())
        body = re.escape(form).replace(r"\ ", r"[\s-]+")
        return re.compile(rf"(?<![\w.+#/-]){body}(?![\w+#-])", 0 if exact else re.I)

    catalog = [(name, f, p, v) for name in names for fs, v in [forms_of(name)] for f in fs for p in [rx_for(f)] if p]

    def section_of(jd: str, start: int) -> str:
        head = "other"
        for line in jd[:start].splitlines():
            s = line.strip()
            if 0 < len(s) <= 60 and (s.endswith(":") or len(s.split()) <= 6) and not s.endswith("."):
                head = ("preferred" if PREFERRED_HEAD.search(s) else "required" if REQUIRED_HEAD.search(s)
                        else "responsibility" if RESP_HEAD.search(s) else head)
        return head

    def saved_read(title: str, jd: str):
        t, (job, _) = rewrite._clean_title(title), rewrite.clean_posting(jd)
        path = a.reads / f"{rewrite._cache_key([], f'requirements|{t}', job + rewrite.REQ_RULES)}.json"
        if not path.exists():
            return None
        got = json.loads(path.read_text())
        reqs = [dict(r, kind="logistics") if r.get("kind") != "logistics" and rewrite.is_logistics(str(r.get("text") or ""))
                else r for r in got.get("requirements") or [] if isinstance(r, dict)]
        return reqs, [k for k in got.get("keywords") or [] if isinstance(k, dict) and k.get("text")]

    meta = a.postings / "postings.json"
    jobs = json.loads(meta.read_text())
    out = []
    for job in jobs:
        key, company, title = job["key"], job["company"].strip()[:120], job["title"].strip()[:200]
        path = a.postings / f"{key}.txt"
        if not path.exists():
            print(f"skipped {key}: no {path.name}")
            continue
        jd = path.read_text()[:60_000]
        got = saved_read(title, jd)
        reqs, given = got if got else ([], [])
        kws = engine.job_keywords(title, jd, given, company, reqs)
        listed = " | ".join(k["text"] for k in kws)
        text = engine.without_employer(f"{title}\n{jd}", company)
        asked = {kind: " ".join(r["text"] for r in reqs if r.get("kind") == kind) for kind in ("required", "preferred")}
        found: dict = {}
        for name, form, p, vendor in catalog:
            m = p.search(text)
            if not m or (vendor and company.lower().startswith(vendor.lower())):
                continue  # not in the posting, or the employer's own product
            hit = m.group(0)
            prev = found.get(hit.lower())
            if prev and len(prev["form"]) >= len(form):
                continue
            known = any(not t.startswith("theme:") for t in engine.terms(hit))
            in_list = bool(re.search(rf"(?<!\w){re.escape(hit)}(?!\w)", listed, re.I))
            where = ("required" if p.search(asked["required"]) else "preferred" if p.search(asked["preferred"])
                     else section_of(jd, jd.find(hit)) if hit in jd else "other")
            found[hit.lower()] = {"word": hit, "onet": name, "form": form, "where": where,
                                  "status": "in the built-in list" if known else
                                            "caught by the posting read" if in_list else "missed",
                                  "in_final_list": in_list}
        longest = sorted(found, key=len, reverse=True)
        for w in list(found):  # a match inside a longer one ("Spark" inside "Apache Spark") is the same mention
            if any(w != o and re.search(rf"(?<!\w){re.escape(w)}(?!\w)", o) for o in longest):
                found.pop(w, None)
        out.append({**{k: job.get(k) for k in ("key", "company", "title", "family", "url")}, "saved_read": got is not None,
                    "keywords": [{"text": k["text"], "kind": k["kind"], "from": k["from"]} for k in kws],
                    "onet_mentions": sorted(found.values(), key=lambda f: (f["status"], f["word"].lower()))})

    tally = collections.Counter(f["status"] for r in out for f in r["onet_mentions"])
    total = sum(tally.values())
    summary = {"postings": len(out), "with_saved_read": sum(r["saved_read"] for r in out), "onet_names": len(names),
               "mentions": total, **{k: tally[k] for k in ("in the built-in list", "caught by the posting read", "missed")}}
    a.out.write_text(json.dumps({"summary": summary, "postings": out}, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=1))
    for r in out:
        for f in r["onet_mentions"]:
            if f["status"] == "missed":
                print(f"missed in {r['key']}: {f['word']} ({f['onet']}, {f['where']})")


if __name__ == "__main__":
    main()
