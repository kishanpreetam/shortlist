"""Tools learned from job postings, beyond the engine's own list.

Candidates come from two places. Tailor reads each posting into its keywords with Claude (rewrite.keywords), each
marked tool, skill, domain, education or soft, and a tool in those reads the engine doesn't recognize is one. And
every line of every posting Shortlist has read is searched for lists that name two or more of the engine's own tools
("Python, Kubeflow, Spark and Airflow"): their other capitalized names are candidates too (mine). Either is learned once the
job boards Shortlist has read show it is a real, shared tool: postings in your role families name it as a tool (in a
list beside another name, after "such as", "e.g." or "experience with", or in parentheses after a kind of software)
at least MIN_POSTINGS times, from at least MIN_EMPLOYERS employers. So one employer's own product names never
count, and neither does an employer naming itself ("Epic Games" for Epic) or describing itself ("the AI Data Cloud").

Each learned tool gets:
  - a pattern: its names as whole words. Each word keeps its leading capitals and may be any case after them, so
    "Langfuse" is LangFuse and "Outlook" is never an "outlook"; a name of 3 letters or fewer keeps all its capitals
    ("SAP", "Ray"); a phrase postings mostly write in lower case ("pivot tables") matches in any case. A capitalized
    plain word ("Access", "Word") isn't learned;
  - its label, the capitalized spelling postings write most ("PowerPoint", "Slurm");
  - its skills row (code, data, cloud, ml or ai): the row whose tools and cue words ("language", "pipeline",
    "deploy", "dashboard", "prompt") sit beside its mentions more than they sit beside the engine's own tools.
The engine then treats it like its own tools (resume.use_lexicon): Tailor can ask whether you've used it, and it
reaches a resume only through your own lines or your answer. What was learned stays learned, so an answer you gave
about a tool always keeps its tool; set "status": "removed" on a term in data/lexicon.json to turn it off.

    python -m shortlist lexicon      learn now and list the tools; every scan and Refresh learns too
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from functools import cache
from pathlib import Path

from . import resume
from .paths import DATA, RESULTS
from .roles import role_for_title

PATH = DATA / "lexicon.json"
READS = RESULTS / "requirements"  # rewrite._reading caches each posting's read here
BOARDS = DATA / "boards"
MIN_POSTINGS = 3    # postings that must name a tool as a tool before it is learned
MIN_EMPLOYERS = 2   # ...from at least this many employers, so one employer's product names aren't a shared tool
MAX_WORDS = 4
LOWER_SHARE = 0.5   # a phrase postings write only in lower case this often is a practice or feature: any case
PLAIN_SHARE = 1.0   # a capitalized word postings use in lower case more often than capitalized is a plain word
BASE_SAMPLE = 300   # postings the usual company of the engine's own tools is read from
AROUND = 40         # characters either side of a match searched for an engine tool whose name holds it
# Kinds of software, not a tool you can say you've used; Claude sometimes files them as tools. Then the systems
# postings are applied through, which their apply instructions name ("submit your application in Workday").
NOT_TOOLS = frozenset("api apis sdk sdks crm erp cpq ehr emr hris ats cms lms gpu gpus cpu cpus saas paas iaas devops "
                      "cloud ai ml llm llms it ui ux ide ides os skills plugins plugin bi olap oltp mpp apm ipaas hpc "
                      "lakehouse microsoftai "
                      "greenhouse lever workday ashby workable icims taleo smartrecruiters jobvite".split())
_FUNCTION_WORDS = frozenset("of and for & as in on with the a an to".split())
# With nothing beside a tool's mentions, its row follows the family of the postings that name it
_FAMILY_ROW = {"data_engineer": "data", "analytics_engineer": "data", "ai_engineer": "ai", "ml_engineer": "ml",
               "data_scientist": "ml", "research_engineer": "ml", "data_analyst": "ml", "bi_engineer": "ml"}


def key_of(text: str) -> str:
    """One tool however it's spaced or cased: "Power Point" and "PowerPoint" are one."""
    return re.sub(r"[^a-z0-9+#]", "", (text or "").lower())


def tools_in(text: str) -> list[list[str]]:
    """The tools a keyword names, each with its names: a full form and its acronym are one tool ("Amazon Simple Queue
    Service (SQS)"), each side of a choice is its own ("Celery/RabbitMQ"), and a name like "PL/SQL" stays whole."""
    t = " ".join((text or "").split())
    m = re.fullmatch(r"(.*?)\s*\(([^()]{2,40})\)", t)
    if m and m.group(1).strip() and m.group(2).strip():
        return [[m.group(1).strip(), m.group(2).strip()]]
    sides = [s.strip() for s in t.split("/")]
    return [[s] for s in sides] if len(sides) > 1 and all(len(key_of(s)) >= 3 for s in sides) else [[t]]


def spellings(text: str) -> list[str]:
    """Every name a keyword gives its tools (tools_in, flattened)."""
    return [n for tool in tools_in(text) for n in tool]


def _firm(company: str) -> str:
    """One employer however its name is written: "Acme Retail, Inc." and "ACME RETAIL" are one."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", resume._SUFFIX.sub("", (company or "").strip()).lower()).split())


def tool_like(name: str) -> bool:
    """Whether a name reads as a tool's: 1 to MAX_WORDS words with a letter among them, each capitalized, styled
    ("nCino"), a number or a function word, and not a kind of software ("CRM", "SDK") or an application system."""
    words = name.split()
    if not 1 <= len(words) <= MAX_WORDS or key_of(name) in NOT_TOOLS or not re.search(r"[A-Za-z]", name):
        return False
    styled = lambda w: not w[0].isalpha() or w[0].isupper() or any(c.isupper() or c.isdigit() for c in w[1:])
    return all(w.lower() in _FUNCTION_WORDS or styled(w) for w in words) and any(c.isupper() or c.isdigit() for c in name)


def read_keywords(folder: Path = READS) -> dict[str, list[dict]]:
    """{read id: the posting's keywords} from every cached read of a posting."""
    out = {}
    for f in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        kws = d.get("keywords") if isinstance(d, dict) else None
        if isinstance(kws, list) and kws:
            out[f.stem] = [k for k in kws if isinstance(k, dict) and k.get("text")]
    return out


def board_postings(roles=(), folder: Path = BOARDS) -> list[dict]:
    """Postings in your role families (all families when `roles` is empty) from every job board read so far, once each."""
    out, seen = [], set()
    for f in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            rows = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for p in rows if isinstance(rows, list) else []:
            role = role_for_title(p.get("title") or "") if isinstance(p, dict) else None
            if role is None or (roles and role.key not in roles) or not p.get("text") or p.get("url") in seen:
                continue
            seen.add(p.get("url"))
            out.append({"company": p.get("company") or "", "title": p.get("title") or "", "text": p["text"],
                        "url": p.get("url") or "", "role": role.key})
    return out


def candidates(reads: dict[str, list[dict]]) -> dict[str, dict]:
    """Tools the reads name that the engine's own list doesn't know: {key: {spellings, reads, from}}. A tool's full
    form and acronym pool into one, across reads, and a choice gives each unknown side ("Kafka/RabbitMQ": RabbitMQ)."""
    out: dict[str, dict] = {}
    alias: dict[str, str] = {}  # each name's key: the candidate it belongs to
    for kws in reads.values():
        seen = set()
        for k in kws:
            if k.get("kind") != "tool":
                continue
            text, groups = " ".join(str(k["text"]).split()), tools_in(str(k["text"]))
            if len(groups) > 1 and resume.keyword_terms(text, learned=False) and \
                    not any(resume.keyword_terms(n, learned=False) for g in groups for n in g):
                continue  # the slash is part of one known tool's name: "Pub/Sub" is neither Pub nor Sub
            for names in groups:
                if any(resume.keyword_terms(n, learned=False) or resume.synonym_tools(n) for n in names):
                    continue  # one of its names is a tool the engine knows ("Amazon Web Services (AWS)")
                names = [n for n in names if tool_like(n)]
                if not names:
                    continue
                key = next((alias[key_of(n)] for n in names if key_of(n) in alias), key_of(names[-1]))  # the acronym
                alias.update({key_of(n): key for n in names})
                c = out.setdefault(key, {"spellings": Counter(), "reads": 0, "from": Counter()})
                if key not in seen:  # a read counts once for each tool it names
                    seen.add(key)
                    c["spellings"].update(names)
                    c["reads"] += 1
                    c["from"][k.get("from") or "responsibility"] += 1
    return out


def pattern(names, case: bool) -> str:
    """A pattern for names exactly as written (`case`) or in any case: whole words, spaced or hyphenated either way,
    never the end of a hyphenated word ("X-Ray" isn't Ray)."""
    alts = sorted({re.escape(n).replace(r"\ ", r"[\s-]*").replace(r"\-", r"[\s-]*") for n in names}, key=len, reverse=True)
    core = rf"(?<![A-Za-z0-9-])(?:{'|'.join(alts)})(?![A-Za-z0-9])"
    return f"(?-i:{core})" if case else core


def cased(names, proper: bool = True) -> str:
    """A learned tool's pattern, matched with re.I. For a proper noun each word's leading capitals stay capitals and
    the rest may be any case: "Outlook" and "OUTLOOK" match, "outlook" doesn't, and "Langfuse" is LangFuse. A word of
    3 letters or fewer in capitals ("SAP") or capitalized ("Ray") keeps all its capitals; a longer one in capitals
    ("JSON", "YAML") none, since "json" is the same tool. A practice (`proper` False) matches in any case."""
    def word(w: str) -> str:
        lead = re.match(r"[A-Z]*", w).group(0)
        if w.isupper() and len(re.sub(r"[^A-Za-z]", "", w)) > 3:
            return re.escape(w)
        if lead and (w.isupper() or len(w) <= 3):
            return f"(?-i:{re.escape(w)})"
        if lead and proper:
            return f"(?-i:{re.escape(lead)}){re.escape(w[len(lead):])}"
        return re.escape(w)  # "gRPC", "nCino"; the "as" in Infrastructure as Code
    alts = sorted({r"[\s-]*".join(word(w) for w in re.split(r"[\s-]+", n.strip()) if w) for n in names},
                  key=len, reverse=True)
    return rf"(?<![A-Za-z0-9-])(?:{'|'.join(alts)})(?![A-Za-z0-9])"


def _plain_word(name: str) -> bool:
    """A capitalized dictionary-style word ("Access", "Word"): the kind a sentence or heading capitalizes too."""
    return bool(re.fullmatch(r"[A-Z][a-z]+", name))


def _proper(names, texts: list[str], lows: list[str]) -> tuple[bool, list[int]]:
    """Whether a tool's name is a proper noun, and the postings that name it in any case. A single word always is
    ("maps" and "access" are other words); a phrase is unless postings write it only in lower case in at least
    LOWER_SHARE of the postings naming it, as a practice or a feature is ("infrastructure as code", "pivot tables")."""
    loose, kept = re.compile(cased(names, False), re.I), re.compile(cased(names, True), re.I)
    firsts = {re.split(r"[\s-]+", n.lower())[0][:12] for n in names}  # a cheap look before the pattern
    hits = [i for i, x in enumerate(texts) if any(f in lows[i] for f in firsts) and loose.search(x)]
    if any(len(re.split(r"[\s-]+", n.strip())) == 1 for n in names):
        return True, hits
    lower = sum(1 for i in hits if not kept.search(texts[i]))
    return lower < LOWER_SHARE * max(1, len(hits)), hits


# ---------- evidence: postings that name it as a tool ----------

_SEP = r"(?:\s*[,/;|•·]\s*|\s+(?:and|or|and/or|&)\s+|\s*,\s*(?:and|or)\s+)"
_NAME = r"(?<![\w.+#-])(?:[A-Z][\w.+#-]*|[a-z0-9]+[A-Z][\w.+#-]*)"  # a capitalized or styled word: Cerner, S3, nCino
_BEFORE, _AFTER = re.compile(rf"{_NAME}{_SEP}$"), re.compile(rf"^{_SEP}{_NAME}")
_INTRO = re.compile(r"(?:such as|e\.g\.?,?|i\.e\.?,?|including|like|incl\.?|experience (?:with|in|using)|knowledge of|"
                    r"familiarity with|proficiency (?:in|with)|proficient (?:in|with)|tools?:|stack:|using)\s*$", re.I)
_KIND_PAREN = re.compile(r"[A-Za-z]{2,}\s*\(\s*$")  # "EHR systems (Epic", "AWS (EKS"


def _clause(text: str, start: int, end: int, reach: int = 90) -> tuple[str, str]:
    """The text just before and after a mention, within its line and sentence."""
    a = max(text.rfind("\n", 0, start) + 1, start - reach)
    b = text.find("\n", end)
    b = min(len(text) if b < 0 else b, end + reach)
    return re.split(r"[.!?](?:\s|$)", text[a:start])[-1], re.split(r"[.!?](?:\s|$)", text[end:b])[0]


def listed(text: str, m: re.Match) -> bool:
    """Whether a posting names a mention as a tool: beside another name in a list ("Kafka or RabbitMQ", "Epic,
    Cerner"), first in parentheses after a kind ("ERP systems (SAP"), or after "such as", "e.g.", "experience with".
    Prose about the employer ("Being Epic means", "built on the AI Data Cloud") is none of these."""
    left, right = _clause(text, m.start(), m.end())
    return bool(_BEFORE.search(left) or _AFTER.search(right) or _INTRO.search(left) or _KIND_PAREN.search(left))


own_name = resume.in_name  # an employer whose own name holds the tool's name: "Epic Games" holds Epic


def _evidence(names: list[str], posts: list[dict], texts: list[str], lows: list[str], own) -> dict:
    """Which postings name a tool as a tool, outside employers named for it; whether its name is a proper noun; and
    why it isn't learned ("" when it is). `hits`: every posting its pattern matches, for its label and row.
    `own(rx, i)`: the matches in posting i that are the tool's own, not inside one of the engine's tools."""
    proper, any_case = _proper(names, texts, lows)
    rx = re.compile(cased(names, proper), re.I)
    hits = [i for i in any_case if not own_name(names, posts[i]["company"]) and own(rx, i)]
    used = [i for i in hits if any(listed(texts[i], m) for m in own(rx, i))]
    out = {"proper": proper, "hits": hits, "postings": len(used), "employers": len({_firm(posts[i]["company"]) for i in used}),
           "families": dict(Counter(posts[i]["role"] for i in used).most_common()), "why_not": ""}
    if all(_plain_word(n) for n in names):  # used in lower case more than capitalized, it's a word first: "access"
        exact, lower = re.compile(pattern(names, True)), re.compile(pattern([n.lower() for n in names], True))
        plain = sum(1 for i in any_case if lower.search(texts[i]))
        if plain > PLAIN_SHARE * max(1, sum(1 for i in any_case if exact.search(texts[i]))):
            out["why_not"] = f"a plain word too ({plain} postings use it in lower case)"
    if not out["why_not"] and (out["postings"] < MIN_POSTINGS or out["employers"] < MIN_EMPLOYERS):
        out["why_not"] = f"{out['postings']} postings from {out['employers']} employers name it as a tool"
    return out


# ---------- the skills row: what sits beside each mention ----------

CUES = {  # words beside a mention that say what a tool is for; none repeats an engine tool's pattern ("agents", "ETL")
    "code": r"\blanguages?\b|\bprogramming\b|\bscripting\b|\bcoding\b",
    "data": r"\bpipelines?\b|\bwarehouses?\b|\bdatabases?\b|\bingest|\bmessag|\bqueues?\b|\bstorage\b|\bsource systems?\b|"
            r"\bcrm\b|\berp\b|\behr\b|\bemr\b|\bhris\b|\benterprise (?:systems?|platforms?|data)\b|"
            r"\bdata (?:sources?|standards?|formats?|extract)",
    "cloud": r"\bcloud\b|\bdeploy|\binfrastructure\b|\bcontainers?\b|\bserverless\b|\bmicroservices?\b|\bdevops\b|"
             r"\bmonitoring\b|\btracing\b|\bclusters?\b|\bschedulers?\b|\bhpc\b|\bnetwork",
    "ml": r"(?<!data )\bmodel(?:s|ing)?\b|\bdashboards?\b|\bvisuali[sz]|\banalytics\b|\breporting\b|\bpresentations?\b|"
          r"\bspreadsheets?\b|\bslides\b",
    "ai": r"\bprompts?\b|\bvoice\b|\bspeech\b|\bconversational\b|\bchatbots?\b|\bassistants?\b",
}
_CUE_RX = {k: re.compile(v, re.I) for k, v in CUES.items()}


def _beside(left: str, right: str, tools: dict) -> Counter:
    """The rows named beside one mention: each engine tool there once, and each row's cue words once."""
    near = f"{left} | {right}"
    out = Counter(resume._BUILTIN_CATEGORY[t] for t, rx in tools.items() if rx.search(near))
    out.update(k for k, rx in _CUE_RX.items() if rx.search(near))
    return out


def _near_base(texts: list[str]) -> Counter:
    """How often each row sits beside the engine's own tools: the same count around each engine tool's first mention,
    in an even sample of BASE_SAMPLE postings. Read once per learn."""
    tools, out = resume._BUILTIN_TERMS, Counter()
    for x in texts[::max(1, len(texts) // BASE_SAMPLE)]:
        for t, trx in tools.items():
            m = trx.search(x)
            if m:
                out += _beside(*_clause(x, m.start(), m.end()), {u: r for u, r in tools.items() if u != t})
    return out


def _row(rx: re.Pattern, texts: list[str], hits: list[int], base: Counter, families: dict, own) -> str:
    """The skills row a tool belongs on: the row whose tools and cue words sit beside its mentions more than they sit
    beside the engine's own tools, by the widest margin. Each distinct context counts once, so a paragraph an employer
    repeats in every posting is one vote. With nothing beside it, the row follows the postings' family."""
    votes, seen = Counter(), set()
    for i in hits:
        for m in own(rx, i):
            ctx = _clause(texts[i], m.start(), m.end())
            if ctx not in seen:
                seen.add(ctx)
                votes += _beside(*ctx, resume._BUILTIN_TERMS)
    total, all_total = sum(votes.values()), sum(base.values()) or 1
    if total:
        return max(votes, key=lambda k: votes[k] / total - base[k] / all_total)
    return next((_FAMILY_ROW[f] for f in families if f in _FAMILY_ROW), "data")


def _label(rx: re.Pattern, hits: list[int], read: Counter, fallback: str, own) -> str:
    """The spelling postings write most, among those with a capital ("PowerPoint", never "powerpoint"); when postings
    write two spellings equally, the one the reads use most."""
    seen = Counter(" ".join(m.group(0).split()) for i in hits for m in own(rx, i))
    styled = [s for s in seen if re.search(r"[A-Z]", s)]
    return max(styled, key=lambda s: (seen[s], read.get(s, 0), s)) if styled else fallback


# ---------- names mined from every line of every posting ----------
# "Python, Kubeflow, Spark and Airflow", "(e.g., Snowflake, ClickHouse, BigQuery)", "Jira/Confluence/Git": a list that
# names two or more of the engine's own tools is a list of tools, so its other capitalized names are very likely tools
# too. Only the engine's tools count as neighbors, not its practices and fields: "Statistics, Machine Learning,
# Economics" is a list of topics.

PRACTICES = frozenset("etl nosql data_modeling data_warehouse data_lake streaming web_scraping relational_db metadata "
                      "data_governance ci_cd rest_api distributed_systems automated_testing sdlc agile observability ml "
                      "deep_learning statistics forecasting ab_testing nlp mlops computer_vision time_series "
                      "causal_inference fine_tuning llm rag agents evals prompt_engineering ai_coding".split())
VENDOR_SHARE = 0.5  # a one-word name that opens a longer one this often is a vendor's or a family's ("Google Cloud")

# A list lives in one line or sentence; "e.g. Snowflake" and "i.e. BigQuery" don't end one
_SENTENCE = re.compile(r"[\n•●▪◦]|(?<=[.!?])(?<!\be\.g\.)(?<!\bi\.e\.)\s+(?=[A-Z(\"'])")
# What separates its items: punctuation, "and"/"or", spaced dashes and the words that introduce examples
_ITEM = re.compile(r"[,;:()\[\]{}|]|\s(?:[-–—]\s|(?:and/or|and|or|nor|plus|versus|vs\.?|such as|including|incl\.|like|"
                   r"e\.g\.|i\.e\.|for example|for instance|especially|particularly|as well as|etc\.?)(?!\w))", re.I)
_LEAD = re.compile(r"^(?:(?:and|or|e\.?g|i\.?e|etc|also|both|either|plus|preferably|ideally|the|a|an|any)(?![\w/&-])"
                   r"\.?,?\s*)+", re.I)
_SLASH, _SLASHED = re.compile(r"\s+/\s*|\s*/\s+"), re.compile(r"\S+/\S+")
# A short lead-in that ends in a connector ("experience with", "strong knowledge of"), and a short lower-case tail
_LEAD_IN = re.compile(r"^(?:\S+\s+){0,4}(?:with|in|of|on|using|via|for|from|to|as|into|leveraging|utilizing)\s+(?=\S)")
_TAIL = re.compile(r"(?:\s+[a-z][\w'’-]*){1,3}$")  # "Kubeflow experience", "Kubeflow is a plus"
_FUNCTION = re.compile(rf"\s(?:{'|'.join(sorted(_FUNCTION_WORDS - {'&'}))})\s")
# Not a name: an adjective ("AI-powered", "Hands-on"), an acronym in the plural ("VLMs", "SLAs"), "You'll", "Master's"
_NOT_NAME = re.compile(r"\w-[a-z]+\b|\b[A-Z]{2,}s\b|['’][a-z]{1,2}\b")
_CAPS = re.compile(r"[A-Z0-9]")
_PLAIN = re.compile(rf"[A-Z][a-z]+(?:-[a-z]+)*|{'|'.join(sorted(_FUNCTION_WORDS))}")  # "Access", "Hands-on"
_OPENS = set("\n.!?:;•●▪◦*·-–—\"'“‘>|")  # a word just after these is capitalized whatever it is
_BEFORE_NAME, _WORD = re.compile(r"[ \t]+[A-Z0-9]"), re.compile(r"[\w-]")


@cache
def _engine():
    """The engine's own tools as one pattern, its practices as another, and the names of both."""
    def table(keys):
        return re.compile("|".join(f"(?:{resume._BUILTIN_TERMS[k].pattern})" for k in keys) or "(?!)", re.I)
    names = {n: t for t in resume._BUILTIN_TERMS for n in (resume.label(t).lower(), resume.item_label(t).lower())}
    return table(k for k in resume._BUILTIN_TERMS if k not in PRACTICES), table(PRACTICES), names


@cache
def _known(item: str) -> int:
    """2 when an item names one of the engine's tools, 1 when only one of its practices, else 0: the test of
    resume.keyword_terms(item, learned=False), with all its patterns at once."""
    tools, practices, names = _engine()
    name = names.get(" ".join(item.lower().split()))
    if tools.search(item) or (name and name not in PRACTICES):
        return 2
    return 1 if name or practices.search(item) else 0


def _items(sentence: str) -> list[str]:
    """A line or sentence cut into its items. "Jira/Confluence/Git" is three; "PL/SQL", "CI/CD" and "Pub/Sub" stay one."""
    out = []
    for piece in _ITEM.split(_SLASH.sub("/", sentence)):
        piece = _LEAD.sub("", piece.strip().lstrip("*-–—>~·").rstrip(".!?*").strip(" '\"“”‘’"))
        for token in _SLASHED.findall(piece):
            sides = token.split("/")
            if all(len(key_of(s)) >= 3 for s in sides) and (not _known(token) or any(map(_known, sides))):
                piece = piece.replace(token, "\n".join(sides), 1)
        out += [s.strip() for s in piece.split("\n") if s.strip()]
    return out


@cache
def _name_in(item: str) -> str:
    """The name an item gives a tool, without a short lead-in or tail ("experience with Kubeflow" gives Kubeflow), or ""."""
    if not _CAPS.search(item) or item.count(" ") > 11:
        return ""
    rest = _LEAD_IN.sub("", item, count=1)
    for name in (rest, _TAIL.sub("", rest), item, _TAIL.sub("", item)):
        if tool_like(name) and len(key_of(name)) >= 2 and not _FUNCTION.search(f" {name} ") and not _NOT_NAME.search(name):
            return name
    return ""


def listed_names(text: str):
    """(name, sentence) for each name in a posting that the engine doesn't know, listed beside two or more of its tools."""
    for sentence in _SENTENCE.split(text or ""):
        if sum(map(sentence.count, ",;/(")) + sentence.count(" and ") + sentence.count(" or ") < 2:
            continue  # a list of three or more items has two separators
        items = _items(sentence)
        names = list(map(_name_in, items))
        if names and names[0] and items[0].startswith(names[0]) and all(_PLAIN.fullmatch(w) for w in names[0].split()):
            names[0] = ""  # "Strong", "Familiarity": a plain word that opens a line is capitalized because it opens it
        new = [n for i, n in zip(items, names) if n and not _known(n) and not _known(i)]
        tools = (i for i in items if _known(i) == 2)
        if new and next(tools, None) and next(tools, None):  # two of the engine's tools, at least
            yield from ((n, sentence) for n in new if not resume.synonym_tools(n))


def _word_first(names: list[str], texts: list[str], lows: list[str]) -> bool:
    """Whether a name made of dictionary-style words is a plain word too: more postings write it in lower case than
    capitalize it inside a sentence (PLAIN_SHARE). A sentence or bullet capitalizes its first word, so that says
    nothing: "Strong" opens a thousand bullets, but Ray is capitalized mid-sentence and "ray" is rare."""
    if not all(_PLAIN.fullmatch(w) for n in names for w in n.split()):
        return False

    def written(rxs, text: str, inside: bool) -> bool:
        for rx in rxs:
            for m in rx.finditer(text):
                a, b = m.span()
                if (a and (text[a - 1].isalnum() or text[a - 1] == "-")) or (b < len(text) and text[b].isalnum()):
                    continue
                while a and text[a - 1].isspace() and text[a - 1] != "\n":
                    a -= 1
                if not inside or (a and text[a - 1] not in _OPENS):
                    return True
        return False
    caps = [re.compile(r"[\s-]+".join(map(re.escape, n.split()))) for n in names]
    lower = [re.compile(r"[\s-]+".join(map(re.escape, n.lower().split()))) for n in names]
    first = names[0].split()[0].lower()
    some = [t for t, low in zip(texts, lows) if first in low]
    n_caps, n_lower = sum(written(caps, t, True) for t in some), sum(written(lower, t, False) for t in some)
    return n_lower > PLAIN_SHARE * max(1, n_caps)


def _vendor(names: list[str], corpus: str) -> bool:
    """Whether a one-word name mostly opens a longer one, as a vendor's or a family's does ("Google Cloud", "Microsoft
    Excel", "Delta Lake", "REST APIs"): more than VENDOR_SHARE of the times postings write it. Its tools are learned
    by their full names."""
    if any(" " in n for n in names):
        return False
    ends = [m.end() for n in names for m in re.finditer(re.escape(n), corpus)
            if not (corpus[m.start() - 1].isalnum() or corpus[m.start() - 1] == "-") and not _WORD.match(corpus, m.end())]
    return sum(bool(_BEFORE_NAME.match(corpus, e)) for e in ends) > VENDOR_SHARE * len(ends)


def mine(posts: list[dict], texts: list[str] | None = None) -> dict[str, dict]:
    """{key: {spellings, postings, employers}}: the names postings list beside the engine's own tools, read from every
    line of every posting, kept when MIN_POSTINGS postings from MIN_EMPLOYERS employers list them so and they aren't
    plain words or vendors' names. A posting never counts a name its employer's own name holds ("Epic" at Epic Games).
    `texts`: the postings' texts without their employers' names, when the caller has them already."""
    texts = texts if texts is not None else [resume.without_employer(p["text"], p["company"]) for p in posts]
    found: dict[str, dict] = {}
    for i, (p, text) in enumerate(zip(posts, texts)):
        for name, _ in listed_names(text):
            key = key_of(name)  # "AI/ML" is two kinds of software, not a tool
            if all(key_of(s) in NOT_TOOLS for s in name.split("/")) or key in resume._BUILTIN_TERMS \
                    or own_name([name], p["company"]):
                continue
            c = found.setdefault(key, {"spellings": Counter(), "posts": set(), "employers": set()})
            c["spellings"][name] += 1
            c["posts"].add(i)
            c["employers"].add(_firm(p["company"]))
    lows, corpus = [t.lower() for t in texts], "\n" + "\n".join(texts)  # every name has a character before it
    out = {}
    for key, c in found.items():
        names = [n for n, _ in c["spellings"].most_common()]
        if len(c["posts"]) >= MIN_POSTINGS and len(c["employers"]) >= MIN_EMPLOYERS \
                and not _word_first(names, texts, lows) and not _vendor(names, corpus):
            out[key] = {"spellings": c["spellings"], "postings": len(c["posts"]), "employers": len(c["employers"])}
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["postings"], kv[0])))


def learn(reads: dict[str, list[dict]], posts: list[dict], previous: dict | None = None) -> dict:
    """The lexicon: what `previous` learned, plus every candidate the postings show is a shared tool. `skipped` lists
    the candidates left out, with why."""
    found = candidates(reads)
    texts = [resume.without_employer(p["text"], p["company"]) for p in posts]  # an employer naming itself isn't a tool
    for key, m in mine(posts, texts).items():  # and the names every line of every posting lists beside known tools
        found.setdefault(key, {"spellings": Counter(m["spellings"]), "reads": 0, "from": Counter()})
    lows = [t.lower() for t in texts]
    base = _near_base(texts)
    kept: dict[tuple, list] = {}

    def own(rx: re.Pattern, i: int) -> list[re.Match]:
        """A tool's matches in posting i, leaving out those inside a match of the engine's own tools, as the engine
        reads them (resume._matches): the "GitHub" in GitHub Actions, the "Sub" in Pub/Sub. Only the text around each
        match is searched, since an engine tool's name that holds it can't reach further."""
        if (rx.pattern, i) not in kept:
            out = []
            for m in rx.finditer(texts[i]):
                a = max(0, m.start() - AROUND)
                near = texts[i][a:m.end() + AROUND]
                if not any(s.start() + a <= m.start() and m.end() <= s.end() + a
                           for brx in resume._BUILTIN_TERMS.values() for s in brx.finditer(near)):
                    out.append(m)
            kept[(rx.pattern, i)] = out
        return kept[(rx.pattern, i)]
    terms = {k: v for k, v in ((previous or {}).get("terms") or {}).items() if isinstance(v, dict)}
    skipped, today = {}, date.today().isoformat()
    for key, c in sorted(found.items()):
        names = [n for n, _ in c["spellings"].most_common()]
        slug = re.sub(r"[^a-z0-9]", "", key) or key
        ev = _evidence(names, posts, texts, lows, own)
        if ev["why_not"] or slug in resume._BUILTIN_TERMS:
            skipped[names[0]] = ev["why_not"] or "the engine knows it"
            continue
        old = terms.get(slug, {})
        names = sorted(set(names) | set(old.get("spellings") or []), key=lambda n: -c["spellings"].get(n, 0))
        rx = re.compile(cased(names, ev["proper"]), re.I)
        terms[slug] = {"label": _label(rx, ev["hits"], c["spellings"], names[0], own), "spellings": names, "pattern": rx.pattern,
                       "category": _row(rx, texts, ev["hits"], base, ev["families"], own), "reads": c["reads"],
                       "postings": ev["postings"], "employers": ev["employers"], "families": ev["families"],
                       "required": c["from"]["required"], "preferred": c["from"]["preferred"],
                       "first_learned": old.get("first_learned") or today,
                       **({"status": old["status"]} if old.get("status") else {})}
    return {"version": 1, "learned_on": today, "reads": len(reads), "postings": len(posts),
            "terms": dict(sorted(terms.items())), "skipped": dict(sorted(skipped.items()))}


def load(path: Path = PATH) -> dict | None:
    try:
        lex = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return lex if isinstance(lex, dict) else None


def save(lex: dict, path: Path = PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(lex, indent=1))
    tmp.replace(path)  # the page reads it whole or not at all


def update(profile: dict) -> dict:
    """Learn from every cached read and every job board read so far, keep it in data/lexicon.json, and return it."""
    lex = learn(read_keywords(), board_postings(profile.get("roles") or ()), load())
    save(lex)
    return lex
