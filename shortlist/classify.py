"""Reads a posting: seniority, years of experience asked for, skills, sponsorship
language, location and employment type."""

from __future__ import annotations

import re

# --- seniority -----------------------------------------------------------------

TOO_SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|head of|vp|vice president|chief|"
    r"distinguished|fellow|architect|iii|iv|v|level 3|level 4|l5|l6)\b"
)
NOT_FULL_TIME_GRAD = re.compile(r"\b(intern|internship|co-?op|apprentice|summer 20\d\d|part[- ]time)\b")
OFF_PROFILE = re.compile(
    r"\b(ios|android|mobile|embedded|firmware|fpga|asic|verilog|rtl|hardware engineer|silicon|kernel|game|graphics|"
    r"unity|unreal|mechanical|electrical|robotics hardware|sales development|account executive|recruiter|designer|copywriter|paralegal|attorney)\b"
)
ENTRY = re.compile(r"\b(new grad|graduate|entry|junior|jr\.?|associate|early career|university|i|1|level 1|level i)\b")


def seniority(title: str) -> str:
    t = title.lower()
    if NOT_FULL_TIME_GRAD.search(t):
        return "internship"
    if TOO_SENIOR.search(t):
        return "senior"
    if OFF_PROFILE.search(t):
        return "off_profile"
    if ENTRY.search(t):
        return "entry"
    return "mid"


# --- years of experience ---------------------------------------------------------

YEARS = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?years?(?:'|’)?\s*(?:of\s+)?"
    r"(?:[a-z/&,\- ]{0,40}?)experience",
    re.I,
)


def years_required(text: str) -> int | None:
    """Smallest 'N years ... experience' the posting asks for (ignoring nonsense values)."""
    found = [int(m.group(1)) for m in YEARS.finditer(text) if 0 < int(m.group(1)) <= 15]
    return min(found) if found else None


# --- skills ----------------------------------------------------------------------

SKILLS: dict[str, str] = {
    "python": r"\bpython\b", "sql": r"\bsql\b", "spark": r"\b(py)?spark\b", "airflow": r"\bairflow\b",
    "aws": r"\baws\b|amazon web services|\bs3\b|\blambda\b|\bglue\b|redshift|sagemaker",
    "gcp": r"\bgcp\b|google cloud|bigquery", "azure": r"\bazure\b", "snowflake": r"snowflake",
    "databricks": r"databricks", "dbt": r"\bdbt\b", "kafka": r"\bkafka\b", "docker": r"\bdocker\b",
    "kubernetes": r"kubernetes|\bk8s\b", "terraform": r"terraform", "tableau": r"tableau",
    "power_bi": r"power ?bi", "looker": r"looker", "excel": r"\bexcel\b", "pandas": r"\bpandas\b",
    "scikit_learn": r"scikit|sklearn", "xgboost": r"xgboost", "pytorch": r"pytorch",
    "tensorflow": r"tensorflow", "llm": r"\bllms?\b|large language model|generative ai|gen ?ai",
    "rag": r"\brag\b|retrieval[- ]augmented", "agents": r"\bagents?\b|agentic|tool calling|function calling",
    "langchain": r"langchain|langgraph|llamaindex", "mcp": r"\bmcp\b|model context protocol",
    "evals": r"\bevals?\b|evaluation framework|llm evaluation", "vector_db": r"vector (db|database|store)|faiss|pinecone|pgvector|weaviate",
    "fastapi": r"fastapi", "flask": r"\bflask\b", "django": r"django", "react": r"\breact\b",
    "nextjs": r"next\.?js", "typescript": r"typescript", "javascript": r"javascript|\bnode(\.js)?\b",
    "java": r"\bjava\b", "scala": r"\bscala\b", "go": r"\bgolang\b|\bgo\b(?= programming| language|,)",
    "cpp": r"c\+\+", "r_lang": r"\br\b(?= programming| language|,|/)", "statistics": r"statistic",
    "ab_testing": r"a/b test|experimentation", "ml": r"machine learning", "deep_learning": r"deep learning|neural net",
    "nlp": r"\bnlp\b|natural language", "etl": r"\betl\b|\belt\b|data pipeline", "data_modeling": r"data model",
    "postgres": r"postgres", "nosql": r"mongodb|dynamodb|nosql|cassandra", "git": r"\bgit\b",
    "ci_cd": r"ci/cd|continuous integration|github actions", "rest_api": r"rest(ful)? api|\bapis?\b",
    "linux": r"\blinux\b", "forecasting": r"forecast",
}
_SKILL_RX = {k: re.compile(v, re.I) for k, v in SKILLS.items()}


def skills_in(text: str) -> set[str]:
    return {k for k, rx in _SKILL_RX.items() if rx.search(text)}


# --- sponsorship -----------------------------------------------------------------
# Patterns from real 2026 postings (see README). Citizenship/clearance is checked
# first, then "no", then "yes". A sentence only counts if it's about work authorization.

CITIZEN = re.compile(
    r"\bU\.?S\.? citizens?(hip)?\b|lawful permanent resident|green card holder|\bITAR\b|\bU\.S\. persons?\b|"
    r"(security|secret|top secret|ts/sci|active|government|federal) clearance",
    re.I,
)
NO = re.compile(
    r"\b(not|unable to|cannot|can't|won't|will not|does not|doesn't|do not|don't)\b[^.]{0,40}\b(sponsor|provide (visa )?sponsorship)|"
    r"sponsorship (is )?not (available|offered|provided)|not eligible for (visa )?sponsorship|"
    r"without (the need for )?(current or future |future |now or in the future )?(visa |employer |employment )?sponsorship|"
    r"now or in the future[^.]{0,60}sponsor|independently authori[sz]ed",
    re.I,
)
YES = re.compile(
    r"\bwe (do |will |can )?(sponsor|offer (visa )?sponsorship)\b|visa sponsorship (is )?available|"
    r"supports? (work authori[sz]ation|visas?|immigration)|take over sponsorship|sponsorship (is )?available",
    re.I,
)
ABOUT_WORK = re.compile(r"visa|work|employ|h-?1b|\bopt\b|authori[sz]|immigra|citizen|sponsor|clearance", re.I)
FALSE_POSITIVE = re.compile(r"export licen[cs]e|every role|executive sponsor|sponsor bank|event sponsor|clearance sponsor|sponsor your clearance", re.I)
CLEARANCE_SPONSOR = re.compile(r"sponsor (your |a )?(security )?clearance", re.I)


def sponsorship_signal(text: str) -> str:
    """'citizen_only' | 'no' | 'yes' | 'unknown'"""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    relevant = [s for s in sentences if ABOUT_WORK.search(s) and not FALSE_POSITIVE.search(s)]
    joined = " ".join(relevant)
    if any(CITIZEN.search(s) and re.search(r"require|must|only|restricted|eligib", s, re.I) for s in relevant):
        return "citizen_only"
    if NO.search(joined):
        return "no"
    if YES.search(joined) and not CLEARANCE_SPONSOR.search(joined):
        return "yes"
    return "unknown"


# --- location & employment type --------------------------------------------------

US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC "
    "SD TN TX UT VT VA WA WV WI WY DC".split()
)
# Whole words only, so "Capacity" doesn't match APAC and "Indiana" doesn't match India.
NON_US = re.compile(
    r"\b(?:canada|toronto|vancouver|montreal|united kingdom|uk|london|ireland|dublin|germany|berlin|munich|france|paris|"
    r"netherlands|amsterdam|spain|madrid|barcelona|portugal|lisbon|poland|warsaw|india|bangalore|bengaluru|hyderabad|pune|"
    r"singapore|japan|tokyo|australia|sydney|melbourne|brazil|são paulo|sao paulo|(?<!new )mexico|israel|tel aviv|emea|apac|latam|"
    r"switzerland|zurich|sweden|stockholm|denmark|copenhagen|korea|seoul|china|hong kong|taiwan|philippines|argentina|colombia|"
    r"europe|asia|africa|middle east|dubai|uae|saudi|costa rica|guatemala|peru|chile|uruguay|vietnam|thailand|indonesia|malaysia|new zealand|nigeria|kenya|egypt|south africa|romania|ukraine|serbia|czech|hungary|greece|italy|belgium|austria|finland|norway|estonia|lithuania|latvia)\b",
    re.I,
)
US_HINT = re.compile(r"united states|\bu\.?s\.?a?\b|\bus\b|remote", re.I)
US_STATE_NAMES = re.compile(
    r"\b(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|"
    r"indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|"
    r"nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|"
    r"pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|wisconsin|wyoming)\b",
    re.I,
)
# Separators between places in one location field: "San Francisco; London, UK", "NYC | Remote", "Austin or Remote".
PLACE_SEP = re.compile(r"\s*[;|\n]\s*|\s+/\s+|\s+or\s+")


def is_us(location: str, title: str = "") -> bool:
    if not location:
        return False
    if NON_US.search(title) and not re.search(r"united states|\bus\b|\busa\b", title, re.I):
        return False
    # A posting that lists several places is open to US candidates if any of them is in the US.
    return any(_is_us_place(p) for p in PLACE_SEP.split(location) if p.strip())


def _is_us_place(location: str) -> bool:
    # "San Jose, CR" style: a trailing two-letter code that isn't a US state means another country.
    trailing = re.findall(r",\s*([A-Z]{2})\b(?!\s*,?\s*(?:United States|US|USA))", location)
    if trailing and all(t not in US_STATES and t != "US" for t in trailing) and not re.search(r"united states|\busa?\b", location, re.I):
        return False
    if NON_US.search(location) and not re.search(r"united states|\bus\b|\busa\b", location, re.I):
        return False
    tokens = re.findall(r"\b[A-Z]{2}\b", location)
    return any(t in US_STATES for t in tokens) or bool(US_HINT.search(location) or US_STATE_NAMES.search(location)) or bool(
        re.search(r"new york|san francisco|boston|seattle|austin|chicago|los angeles|denver|atlanta|washington|cambridge|"
                  r"palo alto|mountain view|menlo park|sunnyvale|san jose|oakland|brooklyn|pittsburgh|philadelphia|dallas|houston|"
                  r"miami|raleigh|salt lake|portland|san diego|nashville|minneapolis|detroit|phoenix", location, re.I)
    )


CONTRACT = re.compile(r"\b(contract|contractor|temporary|temp|1099|c2c|corp[- ]to[- ]corp|w2 contract|freelance)\b", re.I)


def is_contract(title: str, employment_type: str = "") -> bool:
    return bool(CONTRACT.search(title) or CONTRACT.search(employment_type or ""))
