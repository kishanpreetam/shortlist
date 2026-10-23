"""Tailor a resume to one job description, using only lines you approved.

Every line on a tailored resume comes, character for character, from your facts file
(profile `resume_facts`), and only from entries you approved:

  - Your lines: bullets, summaries, titles, education and skills lines.
  - Versions of your bullets in one job type's language, suggested and then approved by you.
    A version keeps your line's numbers and claims no more scope; it may name a tool or theme
    your line doesn't only when it lists that term under `adds`, which you saw when you
    approved it. `faithful()` checks this, and a version that fails is never used.
  - Rewrites of your bullets for one job by Claude (rewrite.py), kept only where `rewrite_problems()`
    finds nothing: your line's numbers on the same words and in the same order, and no tool, term,
    scope or other word your line doesn't have beyond plain resume words (GENERIC). Otherwise your
    own line is used.
  - Skills: your approved skills lines, plus skills the job names that your approved lines
    show you used (an ETL bullet backs "ETL"), plus skills you added yourself, as used or on
    a "Currently learning" line.

Tailoring decides which lines appear, which version of each, and in what order:
  1. Terms: the job description's tools and skills (classify.SKILLS plus EXTRA_SKILLS) and
     themes such as dashboards, stakeholders or data quality (THEMES below).
  2. Versions: each bullet is represented by its approved version that shares the most
     weight with the job; a tie goes to your own wording unless the version was written for
     this resume's job type.
  3. Pick: every approved role keeps its best bullet and stays in date order; the best
     projects follow; more bullets are added by score until the page is full, then the
     lowest-scoring optional ones are trimmed until the PDF is exactly one page.
  4. Skills: within each line the job's skills come first; skills the job names that your
     lines back, or that you added, join the line they belong to, and the line drops its
     least relevant items rather than grow.
  5. Title line: one of your approved titles (the closest to theirs, never their wording),
     plus up to 4 of their skills that appear on the page outside the learning line.
  6. Checks, before any PDF is kept: every line identical to an approved line or version,
     every skill approved, backed or added by you, the title approved, the title-line skills
     on the page, no em dashes, one page. Any failure means no file.

Skills the job asks for that you haven't added are reported, never added for you.
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from datetime import date
from functools import lru_cache
from pathlib import Path

from . import classify

THEMES = {
    "dashboards": r"dashboard|reporting\b|reports?\b|visuali[sz]|business intelligence|\bbi\b",
    "stakeholders": r"stakeholder|cross-functional|leadership|executive|non-technical|partner with|business partners",
    "data_quality": r"data quality|validation|integrity|reliab|\bslas?\b",
    "pipelines": r"pipeline|\betl\b|\belt\b|ingest|orchestrat|workflow",
    "modeling": r"model(s|ing)?\b|predict|statistic|forecast",
    "recommendations": r"recommend|personali[sz]|ranking",
    "search": r"\bsearch\b|retrieval|semantic|embedding|vector",
    "agents": r"\bagents?\b|agentic|multi-agent|tool calling|function calling",
    "llm": r"\bllms?\b|large language|generative|gen ?ai|prompt",
    "production": r"production|deploy|microservice|scalab|distributed|\bapis?\b|backend",
    "experimentation": r"a/b|experiment",
    "metrics": r"metric|\bkpis?\b|measur",
    "frontend": r"frontend|front-end|full[- ]stack|\breact\b|next\.?js|user interface|\bui\b",
    "automation": r"automat",
    "research": r"research",
    "leadership": r"\bled\b|\blead(ing)? (a|the) team|mentor",
    "integrations": r"integrat",
    "analysis": r"\banaly[sz]|\binsights?\b|\btrends?\b",
    "extraction": r"\bscrap(e|ed|es|er|ers|ing)\b|extract|unstructured|\bpars(e|es|ed|er|ing)\b",
}
_THEME_RX = {k: re.compile(v, re.I) for k, v in THEMES.items()}
SKILL_WEIGHT, THEME_WEIGHT, REPEAT_BOOST = 2.0, 1.0, 1.5

# Tools that job descriptions name beyond the job search's own list; used only for tailoring.
EXTRA_SKILLS: dict[str, tuple[str, str]] = {
    "redshift": (r"redshift", "Redshift"), "bigquery": (r"bigquery", "BigQuery"),
    "sagemaker": (r"sagemaker", "SageMaker"), "ec2": (r"\bec2\b", "EC2"), "kinesis": (r"kinesis", "Kinesis"),
    "hadoop": (r"hadoop|\bhdfs\b", "Hadoop"), "hive": (r"\bhive\b", "Hive"), "flink": (r"\bflink\b", "Flink"),
    "dagster": (r"dagster", "Dagster"), "fivetran": (r"fivetran", "Fivetran"), "iceberg": (r"\biceberg\b", "Apache Iceberg"),
    "delta_lake": (r"delta lake", "Delta Lake"), "trino": (r"\btrino\b", "Trino"), "presto": (r"\bpresto\b", "Presto"),
    "duckdb": (r"duckdb", "DuckDB"), "polars": (r"\bpolars\b", "Polars"), "mysql": (r"\bmysql\b", "MySQL"),
    "sql_server": (r"sql server|\bt-sql\b|\bmssql\b", "SQL Server"), "oracle": (r"\boracle\b", "Oracle"),
    "redis": (r"\bredis\b", "Redis"), "elasticsearch": (r"elasticsearch|elastic search", "Elasticsearch"),
    "opensearch": (r"opensearch", "OpenSearch"), "mongodb": (r"mongo ?db", "MongoDB"), "dynamodb": (r"dynamo ?db", "DynamoDB"),
    "data_warehouse": (r"data warehous", "Data Warehousing"), "data_lake": (r"data lake", "Data Lakes"),
    "streaming": (r"stream processing|streaming data|real-time streaming|event streaming", "Stream Processing"),
    "supabase": (r"supabase", "Supabase"), "salesforce": (r"salesforce", "Salesforce"),
    "web_scraping": (r"web scraping|scrapy|beautiful ?soup", "Web Scraping"),
    "playwright": (r"playwright", "Playwright"), "pytest": (r"pytest", "pytest"),
    "mlflow": (r"mlflow", "MLflow"), "mlops": (r"\bmlops\b|\bml ops\b", "MLOps"), "huggingface": (r"hugging ?face", "Hugging Face"),
    "fine_tuning": (r"fine[- ]?tun", "Fine-Tuning"), "computer_vision": (r"computer vision|opencv", "Computer Vision"),
    "time_series": (r"time[- ]series", "Time Series"), "causal_inference": (r"causal inference", "Causal Inference"),
    "sas": (r"\bsas\b", "SAS"), "stata": (r"\bstata\b", "Stata"), "matlab": (r"matlab", "MATLAB"), "spss": (r"\bspss\b", "SPSS"),
    "alteryx": (r"alteryx", "Alteryx"), "google_analytics": (r"google analytics|\bga4\b", "Google Analytics"),
    "amplitude": (r"\bamplitude\b", "Amplitude"), "mixpanel": (r"mixpanel", "Mixpanel"), "qlik": (r"qlik", "Qlik"),
    "sheets": (r"google sheets", "Google Sheets"), "vba": (r"\bvba\b", "VBA"),
    # the model makers' products, not their names: an Anthropic or OpenAI posting names the employer, not a skill
    "openai": (r"openai('s)? (api|sdk|models?)", "OpenAI API"),
    "anthropic": (r"\bclaude\b(?! code)|anthropic('s)? (api|sdk|models?)", "Anthropic Claude API"),
    "ai_coding": (r"claude code|github copilot|\bcopilot\b|\bcodex\b|ai coding (tools?|assistants?)|ai-assisted (coding|development)", "AI Coding Tools"),
    "bedrock": (r"bedrock", "Amazon Bedrock"), "vertex_ai": (r"vertex ai", "Vertex AI"),
    "crewai": (r"crew ?ai\b", "CrewAI"), "autogen": (r"autogen", "AutoGen"), "prompt_engineering": (r"prompt engineering", "Prompt Engineering"),
    "pydantic": (r"pydantic", "Pydantic"), "streamlit": (r"streamlit", "Streamlit"), "jenkins": (r"jenkins", "Jenkins"),
    "graphql": (r"graphql", "GraphQL"), "grafana": (r"grafana", "Grafana"), "datadog": (r"datadog", "Datadog"),
    "numpy": (r"numpy", "NumPy"), "jupyter": (r"jupyter", "Jupyter"), "bash": (r"\bbash\b|shell script", "Bash"),
    "csharp": (r"\bc#|\basp\.net\b|\.net (framework|core)", "C#"), "rust": (r"\brust\b", "Rust"),
    "html_css": (r"\bhtml5?\b|\bcss3?\b", "HTML/CSS"),
    "ui_bakery": (r"ui bakery", "UI Bakery"), "google_workspace": (r"google workspace", "Google Workspace"),
    "pyspark": (r"\bpyspark\b", "PySpark"), "azure_data_factory": (r"azure data factory|\badf\b(?= pipelines?|,| or| and|\))", "Azure Data Factory"),
    "adls": (r"azure data lake( storage)?|\badls( ?gen ?2)?\b", "Azure Data Lake Storage"),
    "fabric": (r"microsoft fabric|\bfabric (spark|notebooks?|lakehouse|pipelines?|data ?warehouse|workspace)", "Microsoft Fabric"),
    "synapse": (r"\bsynapse\b", "Azure Synapse"), "event_hubs": (r"event ?hubs?\b", "Azure Event Hubs"),
    "azure_devops": (r"azure devops", "Azure DevOps"), "snowpipe": (r"snowpipe", "Snowpipe"), "snowsql": (r"snowsql", "SnowSQL"),
    "informatica": (r"informatica", "Informatica"), "talend": (r"\btalend\b", "Talend"), "ssis": (r"\bssis\b", "SSIS"),
    "relational_db": (r"relational databases?|\brdbms\b|\bsql databases\b", "Relational Databases"),
    "metadata": (r"\bmetadata\b", "Metadata Management"),
}
_TERM_RX = dict(classify._SKILL_RX)
_TERM_RX.update({k: re.compile(p, re.I) for k, (p, _) in EXTRA_SKILLS.items()})
_TERM_RX["excel"] = re.compile(r"(?-i:\bExcel\b)|microsoft excel|\bms excel", re.I)  # the tool, not "the chance to excel"
_TERM_RX["rest_api"] = re.compile(r"\brest(ful)?\s*apis?\b|\brest(ful)? (endpoints?|services?)\b|\bapis\b", re.I)  # not "the Claude API"

# The kind of skills line each skill joins when a job names it (a line's `kind` in your facts file).
CATEGORY = {t: kind for kind, names in {
    "code": "python sql typescript javascript java scala go cpp r_lang git nextjs react csharp rust bash html_css numpy pandas polars jupyter "
            "playwright pytest",
    "data": "spark airflow etl dbt kafka snowflake databricks postgres nosql data_modeling redshift bigquery hadoop hive flink dagster "
            "fivetran iceberg delta_lake trino presto duckdb mysql sql_server oracle redis elasticsearch opensearch mongodb dynamodb "
            "data_warehouse data_lake streaming supabase kinesis web_scraping salesforce google_workspace pyspark azure_data_factory "
            "adls fabric synapse event_hubs snowpipe snowsql informatica talend ssis relational_db metadata",
    "cloud": "aws gcp azure docker kubernetes terraform ci_cd fastapi flask django rest_api linux sagemaker ec2 jenkins graphql grafana "
             "datadog vertex_ai bedrock pydantic streamlit ui_bakery azure_devops",
    "ml": "ml deep_learning pytorch tensorflow scikit_learn xgboost statistics forecasting ab_testing tableau power_bi looker excel nlp "
          "mlflow mlops computer_vision time_series causal_inference sas stata matlab spss alteryx google_analytics amplitude mixpanel "
          "qlik sheets vba huggingface fine_tuning",
    "ai": "llm rag agents langchain mcp evals vector_db openai anthropic crewai autogen prompt_engineering ai_coding",
}.items() for t in names.split()}
# How a skill reads on a skills line, when its short label doesn't (each still matches its own pattern)
ITEM_LABELS = {"ml": "Machine Learning", "deep_learning": "Deep Learning", "data_modeling": "Data Modeling", "statistics": "Statistics",
               "ab_testing": "A/B Testing", "evals": "LLM Evals", "vector_db": "Vector Databases", "agents": "AI Agents",
               "forecasting": "Forecasting", "go": "Go (Golang)", "pandas": "pandas"}
LEARNING_LINE = "Currently learning"

# Which approved title is closest to a job title, in order; the first rule whose pattern
# matches and whose title you approved wins, else the resume version's own title.
TITLE_RULES = [
    (r"analytics engineer", "Analytics Engineer"),
    (r"data engineer|data platform|data infrastructure|\betl\b", "Data Engineer"),
    (r"machine learning|\bml\b|mlops", "Machine Learning Engineer"),
    (r"data scien|applied scien", "Data Scientist"),
    (r"forward deployed", "Forward Deployed Engineer"),
    (r"\bai\b|artificial intelligence|\bllm|gen ?ai|generative|agent", "AI Engineer"),
    (r"business analyst|systems analyst|business systems", "Business Analyst"),
    (r"analyst|business intelligence|\bbi\b|reporting|insights", "Data Analyst"),
    (r"software|developer|engineer", "Software Engineer"),
]
VARIANT_TITLE = {"ai-engineer": "AI Engineer", "data-engineer": "Data Engineer",
                 "data-scientist": "Data Scientist", "data-analyst": "Data Analyst"}
MAX_PER_ROLE, MAX_PER_PROJECT, MAX_PROJECTS = 6, 2, 2
_DEGREE = re.compile(r"\b(?:degree|bachelor'?s?|master'?s?|ph\.?d|doctorate|b\.?s\.?|m\.?s\.?|b\.?tech|m\.?tech)\b", re.I)  # shown by Education
REQ_WEIGHT = {"required": 3.0, "responsibility": 2.0, "preferred": 1.0}
SPREAD_MAX = 8  # up to this many required items, each is pulled into both experience and projects; beyond it, one line each  # how much a line counts for each requirement it shows
NUMBER_BONUS = 1.0  # on a tailored resume a line with a number beats an otherwise equal one: numbers make a point land
ADDED_TOOL_MAX = 2  # a tool a rewrite adds to your wording goes in at most this many lines and tools lines of a page
EXPERIENCE_PRIOR = 0.8    # with equal fit, a work bullet beats a project bullet
SAME_KIND_PRIOR = 1.0     # a role whose title is the job's kind (an analyst role for an analyst job) leads
_KIND = re.compile(r"\b(analyst|engineer|scientist|developer|consultant|manager|researcher|assistant)s?\b", re.I)
# Vertical rhythm in points (Charter; experience headers 9.5 pt, bullets and tools lines 8.6 pt, everything
# else 8.8 pt on 10.5 pt lines; Letter with about 0.35 in margins), so the page holds as many of your lines as it can
PAGE_PT = 743             # top margin to the lowest baseline that still fits
FIRST_LINE_PT = 45.8      # top margin to the first summary line: name, contact line, first heading
SECTION_PT, PROJECT_SECTION_PT = 27.2, 25.5   # a section's last line to the next section's first line
LINE10_PT, ENTRY_HEAD_PT, LINE9_PT, BULLET_GAP_PT = 10.5, 11.25, 10.2, 0.0
TOOLS_TO_BULLET_PT, HEAD_TO_BULLET_PT = 9.0, 10.0   # an entry's tools line, or its header, to its first bullet
ENTRY_GAP_PT, PROJECT_GAP_PT, EDU_GAP_PT = 4.8, 3.6, 0.75
SUMMARY_CHARS, SKILL_CHARS, BULLET_CHARS, TECH_CHARS = 130, 104, 129, 120   # SKILL_CHARS: a full skills row
NO_FILL = {"rest_api", "etl", "ml", "llm", "agents", "forecasting", "statistics"}  # too general to pad a skills row with
BUDGETS = tuple(PAGE_PT + d for d in (44, 36, 28, 20, 12, 4, -4, -12, -20, -32, -44))  # fullest first; the PDF decides
LENS_BONUS = 0.25         # a version written for this resume's job type wins a tie with your own wording
BANNED = ("—", "--")
# Never in a rewrite for one job: arrows and the other long dashes (the em dash and double hyphen are banned everywhere),
# nor a character your line doesn't have beyond letters, digits, spaces and plain punctuation
REWRITE_BANNED = re.compile(r"[\u2190-\u21ff\u2794-\u27bf\u27f0-\u27ff\u2900-\u297f\u2b00-\u2bff\u2012\u2013\u2015]|[-=]\>|\<-")
_PLAIN = re.compile(r"[\w\s.,;:!?()'\"\u2019%$+&/-]")

# Faithfulness: what a version of your line may not change
_NUM = re.compile(r"(?<![A-Za-z0-9])(?<![A-Za-z]-)\$?\d[\d,]*(?:\.\d+)?(?:[KMB](?![A-Za-z]))?\+?%?")
_DASH = r"[\s\u2010-\u2014-]"  # a space or any hyphen or dash, as in "end to end" or "end-to-end"
_SCOPE = re.compile(r"\b(led|leads?|leading|manag(?:e|ed|es|ing)|own(?:ed|s|ing)?|owners?|ownership|architect(?:ed|s|ing)?|"
                    r"architectur(?:e|es|al)|spearhead(?:ed|s|ing)?|direct(?:ed|s|ing)|supervis(?:e|ed|es|ing)|mentor(?:ed|s|ing)?|"
                    r"head(?:ed|s|ing)|oversaw|oversee(?:s|ing)?|sole|solely|solo|independently|entire|"
                    rf"single{_DASH}?handed(?:ly)?|end{_DASH}+to{_DASH}+end|(?:company|organi[sz]ation|org|enterprise){_DASH}?wide)\b", re.I)
_SPELLED = re.compile(r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
                      r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|"
                      r"millions?|billions?|dozens?|twice|doubled?|tripled?|half|halved|percent)\b", re.I)
_APPROX = r"(?:(?i:about|around|roughly|nearly)\s+)?"
# A change your line states in order: "from 20+ minutes to about 25 seconds", "70% to 90%", "20-30"
_PAIR = re.compile(rf"(?i:\bfrom)\s+{_APPROX}({_NUM.pattern})(?:\s+[A-Za-z][\w'-]*){{0,4}}?\s+(?i:to)\s+{_APPROX}({_NUM.pattern})"
                   rf"|({_NUM.pattern})(?:\s+(?i:to)\s+{_APPROX}|\s*[-\u2013]\s*)({_NUM.pattern})")
_PAIR_REVERSE = re.compile(rf"(?i:\bto)\s+{_APPROX}({_NUM.pattern})(?:\s+[A-Za-z][\w'-]*){{0,4}}?\s+(?i:from)\s+{_APPROX}({_NUM.pattern})")
_SCOPE_VERBS = {"lead", "manag", "own", "architect", "spearhead", "direct", "supervis", "mentor", "head", "overs"}  # as _stem reads them
_ACTION = re.compile(r"(?:^|[.!?;:,(]|\band)\s*$", re.I)  # what comes right before a line's own action verb
_CLAUSE = re.compile(r"[.,;:!?()\[\]]|" + _NUM.pattern)  # where the words that say what a number counts end
_LEAD_NUM = re.compile(r"^\d[\d,.]*(?:[KMB](?![A-Za-z]))?")
# Words with no fact in them, which a rewrite may add. Not here: people and scope (users, teams, stakeholders,
# leadership), domains, tools, numbers, quantities (all, every, many) and negations. A word that names a term
# (deployed, reporting, workflow) must still name one of your line's own terms.
_FUNCTION = frozenset("""a an the this that these those it its their them they which who whom whose what where when while
    whereas whether if then than so as such of in on at to for from by with within into onto over under across through
    throughout via per about around among between during before after along alongside toward towards upon up out off like
    including and or but yet because although though plus also is are was were be been being has have had do does did
    can could will would should may might must""".split())
GENERIC = _FUNCTION | frozenset("""method methods versus lift lifted lifting languages technologies frameworks platforms systems sources categories

    build built building develop developed design designed create created implement implemented deliver delivered deploy
    deployed engineer engineered produce produced generate generated prepare prepared enable enabled support supported
    improve improved reduce reduced increase increased cut cutting lower lowered raise raised transform transformed convert
    converted process processed partner partnered collaborate collaborated work worked use used using leverage leveraged
    apply applied run ran running write wrote written writing maintain maintained enhance enhanced extend extended optimize
    optimized streamline streamlined translate translated explain explained present presented communicate communicated
    review reviewed track tracked monitor monitored identify identified highlight highlighted summarize summarized examine
    examined compare compared define defined align aligned ensure ensured help helped make made making turn turned provide
    provided power powered feed fed serve served combine combined join joined connect connected organize organized structure
    structured clean cleaned refine refined standardize standardized consolidate consolidated document documented launch
    launched ship shipped shipping test tested validate validated achieve achieved reach reached allow allowed replace
    replaced handle handled keep kept show showed shown surface surfaced flag flagged load loaded store stored pull pulled
    gather gathered collect collected update updated configure configured establish established check checked
    cross-check cross-checked explore explored bring brought
    data dataset records rows workflow process step task report reporting view tool system feature result output input
    source flow quality accuracy reliability consistency efficiency speed visibility availability manual internal shared
    existing raw structured clean curated live real-time daily weekly monthly faster information detail application app
    database findings observations
    """.split())
# Words whose theme a weaker cue matches ("API" reads as production work, "reports" as dashboards): a rewrite may
# use them only when your line says this
_NEEDS = {"production": r"production|deploy", "dashboard": r"dashboard"}


# ---------- facts ----------

def load_facts(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def save_facts(path: Path, facts: dict) -> None:
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(facts, indent=1, ensure_ascii=False))
    tmp.replace(path)


def ok(entry: dict) -> bool:
    return entry.get("status") == "approved"


def pending_count(facts: dict) -> int:
    """Your own lines still waiting for review (suggested versions are counted by pending_versions)."""
    n = sum(not ok(facts[k]) for k in ("contact", "titles"))
    n += sum(not ok(x) for k in ("summaries", "education", "skills") for x in facts[k])
    for group in ("roles", "projects"):
        for g in facts[group]:
            n += (not ok(g)) + sum(not ok(b) for b in g["bullets"])
    return n


def pending_versions(facts: dict) -> int:
    """Suggested versions waiting for your OK (versions that change a fact can't be approved, so don't count)."""
    return sum(a.get("status") == "pending" and not faithful(b["text"], a) for grp in ("roles", "projects")
               for g in facts[grp] for b in g["bullets"] for a in b.get("alts") or [])


# ---------- terms ----------

def terms(text: str) -> dict[str, float]:
    """{term: weight}: tools and skills weigh 2, themes 1, x1.5 when mentioned twice or more."""
    out = {}
    for key, rx in _TERM_RX.items():
        n = len(list(rx.finditer(text or "")))
        if n:
            out[key] = SKILL_WEIGHT * (REPEAT_BOOST if n > 1 else 1)
    for key, rx in _THEME_RX.items():
        n = len(list(rx.finditer(text or "")))
        if n:
            out["theme:" + key] = THEME_WEIGHT * (REPEAT_BOOST if n > 1 else 1)
    return out


def vocabulary() -> list[str]:
    return list(_TERM_RX)


def label(term: str) -> str:
    from .apply import SKILL_LABELS
    if term.startswith("theme:"):
        return term[6:].replace("_", " ")
    if term in EXTRA_SKILLS:
        return EXTRA_SKILLS[term][1]
    return SKILL_LABELS.get(term, term.replace("_", " "))


def item_label(term: str) -> str:
    """The skill as it reads on a skills line."""
    return ITEM_LABELS.get(term) or label(term)


_SUFFIX = re.compile(r"[,.]?\s+(inc|llc|l\.l\.c|corp|corporation|co|company|ltd|plc|technologies|group|holdings)\.?$", re.I)


def without_employer(text: str, employer: str) -> str:
    """The job description minus the employer's own name, so "Snowflake" at Snowflake isn't read as a skill they ask for."""
    name = _SUFFIX.sub("", (employer or "").strip())
    if len(name) < 3:
        return text or ""
    return re.sub(rf"(?<![\w-]){re.escape(name)}(?:'s)?(?![\w-])", " ", text or "", flags=re.I)


def _score(text: str, job: dict[str, float]) -> tuple[float, list[str]]:
    mine = terms(text)
    shared = [t for t in mine if t in job]
    return sum(job[t] for t in shared), shared


def _item_terms(items: list[str]) -> set[str]:
    """Skill terms a list of skills-line items covers (by pattern, or by the item reading exactly as a skill's label)."""
    by_label = {item_label(t).lower(): t for t in _TERM_RX}
    out = set()
    for it in items:
        out |= {t for t in terms(it) if not t.startswith("theme:")}
        if it.lower() in by_label:
            out.add(by_label[it.lower()])
    return out


# ---------- versions of your lines ----------

def _nums(text: str) -> list[str]:
    return sorted(m.group(0).rstrip(",.") for m in _NUM.finditer(text))


def _pairs(text: str) -> list[tuple[str, str]]:
    """Changes in their factual direction, including "to 90% from 70%"."""
    forward = [((m[1] or m[3]).rstrip(",."), (m[2] or m[4]).rstrip(",.")) for m in _PAIR.finditer(text)]
    reverse = [(m[2].rstrip(",."), m[1].rstrip(",.")) for m in _PAIR_REVERSE.finditer(text)]
    return sorted(forward + reverse)


def _after(text: str) -> dict[str, set[str]]:
    """What each number counts: up to 3 words right after it, before the next number or punctuation ("1M+ rows").
    Nothing when the number ends its phrase, as in "by 35%." or "from 72% to 92% and ..."."""
    out: dict[str, set[str]] = {}
    for m in _NUM.finditer(text):
        rest = "" if m[0].endswith(",") else _CLAUSE.split(text[m.end():], maxsplit=1)[0]
        words = [w.lower() for w in re.findall(r"[A-Za-z]\w*", rest)]
        while words and words[0] in ("of", "the", "a", "an"):
            words.pop(0)
        if words and words[0] in _FUNCTION:
            words = []
        out.setdefault(m[0].rstrip(",."), set()).update([_root(w) for w in words if w not in _FUNCTION][:3])
    return out

def _number_qualifiers(text: str) -> dict[str, set[str]]:
    """Keep estimates and limits attached to their numbers instead of presenting them as exact."""
    out: dict[str, set[str]] = {}
    for match in _NUM.finditer(text):
        qualifier = re.search(r"\b(about|around|roughly|approximately|estimated|nearly|up to|at least|at most|more than|less than)\s*$",
                              text[:match.start()], re.I)
        mark = qualifier[1].lower() if qualifier else ""
        if mark in {"about", "around", "roughly", "approximately", "estimated"}:
            mark = "approximate"
        out.setdefault(match[0].rstrip(",."), set()).add(mark)
    return out
def _stem(word: str) -> str:
    """One form per scope verb, so 'architecting' matches your 'Architected' and 'leads' matches your 'led'."""
    w = {"led": "lead", "oversaw": "oversee"}.get(word.lower(), word.lower())
    for suffix in ("ing", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[:-len(suffix)]
            break
    return w.rstrip("e")


def _scope(text: str) -> list[tuple[str, str, bool]]:
    """Each scope word in a line: as written, without spaces or dashes, and whether it is a verb the line uses as its
    own action (at the start of a sentence or after "and" or a comma: "Owned ...", "Built and led ...")."""
    return [(m[0].lower(), re.sub(_DASH, "", m[0].lower()), _stem(m[0]) in _SCOPE_VERBS and bool(_ACTION.search(text[:m.start()])))
            for m in _SCOPE.finditer(text)]


def _verbs(text: str) -> set[str]:
    """Scope verbs whose every form a line covers: ones it uses as its own action in an inflected form ("Architected",
    "Led", "Managing"). A bare or -s form ("their own", "manage", "heads", "directs") is often a noun or someone
    else's verb, and one mid-phrase ("supervised learning", "a leading retailer") isn't yours, so neither does."""
    return {_stem(k) for _, k, act in _scope(text) if act and re.search(r"(?:ed|ing)$|^(?:led|oversaw)$", k)}


_SCHEDULE = {"monthly": "12", "weekly": "52", "quarterly": "4", "biweekly": "26"}  # times a year
_LIST = re.compile(r"(?:[A-Za-z][\w./+#-]*(?:\s+[A-Za-z][\w./+#-]*){0,3},\s+)+[A-Za-z][\w./+#-]*(?:\s+[A-Za-z][\w./+#-]*){0,3},?\s+and\s+[A-Za-z][\w./+#-]*")


def derived_numbers(text: str) -> set[str]:
    """Numbers a line proves without stating them: how many things it lists ("Salesforce, Google Workspace and SQL
    databases" is 3) and how often a schedule it states runs in a year (monthly is 12). Nothing is estimated."""
    out = set()
    for m in _LIST.finditer(text):
        items = [x.strip() for x in re.split(r",\s+(?:and\s+)?|\s+and\s+", m.group(0)) if x.strip()]
        # only a list of named tools and skills is counted: a plain phrase can hide an "and" and miscount
        if len(items) >= 2 and all(any(not t.startswith("theme:") for t in terms(x)) for x in items):
            out.add(str(len(items)))
    return out | {n for word, n in _SCHEDULE.items() if re.search(rf"\b{word}\b", text, re.I)}


def faithful(original: str, alt: dict, supporting: tuple[str, ...] = ()) -> list[str]:
    """Check original facts, permitting additions only from explicitly approved supporting text."""
    text = str(alt.get("text", ""))
    source = " ".join((original, *supporting))
    why = []
    original_nums, text_nums, source_nums = (Counter(_nums(s)) for s in (original, text, source))
    source_nums += Counter({n: 1 for n in derived_numbers(original)})  # counted or annualized from the line itself
    if original_nums - text_nums or text_nums - source_nums:
        why.append("its numbers differ from your line")
    qualifiers = _number_qualifiers(source)
    qualifiers.update(_number_qualifiers(original))
    stated = _number_qualifiers(text)
    if any("" not in marks and n in stated and not marks & stated[n] for n, marks in qualifiers.items()):
        why.append("it changes an estimate or limit into a different numeric claim")
    spelled = {w.lower() for w in _SPELLED.findall(text)} - {w.lower() for w in _SPELLED.findall(source)}
    if spelled:
        why.append("it spells out a number your line doesn't: " + ", ".join(sorted(spelled)))
    original_pairs, text_pairs, source_pairs = (Counter(_pairs(s)) for s in (original, text, source))
    if original_pairs - text_pairs or text_pairs - source_pairs:
        why.append("it changes the order of your numbers: " + ", ".join(f"{a} to {b}" for a, b in _pairs(original)))
    added = text_nums - original_nums
    if any((added[a] or added[b]) and not text_pairs[(a, b)] for detail in supporting for a, b in _pairs(detail)):
        why.append("it uses only part of a supporting numeric result")
    counts, theirs = _after(source), _after(text)
    counts.update({n: ws for n, ws in _after(original).items() if ws})
    moved = sorted(n for n, ws in counts.items() if ws and n in theirs and not ws & theirs[n])
    if moved:
        why.append("it puts a number on other words than your line: " + ", ".join(moved))
    new = _claims(text) - _claims(source) - set(alt.get("adds") or [])
    if new:
        why.append("it names something your line doesn't: " + ", ".join(label(t) for t in sorted(new)))
    mine, verbs = _scope(source), _verbs(source)
    words, actions = {k for _, k, _ in mine}, {k for _, k, act in mine if act}
    scope = {w for w, k, act in _scope(text) if k not in (actions if act else words) and _stem(k) not in verbs}
    if scope:
        why.append("it claims more scope than your line: " + ", ".join(sorted(scope)))
    if any(x in text for x in BANNED):
        why.append("it has a dash")
    # the tools a rewrite names that your line doesn't don't count against its length: adding several is allowed
    tools_added = sum(len(label(t)) + 2 for t in set(terms(text)) - set(terms(original)) if not t.startswith("theme:"))
    if not (10 <= len(text) <= 450 and 0.6 * len(original) <= len(text) <= 1.35 * len(source) + tools_added):
        why.append("its length is too far from your line's")
    return why


def adds_text(alt: dict) -> str:
    """What a version names that your line doesn't, in words: 'LLMs, LLM evals; wording about extraction'."""
    skills = [label(t) for t in alt.get("adds") or [] if not t.startswith("theme:")]
    ideas = [label(t) for t in alt.get("adds") or [] if t.startswith("theme:")]
    return "; ".join(x for x in (", ".join(skills), ("wording about " + ", ".join(ideas)) if ideas else "") if x)


def rewrite_terms(text: str, extra: set[str] | frozenset = frozenset()) -> set[str]:
    """What a rewrite of your line may name: the line's own terms (so the job's synonyms for them are fine), ETL
    for a line that already says pipelines, and `extra`, the tools you said you used in that line's role
    (mapped_tools). Anything else, dashboards, automation or production included, is a claim your line has to
    make first. A tool's name doesn't make a claim: "OpenAI API" doesn't let a rewrite say production."""
    mine = _claims(text)
    return mine | ({"etl"} if "theme:pipelines" in mine else set()) | set(extra)


def mapped_tools(facts: dict, gid: str) -> set[str]:
    """Tools explicitly listed for an approved role/project or added as used there."""
    listed = {t for grp in ("roles", "projects") for g in facts.get(grp, [])
              if ok(g) and g["id"] == gid for t in _item_terms(g.get("tools") or [])}
    return listed | {a["term"] for a in facts.get("added_skills") or []
                     if ok(a) and a.get("level") == "used" and gid in (a.get("where") or [])}


def supporting_facts(bullet: dict) -> tuple[str, ...]:
    """Only explicitly approved, bullet-specific additional facts may enter automatic rewrites."""
    return tuple(dict.fromkeys(detail["text"].strip() for detail in bullet.get("supporting_facts") or []
                               if isinstance(detail, dict) and ok(detail)
                               and isinstance(detail.get("text"), str) and detail["text"].strip()))


def _root(word: str) -> str:
    """A crude stem for comparing words: 'reviewing', 'reviewed' and 'reviews' all read 'review'."""
    w = word.lower()
    w = {"development": "develop", "deployment": "deploy", "implementation": "implement",
         "configuration": "configure", "integration": "integrate", "simultaneous": "concurrent"}.get(w, w)
    for suffix in ("ing", "ed", "es", "ly", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3 and not (suffix == "s" and w.endswith("ss")):
            w = w[:-len(suffix)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 3 else w


_GENERIC_ROOTS = frozenset(_root(w) for w in GENERIC)


def _compounds(text: str) -> list[str]:
    """Words and hyphenated words ("real-time"), without a possessive 's or a leading number ("10-agent" reads "agent")."""
    out = []
    for c in re.findall(r"[\w'\u2019]+(?:[-\u2010\u2011][\w'\u2019]+)*", text):
        parts = [_LEAD_NUM.sub("", re.sub(r"['\u2019]s?$", "", p)) for p in re.split(r"[-\u2010\u2011]", c)]
        if any(parts):
            out.append("-".join(p for p in parts if p))
    return out


@lru_cache(maxsize=8192)
def _word_terms(word: str) -> frozenset[str]:
    return frozenset(terms(word))


def _claims(text: str) -> set[str]:
    """What a line names and claims: its tools as written, and its themes read with the tools' names set aside, so
    naming "OpenAI API" names a tool without claiming production work."""
    found = set(terms(text))
    tools = {t for t in found if not t.startswith("theme:")}
    bare = text
    for t in tools:
        bare = _TERM_RX[t].sub(" ", bare) if t in _TERM_RX else bare
    return tools | {t for t in terms(bare) if t.startswith("theme:")}


def _new_words(original: str, text: str, extra: set[str] | frozenset = frozenset()) -> list[str]:
    """Words a rewrite adds that are neither plain words (GENERIC), nor one of your line's terms, nor a word of one's
    label ("quality" for a line about validation): a word planted in the posting, a tool, or a domain such as patients."""
    may, verbs = rewrite_terms(original, extra), _verbs(original)
    mine = {_root(p) for c in _compounds(original) for p in [c, *c.split("-")]}
    named = {_root(p) for t in may for c in _compounds(label(t)) for p in [c, *c.split("-")]}
    plain = _GENERIC_ROOTS | named

    def fine(w: str) -> bool:
        if _root(w) in mine or (_SCOPE.fullmatch(w) and _stem(w) in verbs):  # "leading" for your "Led"
            return True
        if any(w.lower().startswith(k) and not re.search(v, original, re.I) for k, v in _NEEDS.items()):
            return False
        if _root(w) in named:  # a word of a tool or term this line may name: "API" of "OpenAI API"
            return True
        found = _word_terms(w)
        return found <= may if found else _root(w) in plain
    return sorted({c.lower() for c in _compounds(text) if not fine(c) and not all(fine(p) for p in c.split("-"))})


_STRONG = re.compile(r"\b(production|real-time|scalable|distributed|end-to-end)\b", re.I)
# Words that never go on a resume: `resume_never_words` in profile.yaml, none by default. Each matches as a whole
# word with an s, ed or ing ending, so "contract" matches "contracted" but never "contractor".
_NOTHING = re.compile(r"(?!)")
NEVER = _NOTHING
_NEVER_WORDS: list[str] = []


def never_words(words) -> None:
    """Set the words that never go on a resume. A heading drops them, with any parentheses around them, and lines,
    summaries and rewrites that use one are left out."""
    global NEVER, _NEVER_WORDS
    _NEVER_WORDS = [w for w in (str(x).strip() for x in words or []) if w]
    NEVER = (re.compile(r"\b(?:" + "|".join(re.escape(w) + "(?:s|ed|ing)?" for w in _NEVER_WORDS) + r")\b", re.I)
             if _NEVER_WORDS else _NOTHING)


def never_list() -> list[str]:
    return list(_NEVER_WORDS)


def clean_heading(text: str) -> str:
    """A role, place or project heading without NEVER words: with "contract" set, "Data Engineer (Contract)" reads
    "Data Engineer"."""
    t = re.sub(r"\s*\([^()]*\)", lambda m: "" if NEVER.search(m.group(0)) else m.group(0), text or "")
    t = NEVER.sub(" ", t)
    return re.sub(r"\s{2,}", " ", t).strip(" ,|/")


def _strong(text: str) -> set[str]:
    """The words that make a line strong: its scope (Led, Architected, Owned) and its weight (production, real-time)."""
    return {_stem(m.group(0)) for m in _SCOPE.finditer(text)} | {m.group(0).lower() for m in _STRONG.finditer(text)}


def rewrite_problems(original: str, text: str, extra: set[str] | frozenset = frozenset(),
                     supporting: tuple[str, ...] = ()) -> list[str]:
    """Why a rewrite of your line for one job can't stand for it; empty means it keeps your facts. On top of
    faithful(): every word is your line's, a plain word or one of your line's terms, with no arrow or long dash,
    and no colon, semicolon or unusual character your line doesn't have."""
    source = " ".join((original, *supporting))
    why = faithful(original, {"text": text, "adds": sorted(rewrite_terms(source, extra))}, supporting)
    new = _new_words(source, text, extra)
    if new:
        why.append("it uses words your line doesn't: " + ", ".join(new))
    lost = sorted(_strong(original) - _strong(text))
    if lost:
        why.append("it drops a strong word your line has: " + ", ".join(lost))
    if REWRITE_BANNED.search(text):
        why.append("it has an arrow or a long dash")
    if NEVER.search(text):
        why.append("it says " + NEVER.search(text).group(0).lower() + ", which never goes on your resume")
    if any(text.count(c) > original.count(c) for c in ":;"):
        why.append("it adds a colon or semicolon")
    odd = sorted({c for c in text if not _PLAIN.fullmatch(c) and c not in original})  # emoji, invisible or right-to-left marks
    if odd:
        why.append("it has a character your line doesn't: " + ", ".join(f"U+{ord(c):04X}" for c in odd))
    return why


def faithful_rewrites(facts: dict, rewrites: dict[str, str] | None) -> dict[str, str]:
    """The job-specific rewrites (from rewrite.py) that keep their line's facts (rewrite_problems): nothing your
    line doesn't already say, not even a tool you listed for that role or project."""
    out = {}
    for grp in ("roles", "projects"):
        for g in facts[grp]:
            if not ok(g):
                continue
            extra = mapped_tools(facts, g["id"])
            for b in g["bullets"]:
                text = (rewrites or {}).get(b["id"])
                if ok(b) and text and text != b["text"] and not rewrite_problems(b["text"], text, extra, supporting_facts(b)):
                    out[b["id"]] = text
    return out


def usable_versions(b: dict) -> list[dict]:
    """Your approved suggested versions of bullet `b` that keep its facts."""
    return [a for a in b.get("alts") or [] if ok(a) and not faithful(b["text"], a)]


def summary_parts(s: dict) -> str:
    """A summary's own words: its text, or its opener and closer when the impact sentence is picked per job."""
    return s.get("text") or " ".join(x for x in (s.get("opener", "").replace("{title}", ""), s.get("closer", "")) if x)


def usable_impacts(facts: dict) -> list[dict]:
    """Impact sentences you approved for the summary that keep the numbers and claims of the line they come from."""
    lines = {b["id"]: b for grp in ("roles", "projects") for g in facts[grp] if ok(g) for b in g["bullets"] if ok(b)}
    return [i for i in facts.get("impacts") or [] if ok(i) and i.get("backs") in lines and not faithful(lines[i["backs"]]["text"], i)]


_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def _month(text: str, end: bool, today: date) -> tuple[int, int] | None:
    if re.search(r"present|current|now", text, re.I):
        return today.year, today.month
    m = re.search(r"(?:([A-Za-z]{3})[a-z]*\.?\s+)?((?:19|20)\d\d)", text)
    if not m:
        return None
    month = _MONTHS.get((m.group(1) or "").lower()[:3]) or (12 if end else 1)
    return int(m.group(2)), month


def _role_months(g: dict, today: date) -> set[tuple[int, int]]:
    """The months one role's dates cover, up to today."""
    parts = re.split(r"\s*[–—-]\s+|\s+[–—-]\s*", g.get("dates", ""))
    start = _month(parts[0], False, today) if parts and parts[0] else None
    end = _month(parts[-1], True, today) if len(parts) > 1 else (start[0], 12) if start else None
    months: set[tuple[int, int]] = set()
    if not start or not end:
        return months
    y, mo = start
    while (y, mo) <= end and (y, mo) <= (today.year, today.month):
        months.add((y, mo))
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return months


def experience_years(facts: dict, today: date | None = None) -> int:
    """Whole years of work your approved roles' dates cover: every month in any role counts once, so overlapping
    roles don't add up twice and gaps between roles don't count."""
    today = today or date.today()
    months: set[tuple[int, int]] = set()
    for g in facts["roles"]:
        if ok(g):
            months |= _role_months(g, today)
    return len(months) // 12


def tool_months(facts: dict, tools: set[str], today: date | None = None) -> tuple[int, list[str]]:
    """How many months your roles' dates cover in which you used any of `tools` (named in the role's approved
    lines or tools line, or added as used there; a specific tool counts for its parent, PySpark for Spark), and
    which of the tools those roles show. Projects don't count: a recruiter adds up years from your jobs."""
    today = today or date.today()
    want = set(tools) | {kid for t in tools for kid in PARENT.get(t, ())}
    months: set[tuple[int, int]] = set()
    seen: set[str] = set()
    for g in facts["roles"]:
        if not ok(g):
            continue
        have = mapped_tools(facts, g["id"]) | {t for b in g["bullets"] if ok(b) for t in terms(b["text"]) if not t.startswith("theme:")}
        hit = want & have
        if hit:
            months |= _role_months(g, today)
            seen |= {t for t in tools if t in hit or PARENT.get(t, set()) & hit}
    return len(months), sorted(seen)


TOOLS_SENTENCE = {"data-engineer": "Builds data pipelines and platforms with {}.", "ai-engineer": "Builds AI systems with {}.",
                  "data-scientist": "Builds models and data pipelines with {}.", "data-analyst": "Delivers analysis and reporting with {}."}
TOOLS_MAX = 6
# ideas and methods, not tools you build with: they stay on the skills lines
CONCEPTS = {"data_warehouse", "data_lake", "data_modeling", "relational_db", "metadata", "streaming", "etl", "statistics",
            "ab_testing", "ml", "deep_learning", "nlp", "computer_vision", "time_series", "causal_inference", "forecasting",
            "mlops", "fine_tuning", "prompt_engineering", "web_scraping", "rest_api", "ci_cd", "llm", "rag", "agents", "evals",
            "vector_db", "nosql"}
# a general name the specific one already says: Azure Data Factory, not Azure as well
PARENT = {"azure": {"azure_data_factory", "adls", "synapse", "fabric", "event_hubs", "azure_devops"}, "spark": {"pyspark"},
          "aws": {"redshift", "sagemaker", "ec2", "kinesis", "bedrock"}, "gcp": {"bigquery", "vertex_ai"}}


def _and_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def placed(facts: dict) -> set[str]:
    """Skills you added as used and tied to a role or project ("where"): ones you can point to work for."""
    return {a["term"] for a in facts.get("added_skills") or [] if ok(a) and a.get("level") == "used" and a.get("where")}


def summary_tools(facts: dict, job: dict[str, float]) -> list[str]:
    """The job's tools you added as used and tied to a role or project that none of your lines show, most wanted
    first: the summary names them, so a tool the job asks for and you have is never only a word on a skills line.
    A tool with no place stays on the skills line: a summary claim needs work behind it. Concrete tools only, and
    the specific one instead of its parent."""
    backed = evidence(facts)
    have = {t for t in placed(facts) if t not in backed and CATEGORY.get(t) and t not in CONCEPTS and job.get(t)}
    have -= {parent for parent, kids in PARENT.items() if kids & have}
    return [item_label(t) for t in sorted(have, key=lambda t: -job[t])][:TOOLS_MAX]


def tools_sentence(variant: str, tools: list[str]) -> str:
    return TOOLS_SENTENCE.get(variant, TOOLS_SENTENCE["data-engineer"]).format(_and_list(tools)) if tools else ""


def strip_tools(text: str, tools) -> str | None:
    """`text` without the mentions of `tools` (terms) a rewrite put in it, as in "with Snowflake" or "Python and AI
    coding tools", keeping any list it was in grammatical, or None when a mention can't be taken out cleanly. Used
    when the same added tool would otherwise sit in more lines of a page than ADDED_TOOL_MAX: a recruiter reads
    the same tool in every line as keyword stuffing."""
    prep = r"(?:using|with|via|through|on|in)"
    name_like = lambda s: bool(s) and (s[0].isupper() or s[0].isdigit() or any(not x.startswith("theme:") for x in terms(s.split()[0])))
    out = text
    for t in tools:
        rx = _TERM_RX.get(t)
        m = rx.search(out) if rx else None
        if not m:
            continue
        before, after = out[:m.start()], out[m.end():]
        item = re.search(r",\s+([^,;.()]+?)\s+and\s+$", before)
        if item and len(item.group(1).split()) <= 3 and name_like(item.group(1)):  # "A, B and X" reads "A and B"
            out = before[:item.start()] + " and " + item.group(1) + after
        elif re.search(r"\s+and\s+$", before):  # "A and X" reads "A"
            out = re.sub(r"\s+and\s+$", "", before) + after
        elif re.search(r",\s+$", before) and re.match(r"\s+and\s+", after):  # "A, X and B" reads "A and B"
            out = re.sub(r",\s+$", "", before) + " and " + re.sub(r"^\s+and\s+", "", after)
        elif re.search(r",\s+$", before) and after.startswith(","):  # "A, X, B" reads "A, B"
            out = re.sub(r",\s+$", "", before) + after
        elif re.search(rf"\b{prep}\s+$", before, re.I) and re.match(r",\s+|\s+and\s+", after) \
                and name_like(re.sub(r"^(?:,\s+|\s+and\s+)", "", after)):  # "with X, B and C" reads "with B and C"
            out = before + re.sub(r"^(?:,\s+|\s+and\s+)", "", after)
        elif re.search(rf",?\s+{prep}\s+$", before, re.I):  # "..., using AI coding tools." and "on AWS with Snowflake to"
            out = re.sub(rf",?\s+{prep}\s+$", "", before, flags=re.I) + after
        else:
            return None
        if rx.search(out):
            return None
    out = re.sub(r"\s+([,.;])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()
    out = re.sub(r",\s*([.;])", r"\1", out)
    return out if out != text and len(out) >= 10 else None


_CTX_LEAD = r"(?:for|across|at|within|in|from|spanning|handling|serving|covering|over)"


def trim_context(text: str, nums) -> str | None:
    """`text` without the phrases that set context with one of `nums` ("for a $10M NSF-funded alliance", "from 300+
    institutions", "handling 1M+ customer-interaction records"), or None when none comes out cleanly. Used for a
    line whose context an earlier line of the same role already states, so a role doesn't repeat it."""
    out = text
    for n in sorted(set(nums), key=lambda n: (-len(n), n)):
        rx = re.compile(rf"(?:,\s*|\s+){_CTX_LEAD}\s+(?:an?\s+|the\s+)?{re.escape(n)}(?:\s+[A-Za-z][\w'-]*){{1,3}}?"
                        rf"(?=\s*[,.;]|\s+(?:and|to|with|by|for|across|in|from|at|that|which|while)\b|$)", re.I)
        out = rx.sub("", out, count=1)
    out = re.sub(r"\s+([,.;])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()
    out = re.sub(r",\s*([.;])", r"\1", out)
    return out if out != text and len(out) >= 0.4 * len(text) else None  # a line keeps most of what it says


def context_numbers(g: dict) -> set[str]:
    """The numbers that set context across a role's lines: ones two or more of its approved lines state."""
    counts = Counter(n for b in g.get("bullets") or [] if ok(b) for n in set(_nums(b["text"])))
    return {n for n, c in counts.items() if c >= 2}


_AND_PART = re.compile(r"\s+(?:and(?!\s*/)|as well as|plus)\s+|;", re.I)
_EXAMPLES = re.compile(r"\b(?:such as|e\.g\.?|like|including|for example|for instance)\b", re.I)


def _tool_at(text: str, end: bool) -> bool:
    """Whether a tool's name (or a capitalized name) starts the text, or with `end`, ends it."""
    s = text.strip()
    if not s or (end and s.endswith(")")):
        return False
    word = (s.split()[-1] if end else s.split()[0]).strip(",.;")
    if word[:1].isupper():
        return True
    return any(not t.startswith("theme:") and t not in CONCEPTS for t in terms(word))


def tool_groups(text: str) -> list[set[str]]:
    """The tools a requirement asks for, in the groups it asks for them: "Spark, Hive or Presto and Kafka or Flink"
    is two groups, and any tool of a group shows it, while "such as Tableau, Superset and Looker" is one list.
    General ideas (ETL, streaming, data modeling) aren't tools."""
    parts: list[str] = []
    for part in _AND_PART.split(text or ""):
        if parts and _tool_at(parts[-1], True) and _tool_at(part, False) and ("," in parts[-1] or _EXAMPLES.search(parts[-1])):
            parts[-1] += " and " + part  # the "and" that ends a list of tools joins it
        else:
            parts.append(part)
    out = []
    for part in parts:
        found = {t for t in terms(part) if not t.startswith("theme:") and t not in CONCEPTS}
        if found:
            out.append(found)
    return out


def coverage_advice(report: list[dict], shows: dict, kinds: dict, page: dict[str, str], groups: dict[str, str] | None = None) -> list[dict]:
    """What to change on a tailored page, required first: required items missing or not in any line (a degree is
    covered by Education), lines that only repeat what two other lines already show or show no requirement at all,
    lines that show only preferred items while a required one is missing, then preferred items still missing.
    `page`: {line id: its text}; `groups`: {line id: its role or project}, so a role's only line is never named."""
    short = lambda t: t if len(t) <= 70 else t[:70].rsplit(" ", 1)[0] + "..."
    groups = groups or {}
    req = [r for r in report if r["kind"] == "required"]
    missing = [r for r in req if not r["by"]]
    no_line = [r for r in req if r["by"] and not {"experience", "projects", "education", "dates"} & set(r["where"])]
    partly = [r for r in req if r["by"] and r not in no_line and r.get("status") == "partial"]
    lines_of = {r["id"]: [b for b in r["by"] if b in page] for r in report}
    alone = lambda b: sum(1 for x in page if groups.get(x) == groups.get(b)) <= 1 if groups.get(b) else False
    repeats = [b for b in page if shows.get(b) and not alone(b) and all(len(lines_of.get(x, [])) >= 3 for x in shows[b])]
    idle = [b for b in page if not shows.get(b) and not alone(b)]
    pref_only = [b for b in page if shows.get(b) and all(kinds.get(x) == "preferred" for x in shows[b])]
    pref_missing = [r for r in report if r["kind"] != "required" and not r["by"]]  # the work it asks for, then preferred
    pref_missing.sort(key=lambda r: r["kind"] != "responsibility")
    gaps = missing or no_line
    want = (missing or no_line or pref_missing)[:1]
    room = f" to make room for: {short(want[0]['text'])}" if want else ""
    out = []
    if missing:
        out.append({"level": "change", "text": f"{len(missing)} required not on the page: " + "; ".join(short(r["text"]) for r in missing)
                    + ". Add a line for each (Suggest lines)."})
    if no_line:
        out.append({"level": "change", "text": "Required, but only in your skills: " + "; ".join(short(r["text"]) for r in no_line)
                    + ". Show each in a line."})
    if partly:
        out.append({"level": "change", "text": f"{len(partly)} required only partly shown: "
                    + "; ".join(f"{short(r['text'])} ({'; '.join(r.get('gaps') or [])})" for r in partly)
                    + ". If you have done the rest, show it in a line (Suggest lines); if not, it stays a gap."})
    if repeats and want:
        out.append({"level": "change",
                    "text": f"{len(repeats)} line{'s' if len(repeats) > 1 else ''} only repeat{'' if len(repeats) > 1 else 's'} what two other lines already show: "
                            + "; ".join(short(page[b]) for b in repeats) + (f". Take {'them' if len(repeats) > 1 else 'it'} off (✕){room}." if want else ".")})
    if idle and want:
        out.append({"level": "change",
                    "text": f"{len(idle)} line{'s' if len(idle) > 1 else ''} show{'' if len(idle) > 1 else 's'} none of this job's requirements: "
                            + "; ".join(short(page[b]) for b in idle) + (f". Swap for one that does{room}." if want else ".")})
    if gaps and pref_only:
        out.append({"level": "change", "text": f"{len(pref_only)} line{'s' if len(pref_only) > 1 else ''} show only preferred items while a required one is missing: "
                    + "; ".join(short(page[b]) for b in pref_only) + ". Take them off (✕) first."})
    if not gaps and pref_missing:
        out.append({"level": "note", "text": "Every required item is on the page. Still missing: "
                    + "; ".join(f"{short(r['text'])} ({r['kind']})" for r in pref_missing) + ". Suggest lines can add them."})
    if not out and report:
        out.append({"level": "ok", "text": "Every required and preferred item is on the page, and no line only repeats another."})
    return out


_YEARS_ASKED = re.compile(r"(\d+)\s*\+?\s*(?:(?:-|to|or)\s*\d+\s*)?(?:\+|or more|or above|plus)?\s*years?", re.I)


def _coverage(r: dict, picked: dict, shows: dict, summary: dict | None, section: dict[str, str], educations: list,
              skills: set[str] = frozenset(), years: int = 0, lines: set[str] | None = None, tool_years=None) -> dict:
    """One requirement on the page: the lines that show it, the sections it shows in (summary, experience,
    projects, skills, education, dates), and whether all of it shows. `section`: {line id: "experience" or
    "projects"}; `skills`: the terms on the page's skills lines; `lines`: the terms of the page's summary, lines
    and tools lines; `tool_years`: tools -> (months your roles used them, the ones they show), as tool_months.
    status is "full", "partial" (some of it shows: a tool group only on the skills lines or not on the page, or
    fewer years with its tools than it asks) or "missing", and `gaps` says what's short, in words."""
    by = [bid for bid in picked if r["id"] in shows.get(bid, [])]
    where = ["summary"] if summary and r["id"] in (summary.get("covers") or []) else []
    where += [x for x in ("experience", "projects") if any(section.get(b) == x for b in by)]
    if {t for t in terms(r["text"]) if not t.startswith("theme:")} & set(skills):  # a tool it names is on your skills lines
        where.append("skills")
        by = by or ["skills"]
    if educations and _DEGREE.search(r["text"]):
        where.append("education")
        by = by or ["education"]
    groups, gaps = tool_groups(r["text"]), []
    spot = lambda t: (_TERM_RX[t].search(r["text"]).start() if t in _TERM_RX and _TERM_RX[t].search(r["text"]) else 0)
    names = lambda ts: _and_list([label(t) for t in sorted(ts, key=spot)]).replace(" and ", " or ")
    if lines is not None:
        for grp in groups:  # each group of tools it asks for shows in a line, or only on the skills lines, or nowhere
            if not grp & lines:
                gaps.append(f"{names(grp)} {'only on your skills lines' if grp & set(skills) else 'not on the page'}")
    asked = _YEARS_ASKED.search(r["text"])
    tools = set().union(*groups) if groups else set()
    if asked and tools and tool_years:  # "3+ years with Spark": the years of the roles that used one of its tools
        need = int(asked.group(1))
        months, used = tool_years(tools)
        if months >= 12 * need:
            where.append("dates")
            by = by or ["dates"]
        elif months:
            shown = f"{months / 12:.1f}".rstrip("0").rstrip(".")
            gaps.append(f"your roles show about {shown} years with {names(set(used))}; it asks {need}+")
        else:
            gaps.append(f"no role shows {names(tools)}; it asks {need}+ years")
    elif asked and years >= int(asked.group(1)):  # "1-3 years of experience": your role dates show 4+
        where.append("dates")
        by = by or ["dates"]
    elif asked and not tools:
        gaps.append(f"your role dates show {years}+ years; it asks {asked.group(1)}+")
    status = "missing" if not by else "partial" if gaps else "full"
    return {"id": r["id"], "text": r["text"], "kind": r.get("kind", ""), "by": by, "where": where, "status": status, "gaps": gaps}


def summary_opening(facts: dict, title: str, variant: str) -> str:
    """How a summary written for a job must begin: your title, your years, and what an approved opener says they
    span ("AI Engineer with 4+ years across data analytics and engineering")."""
    scopes = {s.get("variant", ""): m.group(1).strip() for s in facts["summaries"] if ok(s)
              for m in [re.search(r"\{years\}\+ years ([^,.]+)", s.get("opener") or "")] if m}
    scope = scopes.get(variant) or next(iter(scopes.values()), "")
    return f"{title} with {experience_years(facts)}+ years {scope}".strip() if scope else ""


def summary_evidence(facts: dict) -> list[dict]:
    """What a job summary may draw on: your approved highlights and lines, each with its details."""
    out = [{"id": i["id"], "text": i["text"]} for i in usable_impacts(facts)]
    for grp in ("roles", "projects"):
        for g in facts[grp]:
            if ok(g):
                where = clean_heading(" | ".join(x for x in ((g.get("org"), g.get("title")) if grp == "roles" else (g.get("title"), g.get("subtitle"))) if x))
                out += [{"id": b["id"], "where": where, "text": " ".join((b["text"], *supporting_facts(b)))}
                        for b in g["bullets"] if ok(b) and not NEVER.search(b["text"])]
    return out


_REPO_FACTS: dict[str, tuple[float, list[str]]] = {}
_DOCS = (("README.md", "a README"), ("SECURITY.md", "a security policy"), ("RESPONSIBLE_USE.md", "a responsible-use guide"),
         ("CONTRIBUTING.md", "a contributing guide"), ("LICENSE", "an open-source license"))


def repo_facts(path: str | Path) -> list[str]:
    """Facts read from a project's own code on this computer, so a line can state them without anyone guessing: its
    automated tests, its documents and its CI workflows. Cached until the code changes."""
    root = Path(str(path)).expanduser()
    if not root.is_dir():
        return []
    skip = {".venv", "venv", "node_modules", ".git", ".next", "dist", "build", "__pycache__"}
    files = [f for f in root.rglob("*") if f.is_file() and not skip & set(f.relative_to(root).parts)]
    stamp = max((f.stat().st_mtime for f in files), default=0.0)
    if str(root) in _REPO_FACTS and _REPO_FACTS[str(root)][0] == stamp:
        return _REPO_FACTS[str(root)][1]
    tests = 0
    for f in files:
        if re.fullmatch(r"test_.*\.py|.*_test\.py", f.name):
            tests += len(re.findall(r"^\s*def test_\w+", f.read_text(errors="ignore"), re.M))
        elif re.fullmatch(r".*\.(test|spec)\.[jt]sx?", f.name):
            tests += len(re.findall(r"\b(?:it|test)\(", f.read_text(errors="ignore")))
    docs = [name for file, name in _DOCS if (root / file).exists()]
    workflows = len(list((root / ".github" / "workflows").glob("*.y*ml"))) if (root / ".github" / "workflows").is_dir() else 0
    out = ([f"Its code has {tests} automated tests."] if tests else []) + \
        ([f"It is documented with {_and_list(docs)}."] if docs else []) + \
        ([f"It runs {workflows} CI workflow{'s' if workflows > 1 else ''}."] if workflows else [])
    _REPO_FACTS[str(root)] = (stamp, out)
    return out


def place_evidence(facts: dict) -> list[dict]:
    """Each approved role and project, with its lines (and their details) and the tools confirmed there."""
    out = []
    for grp in ("roles", "projects"):
        for g in facts[grp]:
            if not ok(g):
                continue
            name = clean_heading(" | ".join(x for x in ((g.get("org"), g.get("title")) if grp == "roles" else (g.get("title"), g.get("subtitle"))) if x))
            code = repo_facts(g["repo"]) if g.get("repo") else []  # verified from the project's own code
            out.append({"id": g["id"], "name": name,
                        "lines": [{"id": b["id"], "text": " ".join((b["text"], *supporting_facts(b)))} for b in g["bullets"]
                                  if ok(b) and not NEVER.search(b["text"])] + ([{"id": f"{g['id']}_code", "text": " ".join(code)}] if code else []),
                        "tools": sorted(item_label(t) for t in mapped_tools(facts, g["id"]))})
    return out


def suggestion_check(text: str, facts: dict, place_id: str) -> dict:
    """A suggested line, against its place's facts: `adds`, what it says that those lines and tools don't (you
    confirm it by saving it; empty means it only rewords them), and `hard`, what no line may have."""
    place = next((x for x in place_evidence(facts) if x["id"] == place_id), None)
    if place is None:
        return {"adds": [], "hard": ["it isn't for one of your roles or projects"]}
    src = place["name"] + " " + " ".join(l["text"] for l in place["lines"])
    extra = mapped_tools(facts, place_id)
    adds = []
    if "[" in text or "]" in text:
        adds.append("blanks for you to fill")
    if not set(_nums(text)) <= set(_nums(src)):
        adds.append("numbers " + ", ".join(sorted(set(_nums(text)) - set(_nums(src)))))
    new = _claims(text) - _claims(src) - extra
    if new:
        adds.append(", ".join(label(t) for t in sorted(new)))
    scope = {_stem(m.group(0)) for m in _SCOPE.finditer(text)} - {_stem(m.group(0)) for m in _SCOPE.finditer(src)}
    if scope:
        adds.append("scope: " + ", ".join(sorted(scope)))
    words = [w for w in _new_words(src, text, extra) if not re.fullmatch(r"\[.*\]", w)]
    if words:
        adds.append("the words " + ", ".join(words))
    hard = []
    if NEVER.search(text):
        hard.append(f"it says {NEVER.search(text).group(0).lower()}")
    if any(x in text for x in BANNED) or REWRITE_BANNED.search(text) or re.search(r"[:;]", text):
        hard.append("it has a dash, colon or semicolon")
    if re.search(r"\b(?:I|my|me|we|our)\b", text):
        hard.append("it speaks in the first person")
    return {"adds": adds, "hard": hard}


_ASK_WORDS = {"experience", "using", "use", "ability", "able", "strong", "skill", "skills", "practical", "work", "working",
              "demonstrated", "proven", "knowledge", "familiarity", "such", "modern", "related", "plus", "including", "etc",
              "closely", "one", "more", "following", "least", "year", "years"}  # how postings ask, not what they ask for
_ALIAS = {"github": "git", "gitlab": "git", "bitbucket": "git"}


def _ask_roots(text: str) -> set[str]:
    """The words that carry what a requirement or line is about, as roots."""
    out = set()
    for w in re.findall(r"[a-z][a-z0-9+#.-]*", text.lower()):
        w = _ALIAS.get(w.strip(".-"), w.strip(".-"))
        if w and w not in _FUNCTION and w not in _ASK_WORDS:
            out.add(_root(w))
    return out


def same_result(a: str, b: str) -> bool:
    """Whether two lines of one role state the same result: mostly the same words, the same result percentage
    (72% to 92%, 86%), or the same scale number (1M+, 5,000+) with much of the same wording. A number that sets
    context in many lines ("a $10M NSF-funded alliance", "300+ institutions") doesn't make two results one."""
    A, B = _ask_roots(a), _ask_roots(b)
    words = len(A & B) / len(A | B) if A | B else 0.0
    shared = set(_nums(a)) & set(_nums(b))
    # a dollar amount is usually context (a grant, a budget), never one result
    scale = {n for n in shared if not n.startswith("$") and re.search(r"\+|[0-9][MKB]\b|\d{3,}", n)}
    return words >= 0.35 or any(n.endswith("%") for n in shared) or (bool(scale) and words >= 0.25)


def duplicate_groups(facts: dict) -> list[dict]:
    """Your approved lines that state the same result as another of the same role, grouped: each group's keeper
    has the most specifics (numbers and named tools), then the fewest words; the rest repeat it."""
    spec = lambda b: (len(_nums(b["text"])) + len({x for x in terms(b["text"]) if not x.startswith("theme:")}),
                      "_you" not in b["id"], -len(b["text"]))  # on a tie, your original line, which carries its history
    out = []
    for grp in ("roles", "projects"):
        for g in facts[grp]:
            bs = sorted((b for b in g["bullets"] if ok(b)), key=spec, reverse=True) if ok(g) else []
            keepers: list[dict] = []
            for b in bs:  # most specific first; a line repeats a keeper only when it matches that keeper directly
                twin = next((k for k in keepers if same_result(k["keep"]["text"], b["text"])), None)
                if twin:
                    twin["repeats"].append(b)
                else:
                    keepers.append({"place": g.get("org") or g.get("title", ""), "keep": b, "repeats": []})
            out += [k for k in keepers if k["repeats"]]
    return out


def rank_draft(text: str, requirement: str, page: list[str], in_experience: bool) -> dict:
    """How good a suggested line is for a requirement: the share of the requirement's key words it covers, how new
    it is next to the lines already on the page (`dup`, the closest overlap), whether it lands in experience, and a
    number. `score` weighs them; `covers` names what it covers."""
    want, have = _ask_roots(requirement), _ask_roots(text)
    covers = sorted(want & have)
    share = len(covers) / len(want) if want else 0.0
    # the skills the requirement names (Git, PyTorch) are its core: a line that names them comes first
    named = {r for t in terms(requirement) if not t.startswith("theme:") for r in _ask_roots(label(t))}
    named_share = len(named & have) / len(named) if named else 1.0
    dup = max((len(have & _ask_roots(x)) / len(have | _ask_roots(x)) for x in page if have | _ask_roots(x)), default=0.0)
    score = 0.55 * share + 0.25 * (1 - dup) + (0.15 if in_experience else 0.0) + (0.05 if _nums(text) else 0.0)
    return {"score": round(score, 3), "covers": covers, "share": round(share, 2), "named": round(named_share, 2), "dup": round(dup, 2)}


def summary_problems(text: str, facts: dict, opening: str, uses: list[str]) -> list[str]:
    """Why a summary written for a job can't stand; empty means it keeps your facts. It begins with your opening,
    and every number, before-and-after, tool, claim and word after it comes from the lines and highlights it
    draws on (tools also from any of your lines or the places you tied them to)."""
    why = []
    items = {e["id"]: f"{e.get('where', '')} {e['text']}" for e in summary_evidence(facts)}  # a line with where it was done
    src = " ".join(items[u] for u in uses if u in items)
    if not opening or not text.startswith(opening):
        why.append("it doesn't begin with your title, years and what they span")
    if not src:
        why.append("it draws on none of your lines")
    rest = text[len(opening):] if opening and text.startswith(opening) else text
    if not set(_nums(rest)) <= set(_nums(src)) | derived_numbers(src):
        why.append("it has a number the lines it draws on don't: " + ", ".join(sorted(set(_nums(rest)) - set(_nums(src)) - derived_numbers(src))))
    if re.search(r"\d+\+?\s+years?\b", rest):
        why.append("it states years a second time")
    if not set(_pairs(rest)) <= set(_pairs(src)):
        why.append("it changes a before-and-after")
    extra = placed(facts) | evidence(facts) | _item_terms([it for x in facts.get("skills") or [] if ok(x) for it in x["items"]])
    new = _claims(rest) - _claims(src) - extra
    if new:
        why.append("it names something your lines don't: " + ", ".join(label(t) for t in sorted(new)))
    words = _new_words(f"{src} {opening}", rest, extra)
    if words:
        why.append("it uses words your lines don't: " + ", ".join(words))
    scope = {_stem(m.group(0)) for m in _SCOPE.finditer(rest)} - {_stem(m.group(0)) for m in _SCOPE.finditer(src)}
    if scope:
        why.append("it claims more scope: " + ", ".join(sorted(scope)))
    # a sentence that names a company or project uses only that place's lines: a result never moves to another role
    places: dict[str, list[str]] = {}
    for e in summary_evidence(facts):
        if e.get("where"):
            places.setdefault(e["where"].split(" | ")[0].strip(), []).append(e["text"])
    for sentence in re.split(r"(?<=[.!?])\s+", rest):
        named = [n for n in places if len(n) > 2 and re.search(rf"\b{re.escape(n)}\b", sentence)]
        if not named:
            continue
        own = " ".join(t for n in named for t in places[n]) + " " + " ".join(named)
        moved = _new_words(own, sentence, extra)
        if not set(_nums(sentence)) <= set(_nums(own)) or moved:
            why.append(f"it credits {' and '.join(named)} with work from another role" + (": " + ", ".join(moved) if moved else ""))
    if NEVER.search(text):
        why.append(f"it says {NEVER.search(text).group(0).lower()}")
    if any(x in text for x in BANNED) or REWRITE_BANNED.search(text) or re.search(r"[:;]", text):
        why.append("it has a dash, colon or semicolon")
    if re.search(r"\b(?:I|my|me|we|our)\b", text):
        why.append("it speaks in the first person")
    if not 100 <= len(text) <= 560:
        why.append("its length is off")
    return why


def compose_summary(s: dict, title: str, impact: dict | None, years: int = 0, tools_line: str = "") -> str:
    if not s.get("opener"):
        return s["text"]
    opener = s["opener"].replace("{title}", title).replace("{years}", str(years))
    return " ".join(x for x in (opener, impact["text"] if impact else "", tools_line, s.get("closer", "")) if x)


def evidence(facts: dict) -> set[str]:
    """Skills your approved lines show you used: bullets, their approved versions, summaries."""
    texts = [summary_parts(s) for s in facts["summaries"] if ok(s)]
    for grp in ("roles", "projects"):
        for g in facts[grp]:
            if ok(g):
                for b in g["bullets"]:
                    if ok(b):
                        texts += [b["text"], *supporting_facts(b)] + [a["text"] for a in usable_versions(b)]
                texts += list(g.get("tools") or [])
    return {t for t in terms("\n".join(texts)) if not t.startswith("theme:")}


def _added(facts: dict, level: str) -> set[str]:
    return {a["term"] for a in facts.get("added_skills") or [] if ok(a) and a.get("level") == level}


def _kind(line: dict) -> str:
    if line.get("kind"):
        return line["kind"]
    name = line.get("label", "").lower()
    for word, kind in (("ai", "ai"), ("agent", "ai"), ("data", "data"), ("pipeline", "data"), ("cloud", "cloud"),
                       ("deploy", "cloud"), ("ml", "ml"), ("analytic", "ml"), ("program", "code"), ("language", "code")):
        if re.search(rf"\b{word}", name):
            return kind
    return "code"


def _line_for(term: str, lines: list[dict]) -> str | None:
    kind = CATEGORY.get(term, "code")
    return next((s["id"] for s in lines if _kind(s) == kind), lines[-1]["id"] if lines else None)


def allowed_skill_items(facts: dict) -> dict[str, set[str]]:
    """Every item each skills line may show: your approved items, plus skills your lines back or you added."""
    lines = [s for s in facts["skills"] if ok(s)]
    allowed = {s["id"]: set(s["items"]) for s in lines}
    for t in evidence(facts) | _added(facts, "used"):
        lid = _line_for(t, lines)
        if lid:
            allowed[lid].add(item_label(t))
    allowed["learning"] = {item_label(t) for t in _added(facts, "learning")}
    return allowed


# ---------- picking ----------

def choose_title(job_title: str, allowed: list[str], variant: str) -> str:
    for rx, title in TITLE_RULES:
        if re.search(rx, job_title or "", re.I) and title in allowed:
            return title
    fallback = VARIANT_TITLE.get(variant, "")
    return fallback if fallback in allowed else (allowed[0] if allowed else "")


def _lines(text: str, width: int = BULLET_CHARS) -> int:
    return max(1, math.ceil(len(text) / width))


def tech_items(g: dict, job: dict[str, float] | None = None, fit: bool = True, extra: set[str] | frozenset = frozenset()) -> list[str]:
    """The tools line under a role or project: the tools you listed for it, the tools you said you used there
    (`extra`, mapped_tools) that the job names, then the skills its own approved lines name (most relevant to
    the job first), so it never names anything you haven't. `fit`: only as many as fit on one line."""
    job = job or {}
    texts = [x for b in g["bullets"] if ok(b)
             for x in [b["text"], *supporting_facts(b)] + [a["text"] for a in usable_versions(b)]]
    blob = "\n".join(texts)
    found = []
    for t, rx in _TERM_RX.items():
        m = rx.search(blob)
        if not m or (t == "rest_api" and not re.search(r"\brest(ful)?\b", blob, re.I)) \
                or (t == "etl" and not re.search(r"\b(etl|elt)\b", blob, re.I)):
            continue  # "OpenAI API" isn't REST API work, and "a data pipeline" isn't a tool
        name = item_label(t)
        if name.lower() not in blob.lower() and m.group(0).lower() not in name.lower():
            name = m.group(0)  # FAISS reads as FAISS, GitHub Actions as GitHub Actions
        found.append((m.start(), t, name))
    items = list(g.get("tools") or [])
    covered = _item_terms(items)
    for t in sorted((t for t in extra if t not in covered and (job.get(t) or not fit)), key=lambda t: -job.get(t, 0)):
        items.insert(len(g.get("tools") or []), item_label(t))  # right after the tools you listed
        covered.add(t)
    for _, t, name in sorted(found, key=lambda x: (-job.get(x[1], 0), x[0])):
        if t not in covered and name.lower() not in {x.lower() for x in items}:
            items.append(name)
            covered.add(t)
    if not fit:
        return items
    out = []
    for it in items:
        if len(", ".join(out + [it])) > TECH_CHARS:
            break
        out.append(it)
    return out


def _page_height(summary, educations, skills, exp, proj) -> float:
    """Estimated points from the top margin to the last line. `exp` and `proj`: (bullets, has a tools line), with
    bullets None for an entry that isn't on the page and [] for a role shown by its heading and dates alone."""
    h = FIRST_LINE_PT + ((_lines(summary["text"], SUMMARY_CHARS) - 1) * LINE10_PT if summary else 0)
    h += SECTION_PT + (2 * len(educations) - 1) * LINE10_PT + (len(educations) - 1) * EDU_GAP_PT
    h += sum(LINE10_PT for ed in educations if ed.get("courses"))
    h += SECTION_PT + (sum(_lines(", ".join(x["items"]), SKILL_CHARS) for x in skills) - 1) * LINE10_PT
    for groups, start, gap in ((exp, SECTION_PT, ENTRY_GAP_PT), (proj, PROJECT_SECTION_PT, PROJECT_GAP_PT)):
        live = [(bs, sub) for bs, sub in groups if bs is not None]
        if not live:
            continue
        h += start
        for i, (bs, sub) in enumerate(live):
            if not bs:  # a heading alone, and its place line when it has one
                h += (LINE9_PT + gap if i else 0) + (ENTRY_HEAD_PT if sub else 0)
                continue
            h += (LINE9_PT + gap if i else 0) + (ENTRY_HEAD_PT + TOOLS_TO_BULLET_PT if sub else HEAD_TO_BULLET_PT)
            h += sum((_lines(b["text"]) - 1) * LINE9_PT for b in bs) + (len(bs) - 1) * (LINE9_PT + BULLET_GAP_PT)
    return h


def _skill_lines(facts: dict, job: dict[str, float], skip: set[str] | frozenset, keep_order: bool = False) -> list[dict]:
    """Your skills lines for this job: its skills first, plus skills it names that your lines back or you added."""
    lines = [s for s in facts["skills"] if ok(s)]
    backed, used, learning = evidence(facts), _added(facts, "used"), _added(facts, "learning")
    on_lines = _item_terms([it for s in lines for it in s["items"]])
    extra: dict[str, list[str]] = {s["id"]: [] for s in lines}
    learn = []
    for t in sorted((t for t in job if not t.startswith("theme:")), key=lambda t: -job[t]):
        if t in on_lines or item_label(t) in skip:
            continue
        if t in backed or t in used:
            lid = _line_for(t, lines)
            if lid:
                extra[lid].append(item_label(t))
        elif t in learning:
            learn.append(item_label(t))
    out = []
    weight = lambda it: sum(job[t] for t in terms(it) if t in job and not t.startswith("theme:"))  # skills, not themes
    shown = _item_terms([it for s in lines for it in s["items"]])
    for s in sorted(lines, key=lambda s: -weight(" ".join(s["items"] + extra[s["id"]]))):
        named = [it for it in s["items"] if weight(it) > 0 and it not in skip] + extra[s["id"]]
        if not keep_order:
            named.sort(key=lambda it: -weight(it))
        rest = [it for it in s["items"] if weight(it) <= 0 and it not in skip]
        # skills your lines back that belong on this row, to fill it to the end of the line
        fill = [item_label(t) for t in sorted((backed | used) - shown - set(job)) if _line_for(t, lines) == s["id"]
                and item_label(t) not in skip and t not in NO_FILL]
        items, seen = [], set()
        for it in named + rest + fill:  # one line each: whatever fits, most relevant first
            if it not in seen and len(", ".join(items + [it])) <= SKILL_CHARS:
                items.append(it)
                seen.add(it)
        if keep_order:
            mine = [it for it in s["items"] if it in items]
            items = mine + [it for it in items if it not in mine]
        if items:
            out.append({"id": s["id"], "label": s["label"], "items": items,
                        "added": [it for it in items if it in extra[s["id"]] or it in fill]})
    if learn:
        fitted = []
        for it in learn:
            if len(", ".join(fitted + [it])) <= SKILL_CHARS:
                fitted.append(it)
        out.append({"id": "learning", "label": LEARNING_LINE, "items": fitted, "added": fitted})
    return out


def _page_text(sel: dict, with_learning: bool = True) -> str:
    return "\n".join([sel["summary"]["text"] if sel["summary"] else ""] +
                     [b["text"] for g in sel["experience"] + sel["projects"] for b in g["bullets"]] +
                     [" ".join(s["items"]) for s in sel["skills"] if with_learning or s["id"] != "learning"])


def _on_page(sel: dict) -> set[str]:
    """Skill terms on the page outside the learning line."""
    found = {t for t in terms(_page_text(sel, with_learning=False)) if not t.startswith("theme:")}
    return found | _item_terms([it for s in sel["skills"] if s["id"] != "learning" for it in s["items"]])


def select(facts: dict, job_text: str, job_title: str, variant: str,
           exclude: set[str] | frozenset = frozenset(), include: set[str] | frozenset = frozenset(),
           drop: set[str] | frozenset = frozenset(), summary_id: str | None = None,
           title_skip: set[str] | frozenset = frozenset(), original: set[str] | frozenset = frozenset(),
           skill_skip: set[str] | frozenset = frozenset(), employer: str = "", budget: float = PAGE_PT,
           rewrites: dict[str, str] | None = None) -> dict:
    """Choose approved lines for one job. `exclude`: bullets you removed; `include`: bullets you
    added (kept unless they can't fit); `drop`: bullets trimmed to fit one page; `summary_id`:
    the approved summary you picked; `title_skip`: title-line skills you removed; `original`:
    bullets you want in your own wording here; `skill_skip`: skills you took off this resume;
    `employer`: the company, whose own name isn't read as a skill; `budget`: estimated points of page;
    `rewrites`: your points reworded for this job (rewrite.py), used only where they keep your facts."""
    job = terms(without_employer(f"{job_title}\n{job_text}", employer))
    variant_bullets = set((facts.get("variants") or {}).get(variant, []))
    for_job = faithful_rewrites(facts, rewrites)
    # the posting's requirements (rewrite.requirements) and which of them each of your points shows: a line counts
    # for the requirements it demonstrates, required ones most
    every = list(getattr(rewrites, "requirements", None) or [])
    logistics = [r for r in every if r.get("kind") == "logistics"]  # where and when the work is, who may do it: no line shows these
    reqs = [r for r in every if r.get("kind") != "logistics"]
    weight = {r["id"]: REQ_WEIGHT.get(r.get("kind"), 1.0) * (1 + 0.25 * (len(reqs) - 1 - i) / max(1, len(reqs) - 1))
              for i, r in enumerate(reqs)}  # a posting lists what matters most first
    shows = {k: [r for r in v if r in weight] for k, v in (getattr(rewrites, "shows", None) or {}).items()}
    kind_of = {r["id"]: r.get("kind") for r in reqs}
    summaries = [s for s in facts["summaries"] if ok(s) and not NEVER.search(summary_parts(s))]
    summary = next((s for s in summaries if s["id"] == summary_id), None) or \
        max(summaries, key=lambda s: (_score(summary_parts(s), job)[0] + (3.0 if s.get("variant", "").lower() == variant else 0)), default=None)
    variant_weight, base = 0.6, not (job_text or "").strip()
    if base and summary:  # a base version: its summary stands in for a job description,
        job = terms(f"{job_title}\n{summary_parts(summary)}")  # and the bullets you picked for the version lead
        variant_weight = 3.0
    title = choose_title(job_title, facts["titles"]["items"] if ok(facts["titles"]) else [], variant)
    impact = None
    if summary and summary.get("opener"):  # the one impact sentence that fits this job best
        impact = max(usable_impacts(facts), key=lambda i: _score(i["text"], job)[0] + (1.5 if variant in (i.get("lens") or []) else 0),
                     default=None)
        tools = [] if base else summary_tools(facts, job)
        line = tools_sentence(variant, tools)
        closer = summary.get("closer", "")
        summary = {"id": summary["id"], "variant": summary.get("variant", ""), "tools": tools, "tools_line": line,
                   "text": compose_summary(summary, title, impact, experience_years(facts), line), "impact": impact["id"] if impact else None}
        written = getattr(rewrites, "summary", None)
        if written and not base and summary_id is None:  # the summary Claude wrote for this job, if it keeps your facts
            opening = summary_opening(facts, title, variant)
            if not summary_problems(written["text"], facts, opening, written["uses"]):
                summary = {"id": "job", "variant": variant, "version": "job", "tools": [], "tools_line": "", "impact": None,
                           "uses": list(written["uses"]), "covers": [r for r in written.get("covers") or [] if r in weight],
                           "text": " ".join(x for x in (written["text"], closer) if x)}

    def version(b, job_ok=True):
        """The approved version of b that fits this job best, and a better one still waiting for your OK.
        `job_ok`: False leaves out the rewrite for this job."""
        opts = [{"version": "original", "text": b["text"], "lens": ""}]
        if job_ok and b["id"] in for_job and b["id"] not in original:  # reworded for this job, and it kept your facts
            opts.insert(0, {"version": "job", "text": for_job[b["id"]], "lens": variant})
        waiting = []
        if b["id"] not in original:
            for a in b.get("alts") or []:
                if a.get("status") == "removed" or faithful(b["text"], a):
                    continue
                (opts if ok(a) else waiting).append({"version": a["id"], "text": a["text"], "lens": a.get("lens", ""),
                                                     "adds_text": adds_text(a), "why": a.get("why", "")})
        value = lambda v: _score(v["text"], job)[0] + (LENS_BONUS if v["lens"] == variant else 0)
        opts = [v for v in opts if not NEVER.search(v["text"])]
        if not opts:
            return None, None
        best = next((v for v in opts if v["version"] == "job"), None) or max(opts, key=value)
        better = max((v for v in waiting if value(v) > value(best) + 1e-9), key=value, default=None)
        return best, better

    job_kind = {m.lower() for m in _KIND.findall(job_title or "")}
    tool_terms = lambda text: {t for t in terms(text) if not t.startswith("theme:")}
    req_tools = {r["id"]: tool_terms(r["text"]) for r in reqs}
    facts_bullet = {b["id"]: (g, b) for grp in ("roles", "projects") for g in facts[grp] if ok(g) for b in g["bullets"]}

    def make(g, b, v, better, prior, line_shows):
        """One line as a candidate for the page. `added`: the tools a rewrite for this job names that your line
        doesn't (they count toward ADDED_TOOL_MAX)."""
        s, shared = _score(v["text"], job)
        s += sum(weight[r] for r in line_shows)
        bonus = NUMBER_BONUS if not base and _nums(v["text"]) else 0.0
        added = sorted(tool_terms(v["text"]) - tool_terms(" ".join((b["text"], *supporting_facts(b))))) if v["version"] == "job" else []
        return {"id": b["id"], "text": v["text"], "version": v["version"], "lens": v["lens"],
                "score": round(s + prior + bonus, 3), "fit": round(s, 3), "matched": [label(t) for t in shared],
                "shows": list(line_shows), "added": added, "prior": prior,
                "forced": b["id"] in include,
                "waiting": {"id": better["version"], "text": better["text"], "lens": better["lens"],
                            "adds_text": better["adds_text"], "why": better["why"]} if better else None}

    def without(e, tools):
        """Line `e` without the tools its rewrite added that are already in enough lines: the rewrite with those
        mentions taken out when it still keeps your facts, else your best version of the line. It stops counting
        for a requirement that only those tools showed."""
        g, b = facts_bullet[e["id"]]
        text = strip_tools(e["text"], tools)
        if text and text != b["text"] and not rewrite_problems(b["text"], text, mapped_tools(facts, g["id"]), supporting_facts(b)):
            v = {"version": "job", "text": text, "lens": variant}
        else:
            v = version(b, job_ok=False)[0] or {"version": e["version"], "text": e["text"], "lens": e["lens"]}  # no other version may print
        left = tool_terms(v["text"])
        gone = set(tools) - left
        line_shows = [r for r in e["shows"] if not (req_tools.get(r, set()) & gone) or req_tools.get(r, set()) & left]
        new = make(g, b, v, None, e["prior"], line_shows)
        new.update(forced=e["forced"], waiting=e["waiting"])
        return new

    def candidates(group):
        out = []
        for g in facts[group]:
            if not ok(g):
                continue
            same_kind = group == "roles" and not base and bool(job_kind & {m.lower() for m in _KIND.findall(g.get("title", ""))})
            bs = []
            for i, b in enumerate(g["bullets"]):
                if not ok(b) or b["id"] in exclude:
                    continue
                v, better = version(b)
                if v is None:  # every version of this line uses a word that never goes on your resume
                    continue
                prior = (variant_weight if b["id"] in variant_bullets else 0.0) + (EXPERIENCE_PRIOR if group == "roles" else 0.0) \
                    + (SAME_KIND_PRIOR if same_kind else 0.0)
                order = max(0.0, 3.0 - 0.6 * i) if base else -0.01 * i  # a base version follows your own order
                bs.append(make(g, b, v, better, prior + order, shows.get(b["id"], [])))
            if bs:
                out.append((g, sorted(bs, key=lambda x: (not x["forced"], -x["score"]))))
        return out

    roles = candidates("roles")
    priority = {bid: i for i, bid in enumerate((facts.get("variants") or {}).get(variant, []))}
    if base:  # a base version: the version's list in your facts file is its order of importance
        def your_order(group):
            def k(b):
                ids = [x["id"] for x in group["bullets"]]
                return (not b["forced"], priority.get(b["id"], 1000 + ids.index(b["id"])))
            return k
        roles = [(g, sorted(bs, key=your_order(g))) for g, bs in roles]
    def project_rank(gb):  # best bullet, half the second, +1 when it's in this resume version by default
        bs = gb[1]
        return -(bs[0]["score"] + (0.5 * bs[1]["score"] if len(bs) > 1 else 0) + (1.0 if any(b["id"] in variant_bullets for b in bs) else 0))
    projects = sorted(candidates("projects"), key=project_rank)
    if base:  # the projects whose lines come first in the version's list
        projects = sorted(((g, sorted(bs, key=your_order(g))) for g, bs in projects),
                          key=lambda gb: min(priority.get(b["id"], 1000) for b in gb[1]))
    # a project you put a line from on the page always gets a place, ahead of the ranked ones
    projects.sort(key=lambda gb: not any(b["forced"] for b in gb[1]))
    chosen_projects = projects[:MAX_PROJECTS]
    skills = _skill_lines(facts, job, skill_skip, keep_order=base)

    overlap: dict[str, set[str]] = {}
    for grp in ("roles", "projects"):
        for g in facts[grp]:
            for b in g["bullets"]:
                for o in b.get("overlaps") or []:
                    overlap.setdefault(b["id"], set()).add(o)
                    overlap.setdefault(o, set()).add(b["id"])
    clashes = lambda bid: bool(overlap.get(bid, set()) & set(picked))
    fit_of = {g["id"]: max((b["fit"] for b in bs), default=0.0) for g, bs in roles + chosen_projects}
    closest = max(fit_of.values(), default=0.0)
    role_ids = {g["id"] for g, _ in roles}
    current = {g["id"] for g, _ in roles if re.search(r"present|current", g.get("dates", ""), re.I)}
    kind_roles = {g["id"] for g, _ in roles if job_kind & {m.lower() for m in _KIND.findall(g.get("title", ""))}}
    # mandatory: the best bullet of every role and of each chosen project. On a page tailored to a posting's
    # requirements, a past role far from the job (under a third of the closest role's fit, and not the job's kind)
    # keeps its heading and dates, so its years still count, and gets a line only when that line shows a
    # requirement no other line on the page does
    picked, header_only = {}, set()
    for g, bs in roles + chosen_projects:
        first = next((b for b in bs if b["id"] not in drop), None)
        if not first:
            continue
        if not base and reqs and g["id"] in role_ids and g["id"] not in current | kind_roles and not first["forced"] \
                and fit_of.get(g["id"], 0.0) < closest / 3:
            header_only.add(g["id"])
            continue
        picked[first["id"]] = first
    # a tool a rewrite added goes in at most ADDED_TOOL_MAX lines: lines you pinned, then the strongest, keep it
    tool_count: Counter = Counter()
    for bid in sorted(picked, key=lambda x: (not picked[x]["forced"], -picked[x]["score"])):
        over = [t for t in picked[bid]["added"] if tool_count[t] >= ADDED_TOOL_MAX]
        if over:
            picked[bid] = without(picked[bid], over)
        tool_count.update(picked[bid]["added"])
    # optional pool, best first, within per-section caps and the page budget
    role_fit = {g["id"]: max((b["fit"] for b in bs), default=0.0) for g, bs in roles}
    top_fit = max(role_fit.values(), default=0.0)

    def role_cap(gid: str) -> int:
        """How many lines a role may take on a tailored page: 3 to 5 for the closest and current roles, 2 to 3 for
        other relevant roles, and older or distant ones less (recruiters read recent, relevant work most)."""
        if base:
            return MAX_PER_ROLE
        if gid in current or role_fit[gid] >= top_fit * 2 / 3:
            return 5
        return 3 if role_fit[gid] >= top_fit / 3 else 1
    pool = []
    for g, bs in roles:
        pool += [(b, g["id"], role_cap(g["id"])) for b in bs]
    for g, bs in chosen_projects:
        pool += [(b, g["id"], MAX_PER_PROJECT) for b in bs]
    # two lines of one role that state the same result count as one (same_result); the stronger, picked first, stays
    text_of, group_of = {b["id"]: b["text"] for b, _, _ in pool}, {b["id"]: gid for b, gid, _ in pool}
    same = lambda a, b: group_of.get(a) == group_of.get(b) and a in text_of and b in text_of and same_result(text_of[a], text_of[b])
    clashes = lambda bid: bool(overlap.get(bid, set()) & set(picked)) or any(same(bid, p) for p in picked if p != bid)
    second = {bs[1]["id"] for g, bs in roles if g["id"] in current and len(bs) > 1}
    # who gets space first: current roles' second lines; then, for a tailored resume, up to four lines of the role
    # that is the job's kind (your analyst role, for an analyst job); then every other role's second line
    kind_lines = set() if base else {b["id"] for g, bs in roles if job_kind & {m.lower() for m in _KIND.findall(g.get("title", ""))}
                                     for b in bs[1:4]}
    current_second = set(second)
    others = {bs[1]["id"] for g, bs in roles if len(bs) > 1} - current_second - kind_lines
    tier = lambda bid: 0 if bid in current_second else 1 if bid in kind_lines else 2 if bid in others else 3
    second |= kind_lines | others
    pool.sort(key=lambda t: (not t[0]["forced"], tier(t[0]["id"]), -t[0]["score"] / _lines(t[0]["text"]) ** 0.5))
    per = {}
    for b in picked.values():
        owner = next(gid for (x, gid, _) in pool if x["id"] == b["id"])
        per[owner] = per.get(owner, 0) + 1

    techs = {g["id"]: tech_items(g, job, extra=mapped_tools(facts, g["id"])) for g, _ in roles + chosen_projects}
    educations = [e for e in facts["education"] if ok(e)]
    g_of = {id(bs): g for g, bs in roles + chosen_projects}

    context = {g["id"]: context_numbers(g) for g, _ in roles + chosen_projects}

    def arrange(g, bs):
        """g's lines on the page in print order, each without the context an earlier line of g already states."""
        keep = [picked[b["id"]] for b in bs if b["id"] in picked]
        keep.sort(key=lambda b: next(i for i, x in enumerate(g["bullets"]) if x["id"] == b["id"]))  # your own order
        if not base and reqs:  # led by the lines that show required qualifications, then the rest, then results
            keep.sort(key=lambda b: (-sum(1 for r in b.get("shows") or [] if kind_of.get(r) == "required"),
                                     -sum(weight[r] for r in b.get("shows") or []), not _nums(b["text"])))
        if base:
            return keep
        seen, lines = set(), []  # a role states its context once ("a $10M NSF-funded alliance"): later lines leave it out
        for b in keep:
            again = sorted(n for n in set(_nums(b["text"])) if n in seen and n in context[g["id"]])
            cut = trim_context(b["text"], again) if again and not b["forced"] else None
            seen |= set(_nums(b["text"]))
            lines.append(dict(b, text=cut, trimmed_from=b["text"], trimmed_nums=sorted(set(_nums(b["text"])) - set(_nums(cut))))
                         if cut else b)
        return lines

    def height():
        on = lambda bs: arrange(g_of[id(bs)], bs)
        return _page_height(summary, educations, skills,
                            [(on(bs) or ([] if g["id"] in header_only else None),
                              bool((techs[g["id"]] if on(bs) else []) or g.get("place"))) for g, bs in roles],
                            [(on(bs) or None, bool(techs[g["id"]])) for g, bs in chosen_projects])

    if base:  # in the version's order of importance, every role's second line first; a line that doesn't fit makes way
        pool.sort(key=lambda t: (not t[0]["forced"], t[0]["id"] not in second, priority.get(t[0]["id"], 1000), -t[0]["score"]))
    # a tailored page spends its room on what this job asks for: a line beyond a role's best must share at least one
    # of the job's skills or kinds of work, a role far from the job (under a third of the closest role's fit) keeps
    # only its best line, and a role shown by its heading alone gets a line only for a requirement no line shows yet;
    # a line you put on yourself always stays
    worth = lambda b, owner: base or b["forced"] or (b["fit"] > 0 and fit_of.get(owner, 0.0) >= closest / 3) \
        or (owner in header_only and gain(b) > 0)
    # and each next line is the one that adds the most requirements the page doesn't show yet
    section_of = lambda owner: "experience" if owner in role_ids else "projects"
    owner_of = {b["id"]: gid for (b, gid, _) in pool}
    covered = {r for bid in picked for r in picked[bid]["shows"]}
    covered_in = {"experience": set(), "projects": set()}
    for bid in picked:
        covered_in[section_of(owner_of.get(bid, ""))] |= set(picked[bid]["shows"])
    required = {r["id"] for r in reqs if r.get("kind") == "required"}
    # with a few required items each can show in both experience and projects; with many, one line each comes first
    spread = len(required) <= SPREAD_MAX
    gain = lambda b: sum(weight[r] for r in b.get("shows") or [] if r not in covered) + \
        (sum(weight[r] / 2 for r in b.get("shows") or [] if r in required and r not in covered_in[section_of(owner_of.get(b["id"], ""))])
         if spread else 0.0)
    remaining = list(pool)
    while remaining:
        if shows and not base:  # required items the page lacks first, then any other item it lacks, then lines that
            # show any of the job's requirements before lines that show none, then the usual order
            remaining.sort(key=lambda t: (not t[0]["forced"], -sum(1 for r in t[0].get("shows") or [] if r in required and r not in covered),
                                          not any(r not in covered for r in t[0].get("shows") or []), not t[0].get("shows"),
                                          tier(t[0]["id"]), -(t[0]["score"] + gain(t[0])) / _lines(t[0]["text"]) ** 0.5))
        b, owner, cap = remaining.pop(0)
        if b["id"] in picked or b["id"] in drop or \
                (not b["forced"] and (per.get(owner, 0) >= cap or clashes(b["id"]) or not worth(b, owner))):
            continue  # a line you pinned skips these: it stays
        over = [t for t in b["added"] if tool_count[t] >= ADDED_TOOL_MAX]
        if over:  # that added tool is in enough lines already: this line competes again without it
            remaining.append((without(b, over), owner, cap))
            continue
        picked[b["id"]] = b
        if height() > budget and not b["forced"]:
            del picked[b["id"]]
            continue
        per[owner] = per.get(owner, 0) + 1
        tool_count.update(b["added"])
        covered |= set(b.get("shows") or [])
        covered_in[section_of(owner)] |= set(b.get("shows") or [])

    # the tools lines: a tool you added as used in several places goes on at most ADDED_TOOL_MAX of them, the ones
    # whose lines on the page name it, else the closest one; it's always on your skills lines
    added_at = {g["id"]: mapped_tools(facts, g["id"]) - _item_terms(g.get("tools") or []) for g, _ in roles + chosen_projects}
    named_at: dict[str, set[str]] = {}
    for bid, e in picked.items():
        named_at.setdefault(owner_of.get(bid, ""), set()).update(tool_terms(e["text"]))
    shown_groups = [g["id"] for g, bs in roles + chosen_projects if any(b["id"] in picked for b in bs)]
    allow: dict[str, set[str]] = {}
    for t in set().union(*added_at.values()):
        holders = [gid for gid in shown_groups if t in added_at[gid]]
        named = [gid for gid in holders if t in named_at.get(gid, set())]
        for gid in (named or sorted(holders, key=lambda x: -fit_of.get(x, 0.0))[:1])[:ADDED_TOOL_MAX]:
            allow.setdefault(gid, set()).add(t)
    techs = {g["id"]: tech_items(g, job, extra=_item_terms(g.get("tools") or []) | allow.get(g["id"], set()))
             for g, _ in roles + chosen_projects}

    def section(groups, kind):
        out = []
        for g, bs in groups:
            keep = arrange(g, bs)
            if keep or (kind == "role" and g["id"] in header_only):
                name, role = (g["org"], g["title"]) if kind == "role" else (g["title"], g.get("subtitle", ""))
                name, role = clean_heading(name), clean_heading(role)
                out.append({"id": g["id"], "kind": kind, "name": name, "role": role, "place": clean_heading(g.get("place", "")),
                            "heading": f"{name} | {role}" if role else name, "dates": g.get("dates", ""),
                            "tech": techs[g["id"]] if keep else [], "bullets": keep})
        return out

    sel = {"title_line": "", "title": "", "summary": summary, "experience": section(roles, "role"),
           "projects": section(chosen_projects, "project"), "skills": skills,
           "logistics": [{"id": r["id"], "text": r["text"]} for r in logistics]}
    page_lines = "\n".join([(summary or {}).get("text", "")] + [b["text"] for g in sel["experience"] + sel["projects"] for b in g["bullets"]]
                           + [", ".join(g["tech"]) for g in sel["experience"] + sel["projects"]])
    shown_by = {bid: e["shows"] for bid, e in picked.items()}
    sel["requirements"] = [_coverage(r, picked, shown_by, summary, {b["id"]: "experience" if grp == roles else "projects"
                                                                    for grp in (roles, chosen_projects) for _, bs in grp for b in bs},
                                     educations, _item_terms([it for row in skills for it in row["items"]]), experience_years(facts),
                                     tool_terms(page_lines), lambda ts: tool_months(facts, ts)) for r in reqs]
    on_page = _on_page(sel)
    learning = _item_terms([it for s in skills if s["id"] == "learning" for it in s["items"]])
    skill_terms = sorted([t for t in job if not t.startswith("theme:") and t in on_page and item_label(t) not in title_skip],
                         key=lambda t: -job[t])
    title_line = title  # the layout has no title line; the title names the file
    in_bank = evidence(facts) | _added(facts, "used") | _item_terms([it for s in facts["skills"] if ok(s) for it in s["items"]])
    declined = _added(facts, "no")  # skills you said you haven't used: Tailor doesn't ask again
    job_skills = sorted((t for t in job if not t.startswith("theme:")), key=lambda t: -job[t])

    def state(t):
        return ("covered" if t in on_page else "learning" if t in learning else
                "skipped" if item_label(t) in skill_skip else "not_on_page" if t in in_bank else
                "declined" if t in declined else "missing")
    detail = [{"term": t, "label": label(t), "item": item_label(t), "state": state(t)} for t in job_skills]
    own = {b["id"]: b["text"] for grp in ("roles", "projects") for g in facts[grp] for b in g["bullets"]}
    # lines you can add back; a Claude rewrite says so (version "job") and carries your own wording
    available = [dict(b, version="job", original=own[b["id"]]) if b["version"] == "job" else b
                 for g, bs in roles + projects for b in bs if b["id"] not in picked]
    # terms set in bold inside bullets: the job's skills, or with no job, the ones your summary leads with
    emphasis = job_skills or [t for t, _ in sorted(((t, (summary or {}).get("text", "").lower().find(label(t).lower()))
                                                     for t in terms((summary or {}).get("text", "")) if not t.startswith("theme:")),
                                                    key=lambda x: x[1] if x[1] >= 0 else 999)]
    sel["advice"] = coverage_advice(sel["requirements"], shown_by, kind_of,
                                    {b["id"]: b["text"] for g in sel["experience"] + sel["projects"] for b in g["bullets"]},
                                    {b["id"]: g["id"] for g in sel["experience"] + sel["projects"] for b in g["bullets"]}) if reqs else []
    sel.update({
        "title_line": title_line, "title": title,
        "covered": [d["label"] for d in detail if d["state"] == "covered"],
        "learning": [d["label"] for d in detail if d["state"] == "learning"],
        "not_on_page": [d["label"] for d in detail if d["state"] == "not_on_page"],
        "missing": [d["label"] for d in detail if d["state"] == "missing"],
        "skills_detail": detail,
        "coverage": round(100 * sum(t in on_page for t in job_skills) / len(job_skills)) if job_skills else None,
        "available": sorted(available, key=lambda b: -b["score"])[:12],
        "picked_ids": list(picked),
        "emphasis": emphasis,
        "height": round(height(), 1),
        "rewrites": {bid: e["text"] for bid, e in picked.items() if e["version"] == "job"},  # check() verifies each
    })
    return sel


# ---------- rendering and checks ----------

# The layout: a classic one-column resume in Charter. Name centered in capitals, one contact line
# with icons, small-capital section headings over a hairline rule, "Name | Role" entry headers with
# bold dates on the right, a tools line with the place on the right, and small bullets in 9 pt.
CSS = """
@page{size:Letter;margin:24pt 27pt 22pt 27pt}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font-variant-ligatures:none;font-feature-settings:"liga" 0,"clig" 0;font-family:Charter,"Bitstream Charter","Charter BT",Georgia,serif;font-size:8.8pt;line-height:10.5pt;color:#000}
a{color:inherit;text-decoration:none}
b{font-weight:bold}
.name{text-align:center;font-size:12.5pt;line-height:12.5pt;margin:-1pt 0 0.4pt;letter-spacing:0.2pt}
.contact{text-align:center;font-size:8.2pt;line-height:11pt;white-space:nowrap;margin-bottom:-1.6pt}
.contact .loc{font-size:8.6pt}
.contact .ci{margin-left:5pt}
.contact svg{height:6.8pt;width:auto;vertical-align:-0.8pt;margin-right:2.3pt;fill:#000}
h2{font-size:9.3pt;line-height:10pt;font-weight:bold;text-transform:uppercase;letter-spacing:0.45pt;
   margin:1.8pt 0 1.6pt;padding:0 0 1.4pt;background:linear-gradient(#000,#000) left bottom/100% 0.4pt no-repeat}
.row{display:flex;justify-content:space-between;align-items:baseline}
.row>span:first-child{flex:1;min-width:0}
.row .r{white-space:nowrap;padding-left:8pt}
.d9{font-size:8.6pt;font-weight:bold}
.entry .head{font-size:9.5pt;line-height:11.3pt}
.sub{font-size:8.6pt;line-height:10.6pt}
.entry+.entry{margin-top:3.2pt}
.proj .head{font-size:8.6pt;line-height:10.6pt}
.proj+.proj{margin-top:2.6pt}
.edu+.edu{margin-top:1pt}
ul{list-style:none;margin:-1.1pt 0 0;padding:0}
h2+.entry{margin-top:0.6pt}
li{padding-left:20.5pt;font-size:8.6pt;line-height:10.3pt;margin-top:0.15pt}
li .dot{display:inline-block;width:8.2pt;margin-left:-8.2pt;font-size:10pt;line-height:0;vertical-align:-1.05pt}
.sk{padding-left:103pt;text-indent:-103pt}
.sk .gap{white-space:pre}
p{margin:0}
.nw{white-space:nowrap}
"""

# Contact icons, drawn for this layout. The GitHub mark is from GitHub's Primer Octicons
# (MIT License, Copyright (c) GitHub Inc.).
ICONS = {
    "phone": '<svg viewBox="0 0 16 16"><path d="M3.3 1.2 5.6 1c.4 0 .8.2.9.6l.9 2.6c.1.4 0 .8-.3 1.1L5.8 6.5c.8 1.6 2.1 2.9 3.7 3.7l1.2-1.3c.3-.3.7-.4 1.1-.3l2.6.9c.4.1.6.5.6.9l-.2 2.3c0 .5-.5.9-1 .9C7.3 13.6 2.4 8.7 2.4 2.2c0-.5.4-1 .9-1z"/></svg>',
    "mail": '<svg viewBox="0 0 16 12"><path d="M1.5 0h13C15.3 0 16 .7 16 1.5v.3L8 7 0 1.8v-.3C0 .7.7 0 1.5 0zM0 3.6l7.6 4.9c.2.2.6.2.8 0L16 3.6v6.9c0 .8-.7 1.5-1.5 1.5h-13C.7 12 0 11.3 0 10.5z"/></svg>',
    "linkedin": '<svg viewBox="0 0 16 16"><path d="M14.8 0H1.2C.5 0 0 .5 0 1.2v13.6c0 .7.5 1.2 1.2 1.2h13.6c.7 0 1.2-.5 1.2-1.2V1.2C16 .5 15.5 0 14.8 0zM4.7 13.6H2.4V6h2.3v7.6zM3.6 5a1.3 1.3 0 1 1 0-2.7 1.3 1.3 0 0 1 0 2.7zm10 8.6h-2.4V9.9c0-.9 0-2-1.2-2s-1.4.9-1.4 1.9v3.8H6.3V6h2.3v1c.3-.6 1.1-1.2 2.2-1.2 2.4 0 2.8 1.6 2.8 3.6v4.2z"/></svg>',
    "github": '<svg viewBox="0 0 16 16"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>',
    "web": '<svg viewBox="0 0 16 16"><path d="M8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0zm5.9 7.2h-2.4a12 12 0 0 0-1-4.6 6.3 6.3 0 0 1 3.4 4.6zM8 1.6c.7.9 1.4 2.9 1.6 5.6H6.4C6.6 4.5 7.3 2.5 8 1.6zM5.5 2.6a12 12 0 0 0-1 4.6H2.1a6.3 6.3 0 0 1 3.4-4.6zM2.1 8.8h2.4a12 12 0 0 0 1 4.6 6.3 6.3 0 0 1-3.4-4.6zM8 14.4c-.7-.9-1.4-2.9-1.6-5.6h3.2c-.2 2.7-.9 4.7-1.6 5.6zm2.5-1a12 12 0 0 0 1-4.6h2.4a6.3 6.3 0 0 1-3.4 4.6z"/></svg>',
}
CONTACT_CHARS = 134   # one contact line at 8.6 pt; links that don't fit are left off, last first
NO_BOLD = {"rest_api", "git", "ml"}  # too generic to set in bold
# Line up the skills rows' colons at 98 pt with real spaces, so each row stays one line of text for
# screening software (an empty gap reads as a second column). Without the script a row still reads well.
SKILL_ALIGN = """for(const r of document.querySelectorAll('.sk')){const l=r.querySelector('.lab'),g=r.querySelector('.gap');
const t=98*96/72,w=l.getBoundingClientRect().width;g.textContent=' ';const sw=g.getBoundingClientRect().width||3.7;
const n=Math.max(1,Math.floor((t-w)/sw));g.textContent=' '.repeat(n);const now=g.getBoundingClientRect().width;
g.style.letterSpacing=((t-w-now)/n)+'px';}"""
# with none of the job's skills in a bullet, its first named tool is set in bold instead
BOLD_ANY = [t for t in _TERM_RX if t not in NO_BOLD and CATEGORY.get(t) in ("ai", "data", "cloud", "ml")]


def emphasize(text: str, emphasis: list[str], limit: int = 2) -> list[tuple[str, bool]]:
    """Split a bullet into (text, bold) runs: up to `limit` of the emphasized skills it names, in their order of
    importance, each widened to whole words and through a parenthesis right after it, as in "AWS (S3, Glue)"."""
    spans: list[tuple[int, int]] = []
    for t in emphasis:
        if t in NO_BOLD:
            continue
        rx = _TERM_RX.get(t)
        whole = re.search(re.escape(item_label(t)), text, re.I)
        m = whole or (rx.search(text) if rx else None)
        if not m:
            continue
        a, z = m.start(), m.end()
        while a > 0 and (text[a - 1].isalnum() or text[a - 1] in "-."):
            a -= 1
        while z < len(text) and (text[z].isalnum() or text[z] in "+#-"):
            z += 1
        par = re.match(r"\s?\([^()]{1,60}\)", text[z:])
        if par:
            z += par.end()
        if any(a < y and x < z for x, y in spans):
            continue
        spans.append((a, z))
        if len(spans) >= limit:
            break
    if not spans and emphasis is not BOLD_ANY:
        return emphasize(text, BOLD_ANY, 1)
    out, i = [], 0
    for a, z in sorted(spans):
        out += [(text[i:a], False), (text[a:z], True)]
        i = z
    out.append((text[i:], False))
    return [(x, bold) for x, bold in out if x]


def _heading(title: str) -> str:
    """One run of capitals at one size. A larger first letter (small capitals) reads as "S UMMARY" to some resume
    parsers, which then miss the section."""
    return f"<h2>{html.escape(title, quote=True)}</h2>"


def _contact(c: dict) -> str:
    e = lambda x: html.escape(str(x), quote=True)
    lines = list(c.get("lines") or [])
    place = lines[0] if lines else ""
    email = next((x for x in lines if "@" in x), "")
    phone = next((x for x in lines if "@" not in x and re.search(r"\d{3}\D*\d{4}", x)), "")
    parts = [(len(place), f"<span class='loc'>{e(place)}</span>")]
    if phone:
        parts.append((len(phone) + 3, f"<span class='ci'>{ICONS['phone']}{e(phone)}</span>"))
    if email:
        parts.append((len(email) + 3, f"<span class='ci'>{ICONS['mail']}<a href='mailto:{e(email)}'>{e(email)}</a></span>"))
    for l in c.get("links") or []:
        kind = "linkedin" if "linkedin." in l["url"] else "github" if "github." in l["url"] else "web"
        piece = (len(l["text"]) + 3, f"<span class='ci'>{ICONS[kind]}<a href='{e(l['url'])}'>{e(l['text'])}</a></span>")
        if sum(n for n, _ in parts) + piece[0] <= CONTACT_CHARS:
            parts.append(piece)
    return "<div class='contact'>" + "".join(x for _, x in parts) + "</div>"


def _typeset(text: str) -> str:
    """Curly apostrophes, as typeset text has them: organization's -> organization’s."""
    return re.sub(r"(?<=\w)'(?=\w)|(?<=s)'(?=\s)", "’", text)


# ---------- print punctuation: your own periods and colons; no colon or semicolon in the summary; no double dashes ----------

def printed(text: str) -> str:
    """Text as the page prints it: your own punctuation, with spacing tidied and a 20–30 range set with a plain
    hyphen."""
    t = " ".join(str(text or "").split())
    return re.sub(r"(?<=\d)\s*[\u2013\u2012]\s*(?=\d)", "-", t)


def sentence(text: str) -> str:
    """A bullet or summary line as printed, ending with one period like the rest (a rewrite that left it off gets it)."""
    t = printed(text).rstrip()
    return t if not t or t[-1] in ".!?" else t + "."


def printed_summary(text: str) -> str:
    """The summary as printed: never a colon or semicolon in it, so a stray one reads as a comma."""
    return sentence(re.sub(r"\s*[:;]\s*", ", ", printed(text)))


def printed_dates(dates: str) -> str:
    """May 2026 – Present prints as May 2026 - Present: one plain hyphen, the range form every resume parser reads."""
    d = str(dates or "").strip()
    return re.sub(r"\s*(?:[\u2013\u2014\u2012]|-{1,2}|\bto\b)\s*", " - ", d) if re.search(r"\d", d) else d


def print_problems(page: str) -> list[str]:
    """Punctuation the finished page must never show: a colon or semicolon in the summary, or a dash between words
    anywhere (em dash, en dash, double hyphen). Empty means the page is clean."""
    plain = lambda x: html.unescape(re.sub(r"<[^>]+>", " ", x))
    text = plain(re.sub(r"<(script|style|title)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I))
    bad = []
    summary = re.search(r"<h2>Summary</h2>\s*<p>(.*?)</p>", page, flags=re.S)
    if summary and re.search(r"[:;]", plain(summary.group(1))):
        bad.append("The summary has a colon or semicolon.")
    m = re.search(r"\S*(?:--|[\u2012-\u2015])\S*", text)
    if m:
        bad.append("The page has a dash: " + m.group(0)[:40])
    never = NEVER.search(text)
    if never:
        bad.append(f'The page says "{never.group(0)}", which never goes on your resume.')
    return bad


def render(facts: dict, sel: dict) -> str:
    e = lambda x: html.escape(_typeset(str(x)), quote=True)
    c = facts["contact"]
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{e(c['name'])}</title><style>{CSS}</style></head><body>",
           f"<div class='name'>{e(c['name'].upper())}</div>", _contact(c)]
    if sel["summary"]:
        out += [_heading("Summary"), f"<p>{e(printed_summary(sel['summary']['text']))}</p>"]
    out.append(_heading("Education"))
    for ed in facts["education"]:
        if ok(ed):
            left = f"<b>{e(printed(ed['school']))}</b>" + "".join(f" | {e(printed(x))}" for x in (ed.get("college"), ed.get("place")) if x)
            out.append(f"<div class='edu'><div class='row'><span>{left}</span><b class='r'>{e(printed_dates(ed['date']))}</b></div>"
                       f"<div><b>{e(printed(ed['degree']))}</b></div>"
                       + (f"<div>Courses : {e(printed(ed['courses']))}</div>" if ed.get("courses") else "") + "</div>")
    out.append(_heading("Technical Skills"))
    out += [f"<div class='sk'><span class='lab'>{e(printed(s['label']))}</span><span class='gap'> </span>: "
            f"{e(', '.join(printed(x) for x in s['items']))}</div>" for s in sel["skills"]]
    bold = list(sel.get("emphasis") or [])
    for title, groups in (("Experience", sel["experience"]), ("Projects", sel["projects"])):
        if not groups:
            continue
        out.append(_heading(title))
        for g in groups:
            cls = "entry proj" if g.get("kind") == "project" else "entry"
            head = f"<b>{e(printed(g['name']))}</b>" + (f" | {e(printed(g['role']))}" if g.get("role") else "")
            out.append(f"<div class='{cls}'><div class='row head'><span>{head}</span><b class='r d9'>{e(printed_dates(g.get('dates', '')))}</b></div>")
            if g.get("tech") or g.get("place"):
                out.append(f"<div class='row sub'><span>{e(', '.join(printed(x) for x in g.get('tech') or []))}</span>"
                           f"<span class='r'>{e(printed(g.get('place', '')))}</span></div>")
            if g["bullets"]:  # a role far from the job shows its heading and dates alone
                out.append("<ul>")
                for b in g["bullets"]:
                    runs = "".join(f"<b>{e(x)}</b>" if is_bold else e(x) for x, is_bold in emphasize(sentence(b["text"]), bold))
                    runs = re.sub(r"\b([A-Za-z]{1,3}-\d+[A-Za-z]?)\b", r"<span class='nw'>\1</span>", runs)  # H-1B stays whole
                    out.append(f"<li><span class='dot'>•</span>{runs}</li>")
                out.append("</ul>")
            out.append("</div>")
    out.append(f"<script>{SKILL_ALIGN}</script></body></html>")
    return "\n".join(out)


def check(facts: dict, sel: dict, pages: int | None) -> list[str]:
    """Every reason this resume must not be saved; empty means it passed."""
    fail = []
    if not ok(facts["contact"]):
        fail.append("Approve your contact lines first.")
    if not any(ok(x) for x in facts["education"]):
        fail.append("Approve your education lines first.")
    if not ok(facts["titles"]) or sel["title"] not in facts["titles"]["items"]:
        fail.append("The title line needs an approved title.")
    allowed_text = {}
    for grp in ("roles", "projects"):
        for g in facts[grp]:
            if ok(g):
                for b in g["bullets"]:
                    if ok(b):
                        allowed_text[b["id"]] = {b["text"]} | {a["text"] for a in usable_versions(b)}
    for bid, text in faithful_rewrites(facts, sel.get("rewrites")).items():
        allowed_text.setdefault(bid, set()).add(text)
    for g in sel["experience"] + sel["projects"]:
        said: set[str] = set()  # the numbers this entry's earlier lines state
        for b in g["bullets"]:
            source = b.get("trimmed_from") or b["text"]
            if source not in allowed_text.get(b["id"], set()):
                fail.append(f"A bullet doesn't match an approved line exactly: {b['text'][:60]}")
            elif b.get("trimmed_from") and (trim_context(source, b.get("trimmed_nums") or []) != b["text"]
                                            or not set(b.get("trimmed_nums") or []) <= said):
                fail.append(f"A line leaves out context no earlier line of {g.get('name', g['id'])} states: {b['text'][:60]}")
            said |= set(_nums(source))
    groups = {g["id"]: g for grp in ("roles", "projects") for g in facts[grp] if ok(g)}
    for g in sel["experience"] + sel["projects"]:
        if g.get("tech") and not set(g["tech"]) <= set(tech_items(groups[g["id"]], fit=False, extra=mapped_tools(facts, g["id"]))
                                                       if g["id"] in groups else []):
            fail.append(f"The tools line for {g.get('name', g['id'])} names something its approved lines don't.")
    titles = facts["titles"]["items"] if ok(facts["titles"]) else []
    years = experience_years(facts)
    allowed_summaries = {compose_summary(s, t, i, years) for s in facts["summaries"] if ok(s)
                         for t in (titles if s.get("opener") else [""]) for i in (usable_impacts(facts) + [None] if s.get("opener") else [None])}
    if sel["summary"] and sel["summary"].get("version") == "job":
        closers = sorted({s.get("closer", "") for s in facts["summaries"] if ok(s)}, key=len, reverse=True)
        text = sel["summary"]["text"]
        body = next((text[: -len(c) - 1] for c in closers if c and text.endswith(" " + c)), text)
        if summary_problems(body, facts, summary_opening(facts, sel["title"], sel["summary"].get("variant", "")), sel["summary"].get("uses") or []):
            fail.append("The summary written for this job doesn't keep your facts.")
    elif sel["summary"]:
        tools, line = list(sel["summary"].get("tools") or []), sel["summary"].get("tools_line") or ""
        may = {item_label(t) for t in placed(facts) | evidence(facts)}  # a summary names only tools with work behind them
        if not set(tools) <= may:
            fail.append("The summary names a skill you haven't added or shown in your lines.")
        if line and line not in {tools_sentence(v, tools) for v in TOOLS_SENTENCE}:
            fail.append("The summary's tools sentence isn't one the engine writes.")
        if sel["summary"]["text"].replace(" " + line if line else "", "", 1) not in allowed_summaries:
            fail.append("The summary isn't an approved summary.")
    allowed_items = allowed_skill_items(facts)
    for s in sel["skills"]:
        if s["id"] not in allowed_items or not set(s["items"]) <= allowed_items[s["id"]]:
            fail.append(f"Skills line {s['label']} has an item that isn't approved, backed by your lines, or added by you.")
        if (s["id"] == "learning") != (s["label"] == LEARNING_LINE):
            fail.append(f"Skills line {s['label']} is labeled wrong.")
    on_page = {item_label(t) for t in _on_page(sel)}
    for item in sel["title_line"].split(" · ")[1:]:
        if item not in on_page:
            fail.append(f"Title-line skill {item} doesn't appear on the page outside the learning line.")
    everything = _page_text(sel) + sel["title_line"] + " ".join(facts["contact"]["lines"]) + " ".join(s["label"] for s in sel["skills"])
    if any(x in everything for x in BANNED):
        fail.append("An em dash or a double hyphen is on the page.")
    if not sel["experience"]:
        fail.append("No approved experience lines yet.")
    if pages is not None and pages != 1:
        fail.append(f"The PDF is {pages} pages, not one.")
    return fail


def _pages(pdf: Path) -> int:
    data = pdf.read_bytes()
    return data.count(b"/Type /Page") - data.count(b"/Type /Pages")


def _print_pdf(html_text: str, folder: Path, filename: str, chrome: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    page = folder / "page.html"
    page.write_text(html_text)
    pdf = folder / filename
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf}", page.as_uri()],
                   check=True, capture_output=True, timeout=60)
    page.unlink()
    return pdf


_FIT = {"index": 0}  # the page size that last fit: the next resume starts one step fuller, so most fit in one or two prints


def build(facts: dict, job_text: str, job_title: str, variant: str, workdir: Path, filename: str | None, chrome: str,
          exclude: set[str] | frozenset = frozenset(), include: set[str] | frozenset = frozenset(),
          summary_id: str | None = None, title_skip: set[str] | frozenset = frozenset(),
          original: set[str] | frozenset = frozenset(), skill_skip: set[str] | frozenset = frozenset(),
          employer: str = "", rewrites: dict[str, str] | None = None) -> dict:
    """Pick, print, and trim until one page. Returns the selection, the PDF and the check results.
    With no filename, the PDF is named after you and the approved title: Your-Name-Data-Engineer.pdf."""
    drop: set[str] = set()
    trimmed = []
    budgets = list(BUDGETS[max(0, _FIT["index"] - 1):])
    for _ in range(14):
        sel = select(facts, job_text, job_title, variant, exclude, include, frozenset(drop), summary_id, title_skip,
                     original, skill_skip, employer, budgets[0], rewrites)
        if filename is None and sel["title"]:
            name = "-".join(re.sub(r"[^A-Za-z0-9]+", " ", f"{facts['contact']['name']} {sel['title']}").split())
            filename = f"{name}.pdf"
        page = render(facts, sel)
        fail = check(facts, sel, None) + print_problems(page)
        if fail:
            return {"selection": sel, "pdf": None, "pages": None, "failures": fail, "trimmed": trimmed}
        pdf = _print_pdf(page, workdir, filename, chrome)
        pages = _pages(pdf)
        if pages == 1:
            _FIT["index"] = BUDGETS.index(budgets[0])
            return {"selection": sel, "pdf": str(pdf), "pages": 1, "failures": check(facts, sel, 1) + print_problems(page),
                    "trimmed": trimmed}
        if len(budgets) > 1:  # the fuller page ran over: redo it at the budget that fits
            budgets.pop(0)
            continue
        mandatory = {g["bullets"][0]["id"] for g in sel["experience"] + sel["projects"] if len(g["bullets"]) == 1}
        optional = [b for g in sel["experience"] + sel["projects"] for b in g["bullets"]
                    if b["id"] not in mandatory and len(g["bullets"]) > 1 and not b.get("forced")]
        if not optional:
            return {"selection": sel, "pdf": None, "pages": pages, "trimmed": trimmed,
                    "failures": [f"The lines you added don't all fit on one page (still {pages}). Take one off with ✕."]}
        worst = min(optional, key=lambda b: b["score"])
        drop.add(worst["id"])
        trimmed.append(worst["text"][:60])
    return {"selection": sel, "pdf": None, "pages": pages, "failures": [f"Couldn't fit one page (still {pages})."], "trimmed": trimmed}


def keep(pdf: str, folder: Path, copy_folder: Path | None = None) -> tuple[str, str]:
    """Copy a checked preview PDF to its final folder, then a phone copy: (where it's kept, why the phone copy wasn't
    made, or ""). The resume is kept once it's in `folder`. macOS asks each app before it may use iCloud Drive, and
    a phone copy it blocks never fails the save."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / Path(pdf).name
    shutil.copy2(pdf, dest)
    if copy_folder:
        try:
            copy_folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pdf, copy_folder / Path(pdf).name)
        except OSError as exc:
            app = Path(os.path.realpath(sys.executable)).name  # the name macOS lists it under, such as python3.13
            if isinstance(exc, PermissionError) and "Mobile Documents" in str(copy_folder):
                return str(dest), (f"Saved, but not the phone copy: macOS blocks {app} from iCloud Drive. To allow it, open "
                                   f"System Settings, Privacy & Security, Files and Folders, and turn on iCloud Drive under {app}.")
            return str(dest), f"Saved, but not the phone copy: {exc}"
    return str(dest), ""


def new_preview_id() -> str:
    return uuid.uuid4().hex[:12]
