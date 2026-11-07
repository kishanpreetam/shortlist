"""Reads a posting: seniority, years of experience asked for, skills, sponsorship
language, location and employment type."""

from __future__ import annotations

import math
import re
from typing import NamedTuple

# --- seniority -----------------------------------------------------------------

# "head" wherever it stands ("Head of Data", "Workforce Strategy Head"), but not Head Start, the early-education program
TOO_SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|head(?! start)|vp|vice president|chief|"
    r"distinguished|fellow|architect|iii|iv|v|level 3|level 4|l5|l6|managing partner|executive partner)\b"
)
NOT_FULL_TIME_GRAD = re.compile(r"\b(intern|internships?|co-?op|apprentice|summer 20\d\d|part[- ]time)\b")
POSTDOC = re.compile(r"\bpost[- ]?doc(toral)?\b")  # postdoctoral appointments need a finished PhD
# Hospital, university, bank and insurer boards add clinical, teaching, finance and HR titles
# that match role-family words ("Nurse Consultant", "Equity Research Associate").
OFF_PROFILE = re.compile(
    r"\b(ios|android|mobile|embedded|firmware|fpga|asic|verilog|rtl|hardware engineer|silicon|kernel|game|graphics|"
    r"unity|unreal|mechanical|electrical|robotics hardware|sales development|account executive|recruiter|designer|copywriter|paralegal|attorney|"
    r"nurse|rn|pharmacist|pharmacy technician|physician|therapist|phlebotomist|lactation|beauty|"
    r"clinical research (associate|coordinator)|faculty|lecturer|professor|"
    r"equity research|investment banking|financial planning|wealth planning|retirement consultant|risk control|wholesaler|"
    r"hr consultant|talent consultant)\b"
)
ENTRY = re.compile(r"\b(new grad|graduate|entry|junior|jr\.?|associate|early career|university|i|1|level 1|level i)\b")


def seniority(title: str) -> str:
    t = title.lower()
    if POSTDOC.search(t):
        return "postdoc"
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
# What a posting asks the candidate to already have, read the way a person reads it:
#   - only years of the candidate's own experience count, not "the last 3 years", an age, the company's age
#     or a benefit that grows with tenure ("a sabbatical after 5 years");
#   - a preferred ask is ignored, whether its own sentence says so or a "Preferred Qualifications" heading does;
#   - a range or "N+" counts at its low end, and decimals and months round up ("1.5+ years" is 2);
#   - separate asks all apply, so the largest one wins;
#   - alternatives joined by "or" need only one, so the smallest one the candidate's degree meets wins
#     ("7+ years (or 5+ with an advanced degree)" is 5 for someone with a master's).

NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15}
_NUMBER = r"(?:\d{1,2}(?:\.\d)?|\b(?:" + "|".join(NUMBER_WORDS) + r")\b)"
# "5 years", "3-5+ yrs", "Three (3) years", "1.5 years", "6 months". Not "5 years old", "20 years ago", "in a row".
YEARS_ASK = re.compile(
    rf"(?<![\d.$/])(?P<low>{_NUMBER})(?:\s*\(\d{{1,2}}\))?\s*(?:(?:-|to)\s*(?P<high>{_NUMBER})(?:\s*\(\d{{1,2}}\))?)?"
    rf"(?:\s*\+|\s+or\s+(?:more|greater)|\s+plus)?[\s-]*(?:(?P<years>years?|yrs?)|(?P<months>months?))\b"
    rf"(?!\s*(?:old|ago|of age|in business|of history|in a row|in row|running|straight|consecutive))", re.I)
YEARS_WORD = re.compile(r"year|yr|month", re.I)  # a line without one of these holds no ask
EXPERIENCE_WORD = re.compile(r"experience|experienced|background|hands[- ]on|track record|proven|professional|proficien|exposure|tenure", re.I)
# Words right after the years that start a description of the work ("5 years of", "3 years managing"),
# and words right before them that make them an ask ("at least 5 years")
WORK_AFTER = re.compile(r"\s*(?:of|in|as|with|across|working|managing|developing|building|using|designing|leading|selling|supporting|"
                        r"delivering|operating|performing|writing|doing|specifically|within|related|relevant|combined|post|full|hands)\b", re.I)
ASK_BEFORE = re.compile(r"\b(?:minimum(?: of)?|min\.?|at least|more than|over|plus)\s*$", re.I)
# Years that describe the company ("we have 12 years of experience", "founded 20 years ago"), unless an ask cue is nearer
COMPANY_BEFORE = re.compile(r"(?:\bwe(?:'ve| have| are| were| celebrate)\b|\bour\b|\bhas been\b|\bhave been\b|\bfounded\b|\bsince\b|"
                            r"\bcelebrat\w+|\bfor over\b|\bfor more than\b|\bwith over\b|\bwith more than\b|\bserving\b|\bin business\b)"
                            r"[^.;]{0,50}$", re.I)
ASK_CUE_BEFORE = re.compile(r"(?:minimum|at least|required|must|need|require|requires|looking for|candidate|you)\b[^.;]{0,50}$", re.I)
# A time window or a tenure, not a requirement: "in the last 3 years", "aged 18", "every 2 years", "after 5 years of service"
WINDOW_BEFORE = re.compile(r"\b(?:last|past|previous|prior|preceding|next|within(?: the)?|aged?|ages?|every|each|over the|after)\s+(?:the\s+)?$", re.I)
# A benefit that grows with tenure ("sabbatical for 7 years of employment", "PTO accrues over the first five years")
BENEFIT_WORD = re.compile(r"\bsabbatical|\bPTO\b|paid time off|\bvacation", re.I)
# Preferred, not required: a cue later in the same clause ("is a plus"), or one just before ("Ideally 5+ years")
PREFERRED_AFTER = re.compile(r"\b(?:preferred|a plus|is a plus|are a plus|plus\b|bonus|desired|desirable|an advantage|a bonus|"
                             r"would be (?:great|nice|a plus|an advantage|ideal)|is ideal|are ideal|not required|but not necessary)\b", re.I)
PREFERRED_BEFORE = re.compile(r"(?:\b(?:ideally|preferably)\s*|\b(?:preferred|desired|desirable|bonus(?: points)?|nice[- ]to[- ]haves?)\s*[:\-]\s*)$", re.I)
PREFERRED_START = re.compile(r"^\W*(?:preferred|desired|desirable|bonus|nice[- ]to[- ]haves?)\b", re.I)
SKILL_NOUN = re.compile(r"\b(?:experience|knowledge|skills?|familiarity|degree|certification|background|exposure)\b", re.I)
# Section headings are short lines of their own that begin with a section phrase. A preferred heading starts a
# stretch where nothing counts, and any other heading ends it.
_QUALITIES = r"(?:qualifications?|requirements?|skills?|experiences?|attributes|characteristics|knowledge|abilities|points|credentials)"
PREFERRED_HEADING = re.compile(
    rf"^(?:the )?(?:(?:an )?ideal candidate also has|what will (?:make you stand out|set you apart)|what makes you stand out|"
    rf"you might also have|even better|nice[- ]to[- ]haves?|extra credit|plus(?:es)?|preferred|desired|desirable|bonus)"
    rf"(?:\s*(?:and|&|,)?\s*{_QUALITIES})*(?:\s*(?:and|&|,)\s*\w+)?$", re.I)
OTHER_HEADING = re.compile(
    rf"^(?:the )?(?:(?:minimum|basic|required|general|key|core|other|role|job|position|technical|work|professional|relevant|education|must[- ]have)\s+){{0,2}}{_QUALITIES}"
    rf"(?:\s*(?:and|&|,)\s*(?:{_QUALITIES}|requirements?|responsibilities))*$|"
    r"^(?:minimum|basic|required|requirements?|qualifications?|must[- ]haves?)$|"
    r"^what (?:you|we)'?(?:ll| will)? (?:need|bring|have|require|'?re looking for|are looking for)(?:\s+to \w+)?$|"
    r"^(?:what we'?re looking for|who you are|about you|you have|you bring|your background|you'?ll need|"
    r"what you should have|what we want|the ideal candidate|ideal candidate)$|"
    r"^(?:the )?(?:key |core |primary |main |essential )?(?:duties(?: and| &) )?responsibilit(?:y|ies)(?:\s*(?:and|&)\s*duties)?$|"
    r"^(?:what you'?ll (?:do|be doing|achieve)|what you will (?:do|be doing)|a day in the life|your role|the role|"
    r"in this role|what will you do|what you'?ll work on|the work)$|"
    r"^(?:about (?:us|the company|the team|the role|[\w.!&' -]{1,30})|who we are|why (?:join|you)[\w ]*|benefits?(?: & perks| and perks)?|"
    r"perks|compensation|salary|pay range|what we offer|our (?:mission|values|culture)|equal opportunity|life at [\w ]+|"
    r"why you'?ll love [\w ]+)$", re.I)
BRACKETED = re.compile(r"\s*\(([^)]*)\)")  # "(Preferred)" after a heading, "(Terraform a plus)" after a skill
PREFERRED_BRACKET = re.compile(r"preferred|nice|bonus|desired|plus", re.I)
REQUIRED_BRACKET = re.compile(r"minimum|required|basic|requirements", re.I)
DIGIT_OR_DOLLAR = re.compile(r"[\d$]")
CLAUSE_BREAK = re.compile(r"[.;]")
BULLET = re.compile(r"^\s*(?:[\N{BULLET}\N{TRIANGULAR BULLET}\N{BULLET OPERATOR}\N{MIDDLE DOT}*\-]|\d+[.)])\s+")
# Sentences end at ". ", "! ", "? " and "; ", but not after "e.g." or "i.e." and not before "or" or "and"
SENTENCE_BREAK = re.compile(r"(?<=[.!?])(?<![eE]\.[gG]\.)(?<![iI]\.[eE]\.)\s+|(?<=;)\s+(?!or\b|and\b)")
# Degrees an alternative can name: "7+ years (or 5+ with an advanced degree)", "BS and 5 years or MS and 3 years"
MASTERS = re.compile(r"\b(?:master'?s?|m\.?s\.?c?\.?|ms(?:e|ee|me|cs|ce|ie)|graduate degree|advanced degree|m\.?eng)\b", re.I)
BACHELORS = re.compile(r"\b(?:bachelor'?s?|b\.?s\.?c?\.?|bs(?:e|ee|me|cs|ce|ie)|undergraduate|b\.?a\.?|b\.?tech)\b", re.I)
DOCTORATE = re.compile(r"\b(?:ph\.?d\.?|doctorate|doctoral)\b", re.I)
MBA = re.compile(r"\bmba\b", re.I)
# "(or 5+ with an advanced degree)": the unit is left out after "or", so it is put back before reading
BARE_ALTERNATIVE = re.compile(r"(\bor\s+\d{1,2}(?:\.\d)?\+?)(?=\s+with\s+(?:an?\s+|the\s+)?"
                              r"(?:advanced|graduate|master|ph\.?d|doctorate|bachelor|[mb]\.?s\b))", re.I)
OR_WORD = re.compile(r"\bor\b", re.I)
OR_OPTION = re.compile(r"\bor\b|\band/or\b", re.I)
# What may sit between "or" and the next years: a degree ("or a Master's degree and 3 years"), "or equivalent ..."
OR_THEN_YEARS = re.compile(
    r"^\s*(?:(?:a|an|the)\s+)?(?:(?:(?:bachelor|master|ph\.?d|b\.?[as]|m\.?s|doctorate|advanced|graduate)[^,;]{0,40}?\s"
    r"(?:and|with|plus|\+)\s*)|(?:equivalent|comparable)[^,;]{0,40}?\s)?(?:at least\s+|a minimum of\s+|minimum\s+|over\s+)?$", re.I)
# Odd characters a posting may use, and what they stand for
_PLAIN = {"\N{NO-BREAK SPACE}": " ", "\N{RIGHT SINGLE QUOTATION MARK}": "'", "\N{LEFT SINGLE QUOTATION MARK}": "'",
          "\N{EN DASH}": "-", "\N{EM DASH}": "-", "**": "", "__": ""}


REACH = 200  # characters on each side of a mention that the checks read, so a very long sentence costs no more per mention


class _Ask(NamedTuple):
    """One "N years" mention: the years, and where it begins and ends in its sentence."""
    years: float
    start: int
    end: int


def _plain(text: str) -> str:
    """The text with no-break spaces, curly quotes, dashes and markdown bold turned plain."""
    for odd, plain in _PLAIN.items():
        text = text.replace(odd, plain)
    return text


def _number(token: str) -> float:
    try:
        return float(token)
    except ValueError:
        return NUMBER_WORDS[token.lower()]


def _heading(line: str) -> str | None:
    """'preferred' for a preferred-qualifications heading, 'other' for any other section heading, else None."""
    s = BULLET.sub("", line).strip().strip("#*_ ").rstrip(": ").strip()
    if not s or len(s) > 90 or len(s.split()) > 11 or ":" in s or DIGIT_OR_DOLLAR.search(s) or s.endswith("."):
        return None
    brackets = " ".join(BRACKETED.findall(s))
    core = BRACKETED.sub("", s).strip()
    if PREFERRED_BRACKET.search(brackets):
        return "preferred"
    if REQUIRED_BRACKET.search(brackets):
        return "other"
    if PREFERRED_HEADING.match(core):
        return "preferred"
    return "other" if OTHER_HEADING.match(core) else None


def _about_candidate(sent: str, m: re.Match, before: str, experience_named: bool) -> bool:
    """Whether the years describe the candidate's experience, not a contract length or the company's age."""
    if m.group("months"):  # "3 months" is an ask only when experience words follow it
        return bool(WORK_AFTER.match(sent, m.end()) or ASK_BEFORE.search(before)) and bool(
            EXPERIENCE_WORD.search(sent[m.end():m.end() + 60]))
    return experience_named or bool(WORK_AFTER.match(sent, m.end()) or ASK_BEFORE.search(before))


def _preferred(sent: str, m: re.Match, before: str, opening: re.Match | None) -> bool:
    """Whether the sentence marks these years as preferred rather than required (`opening`: it starts "Preferred...")."""
    if PREFERRED_BEFORE.search(before) or (opening and opening.end() <= m.start()):
        return True
    right = sent[m.end():m.end() + REACH]
    cue = PREFERRED_AFTER.search(right)
    if not cue or "(" in right[:cue.start()] or CLAUSE_BREAK.search(right[:cue.start()]) or cue.start() > 90:
        return False
    nouns = list(SKILL_NOUN.finditer(right))
    return not (len(nouns) >= 2 and nouns[1].start() < cue.start())  # "X experience, Y experience is a plus": the cue is Y's


def _asks_in(sent: str) -> list[_Ask]:
    """The required-experience asks in one sentence."""
    asks = []
    experience_named, opening = bool(EXPERIENCE_WORD.search(sent)), PREFERRED_START.match(sent)
    for m in YEARS_ASK.finditer(sent):
        years = _number(m.group("low")) / (12 if m.group("months") else 1)
        if not 0 <= years <= 15 or (m.group("high") and _number(m.group("high")) < _number(m.group("low"))):
            continue
        before = sent[max(0, m.start() - REACH):m.start()]
        if not _about_candidate(sent, m, before, experience_named) or WINDOW_BEFORE.search(before):
            continue
        if COMPANY_BEFORE.search(before) and not ASK_CUE_BEFORE.search(before):
            continue
        if not _preferred(sent, m, before, opening):
            asks.append(_Ask(years, m.start(), m.end()))
    return asks


def _asks(text: str):
    """Yield (sentence, asks) for each sentence that asks the candidate for experience, leaving out
    everything under a preferred heading and sentences about benefits."""
    in_preferred, since_heading = False, 0
    for raw in _plain(text).split("\n"):
        line = raw.strip()
        if not line:
            continue
        heading = _heading(line)
        if heading:
            in_preferred, since_heading = heading == "preferred", 0
            continue
        since_heading += 1
        if in_preferred and since_heading > 12:  # a long preferred list often has no heading after it
            in_preferred = False
        if in_preferred or not YEARS_WORD.search(line):
            continue
        for sent in SENTENCE_BREAK.split(BARE_ALTERNATIVE.sub(r"\1 years", line)):
            asks = [] if BENEFIT_WORD.search(sent) else _asks_in(sent)
            if asks:
                yield sent, asks


def _is_alternative(sent: str, first: _Ask, second: _Ask) -> bool:
    """Whether the second ask is an alternative to the first ("... or 7 years", "... or a Master's degree and 3 years")."""
    between = sent[first.end:second.start]
    ors = list(OR_OPTION.finditer(between))
    return bool(ors) and bool(OR_THEN_YEARS.match(between[ors[-1].end():]))


def _degree_level(span: str) -> int | None:
    """1 for a bachelor's, 2 for a master's or other advanced degree, 3 for a doctorate, None when no degree is named."""
    return 3 if DOCTORATE.search(span) else 2 if MASTERS.search(span) else 1 if BACHELORS.search(span) else None


def _easiest(sent: str, options: list[_Ask], have: int) -> float:
    """Of alternatives joined by "or", the smallest ask whose degree the candidate has (`have`: 1 bachelor's, 2 master's)."""
    if len(options) == 1:
        return options[0].years
    starts = [0]  # each option runs from its own "or" to the next option's "or"
    for before, after in zip(options, options[1:]):
        ors = [m.start() for m in OR_WORD.finditer(sent[before.end:after.start])]
        starts.append(before.end + ors[-1] if ors else after.start)
    starts.append(len(sent))
    met = [o.years for k, o in enumerate(options)
           if (_degree_level(sent[max(starts[k], o.start - REACH):min(starts[k + 1], o.end + REACH)]) or 0) <= have]
    return min(met) if met else min(o.years for o in options)


def years_required(text: str, advanced_degree: bool = False) -> int | None:
    """Years of experience the posting asks for, or None when it names none (see the rules above).
    `advanced_degree`: the candidate has a master's degree or more, which meets "or 5+ years with an advanced degree".
    A PhD-only alternative ("or a PhD with 1 year") is never counted as met."""
    have = 2 if advanced_degree else 1
    asked: list[float] = []
    for sent, asks in _asks(text or ""):
        options = [asks[0]]
        for earlier, later in zip(asks, asks[1:]):
            if _is_alternative(sent, earlier, later):
                options.append(later)
            else:
                asked.append(_easiest(sent, options, have))
                options = [later]
        asked.append(_easiest(sent, options, have))
    return math.ceil(max(asked)) if asked else None


def has_advanced_degree(degree) -> bool:
    """Whether a profile's `degree` ("masters", "MS in Analytics", "PhD", "MBA") is a master's degree or higher."""
    return isinstance(degree, str) and bool(MASTERS.search(degree) or DOCTORATE.search(degree) or MBA.search(degree))


# --- skills ----------------------------------------------------------------------

SKILLS: dict[str, str] = {
    "python": r"\bpython\b", "sql": r"\bsql\b", "spark": r"\b(py)?spark\b", "airflow": r"\bairflow\b",
    "aws": r"\baws\b|amazon web services|\bs3\b|\blambda\b|\bglue\b|redshift|sagemaker",
    "gcp": r"\bgcp\b|google cloud|bigquery", "azure": r"\bazure\b", "snowflake": r"snowflake(?![\s-]+schemas?)",
    "databricks": r"databricks", "dbt": r"\bdbt\b", "kafka": r"\bkafka\b", "docker": r"\bdocker\b",
    "kubernetes": r"kubernetes|\bk8s\b", "terraform": r"terraform", "tableau": r"tableau",
    "power_bi": r"power ?bi", "looker": r"looker(?! studio)", "excel": r"\bexcel\b", "pandas": r"\bpandas\b",
    "scikit_learn": r"scikit|sklearn", "xgboost": r"xgboost", "pytorch": r"pytorch",
    "tensorflow": r"tensorflow", "llm": r"\bllms?\b|large language model|generative ai|gen ?ai",
    "rag": r"\brag\b|retrieval[- ]augmented", "agents": r"\bagents?\b|agentic|tool calling|function calling",
    "langchain": r"langchain|langgraph|llamaindex", "mcp": r"\bmcp\b|model context protocol",
    "evals": r"\bevals?\b|evaluation framework|llm evaluation", "vector_db": r"vector (db|database|store)|faiss|pinecone|pgvector|weaviate",
    "fastapi": r"fastapi", "flask": r"\bflask\b", "django": r"django", "react": r"\breact\b",
    "nextjs": r"next\.?js", "typescript": r"typescript", "javascript": r"javascript|\bnode(\.js)?\b",
    "java": r"\bjava\b", "scala": r"\bscala\b", "go": r"\bgolang\b|\bgo\b(?= programming| language|,)",
    "cpp": r"c\+\+", "r_lang": r"\br\b(?= programming| language|,|/)", "statistics": r"\bstatistic",
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
# "may not be able to employ candidates who ... or support future F-1 or H-1B sponsorship" (Roblox and others)
NO_VISA_SUPPORT = re.compile(r"\b(may not|not be able to|unable to|cannot)\b[^\n]{0,220}?\b(F-1|H-1B|visa)\b[^\n]{0,40}?\bsponsorship\b", re.I)
# Green-card (PERM) postings list one person's exact background, in the Labor Department's words: "Position also
# requires the following: 2 years ...", "3 years of experience in the job offered or a related occupation", "In the
# alternative, the employer will accept ...", "Any suitable combination of education, training, or experience is
# acceptable", "Must also have 3 years of experience in each of the following:". Postings put no-break spaces and
# line breaks between these words, so the gaps are \s.
PERM_STYLE = re.compile(
    r"position\s+(?:also\s+)?requires\s+(?:the\s+following|\d+\s+(?:years|months))|"
    r"requires\s+\d+\s+(?:years|months)\s+of\s+experience\s+in\s+the\s+following|"
    r"\bin\s+(?:the\s+)?job\s+offered\b|"
    r"\bemployer\s+will\s+accept\b[^.]{0,100}?\b(?:degree|experience|education|training|years?)\b|"
    r"\bany\s+suitable\s+combination\s+of\s+education,\s+training\b|"
    r"\bmust\b[^.]{0,120}?\b(?:years?|months?|experience|training|coursework)\s+(?:(?:with/)?in|with)\s+each\s+of\s+the\s+following\b",
    re.I)


def is_perm_style(text: str) -> bool:
    return bool(PERM_STYLE.search(text or ""))


CITIZEN_WITH_CLEARANCE = re.compile(r"\bU\.?\s?S\.?\s+citizen(ship)?\b.{0,160}?\bclearance\b", re.I | re.S)
CLEARANCE_SPONSOR = re.compile(r"sponsor (your |a )?(security )?clearance", re.I)


def sponsorship_signal(text: str) -> str:
    """'citizen_only' | 'no' | 'yes' | 'unknown'"""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    relevant = [s for s in sentences if ABOUT_WORK.search(s) and not FALSE_POSITIVE.search(s)]
    joined = " ".join(relevant)
    if any(CITIZEN.search(s) and re.search(r"require|must|only|restricted|eligib", s, re.I) for s in relevant):
        return "citizen_only"
    # A requirements list often says it with no "must": "U.S. Citizenship with the ability to obtain ... a clearance."
    # Checked on the whole text, since sentence splitting cuts "U.S." apart.
    if CITIZEN_WITH_CLEARANCE.search(text):
        return "citizen_only"
    if NO.search(joined) or NO_VISA_SUPPORT.search(text):
        return "no"
    if YES.search(joined) and not CLEARANCE_SPONSOR.search(joined):
        return "yes"
    return "unknown"


# Clinical roles that need a license the profile can't have ("PA or RN license", "direct patient care").
CLINICAL_LICENSE = re.compile(
    r"\b(RN|NP|PA|LPN|MD)\b[^.]{0,40}\b(licen[sc]e|degree)\b|registered nurse|nursing licen[sc]e|direct (care of )?patient care|"
    r"direct care of patients|board[- ]certified", re.I)


def needs_clinical_license(text: str) -> bool:
    return bool(CLINICAL_LICENSE.search(text or ""))


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
