"""The algorithm: a transparent estimate of this candidate's chance at each posting.

    score = fit x experience x sponsor x (0.5 + 0.5 x lottery) x freshness x access x leverage

- fit         skills the posting mentions that the candidate has (60%), blended
              with how well the candidate fits the role family overall (40%).
- experience  1.0 when the years asked for are within reach, falling off fast after.
- sponsor     how likely this employer is to sponsor: the posting's own wording
              first, then cap-exempt status, then its H-1B filing history.
- lottery     chance of an H-1B selection within the candidate's remaining
              lottery seasons, from the salary's wage level. Cap-exempt is 1.0.
              It counts half, because the offer itself doesn't depend on it.
- freshness   older postings are often filled or stale.
- access      how open the role family is to 0-2 years of experience.
- leverage    warm contacts, alumni, and proof-of-work projects that match the employer
              (configured in profile.yaml under `proof`).

Hard exclusions: senior titles, internships, non-US, contract roles (STEM OPT
needs W-2 employment), citizenship or clearance requirements, explicit "no
sponsorship", and asks of 3+ more years than the candidate has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import classify, wages
from .boards import Posting
from .roles import ROLE_BY_KEY, Role, role_for_title


@dataclass
class Company:
    name: str
    boards: list[list[str]]
    lca_2026: int | None = None
    cap_exempt: bool = False
    tags: list[str] = field(default_factory=list)
    alumni: int = 0
    warm: bool = False
    h1b: str | None = None  # legal-name stem for H-1B lookups
    portal: str | None = None  # careers page for employers without a readable board
    discovered: dict | None = None  # filing counts when the employer came from `discover`


@dataclass
class Scored:
    posting: Posting
    company: Company
    role: Role
    excluded: str | None = None
    fit: float = 0.0
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    years_required: int | None = None
    experience: float = 0.0
    sponsor: float = 0.0
    sponsor_reason: str = ""
    salary: float | None = None
    salary_source: str = ""
    wage_level: int | None = None
    lottery: float = 0.0
    freshness: float = 0.0
    age_days: int | None = None
    access: float = 0.0
    leverage: float = 1.0
    leverage_reason: str = ""
    score: float = 0.0


def _experience(required: int | None, have: float) -> float:
    if required is None:
        return 0.9
    gap = required - have
    if gap <= 0:
        return 1.0
    if gap <= 1:
        return 0.75
    if gap <= 2:
        return 0.45
    return 0.2


def _freshness(age: int | None) -> float:
    if age is None:
        return 0.7
    for limit, value in ((14, 1.0), (30, 0.85), (60, 0.65), (120, 0.45)):
        if age <= limit:
            return value
    return 0.3


def _sponsor(company: Company, signal: str, role: Role, record: dict | None) -> tuple[float, str]:
    if signal == "yes":
        return 0.95, "posting says it sponsors visas"
    if company.cap_exempt:
        return 0.9, "cap-exempt employer: no lottery (confirm with them)"
    if record:
        n_role, n_all = record["by_role"].get(role.key, 0), record["filings"]
        if n_role >= 25:
            return 0.85, f"{n_role} H-1B filings for this role family"
        if n_role >= 5:
            return 0.7, f"{n_role} H-1B filings for this role family"
        if n_role >= 1:
            return 0.55, f"{n_role} H-1B filing(s) for this role family, {n_all} in tech overall"
        if n_all >= 10:
            return 0.45, f"{n_all} H-1B tech filings, none for this role family"
        if n_all >= 1:
            return 0.25, f"only {n_all} H-1B tech filing(s)"
        return 0.1, "no H-1B tech filings found in 2025-26"
    if company.lca_2026:
        n = company.lca_2026
        value = 0.85 if n >= 200 else 0.75 if n >= 50 else 0.6 if n >= 10 else 0.45
        return value, f"{n} H-1B filings Jan-Jun 2026"
    return 0.3, "no filing data loaded yet; check before applying"


def score_posting(p: Posting, company: Company, profile: dict, sponsor_record: dict | None, now: datetime) -> Scored | None:
    role = role_for_title(p.title)
    if role is None or role.key not in profile["roles"]:
        return None
    overrides = profile.get("role_overrides", {}).get(role.key, {})
    s = Scored(posting=p, company=company, role=role)
    text = f"{p.title}\n{p.text}"

    seniority = classify.seniority(p.title)
    signal = classify.sponsorship_signal(p.text)
    s.years_required = classify.years_required(p.text)
    have = float(profile["years_experience"])
    if seniority == "senior":
        s.excluded = "senior title"
    elif seniority == "internship":
        s.excluded = "internship or part-time"
    elif seniority == "off_profile":
        s.excluded = "off-profile specialty (mobile, hardware, games...)"
    elif not (classify.is_us(p.location, p.title) or p.remote and not classify.NON_US.search(f"{p.location} {p.title}")):
        s.excluded = f"outside the US ({p.location})"
    elif classify.is_contract(p.title, p.employment_type):
        s.excluded = "contract role (STEM OPT needs W-2 employment)"
    elif signal == "citizen_only":
        s.excluded = "US citizens / clearance only"
    elif signal == "no":
        s.excluded = "posting says no visa sponsorship"
    elif s.years_required is not None and s.years_required >= have + 3:
        s.excluded = f"asks for {s.years_required}+ years"

    # fit
    levels = profile["skills"]
    asked = classify.skills_in(text)
    s.matched = sorted((k for k in asked if levels.get(k, 0) >= 0.5), key=lambda k: -levels[k])
    s.missing = sorted(k for k in asked if levels.get(k, 0) < 0.5)
    role_fit = overrides.get("fit", role.fit)
    s.fit = 0.6 * (sum(levels.get(k, 0) for k in asked) / len(asked)) + 0.4 * role_fit if len(asked) >= 3 else role_fit

    s.experience = _experience(s.years_required, have)
    s.sponsor, s.sponsor_reason = _sponsor(company, signal, role, sponsor_record)

    # salary -> wage level -> lottery odds
    if p.salary_min or p.salary_max:
        lo, hi = p.salary_min or p.salary_max, p.salary_max or p.salary_min
        s.salary, s.salary_source = (lo + hi) / 2, "posted"
    else:
        s.salary, s.salary_source = float(overrides.get("typical_wage", role.typical_wage)), "typical for role"
    if company.cap_exempt:
        s.lottery = 1.0
    else:
        area = wages.area_for(p.location, p.remote, profile.get("home_area", wages.BOSTON_AREA))
        primary = wages.wage_level(s.salary, role.socs[0], area)
        best = max((lv for soc in role.socs if (lv := wages.wage_level(s.salary, soc, area)) is not None), default=None)
        if s.salary_source == "posted" and best == 0 and not s.excluded:
            s.excluded = "salary below the H-1B minimum wage for every fitting occupation"
        # The employer picks the occupation code; if the main one is out of reach but another
        # fitting code works, assume level I. Unknown salaries are treated as level I, too.
        s.wage_level = primary if primary else (1 if best or s.salary_source != "posted" else 0)
        seasons = int(profile.get("lottery_seasons", 2))
        s.lottery = wages.multi_season_odds(s.wage_level, seasons)

    s.age_days = (now - p.posted_at).days if p.posted_at else None
    s.freshness = _freshness(s.age_days)
    s.access = max(overrides.get("access", role.access), 0.8) if seniority == "entry" else overrides.get("access", role.access)

    reasons, boost = [], 0.0
    if company.warm:
        boost += 0.4
        reasons.append("warm contact")
    if company.alumni:
        boost += 0.2
        reasons.append(f"{company.alumni} {profile.get('school', 'school')} alumni")
    for tag, proof in (profile.get("proof") or {}).items():
        if tag in company.tags and role.key in proof.get("roles", []):
            boost += float(proof.get("boost", 0.15))
            reasons.append(f"{proof['project']} is direct proof")
    s.leverage, s.leverage_reason = min(1 + boost, 1.6), ", ".join(reasons)

    if not s.excluded:
        s.score = 100 * s.fit * s.experience * s.sponsor * (0.5 + 0.5 * s.lottery) * s.freshness * s.access * s.leverage
    return s


def summarize_roles(scored: list[Scored], profile: dict) -> list[dict]:
    """Rank role families by how much real opportunity this scan found for them."""
    import math
    rows = []
    for key in profile["roles"]:
        role = ROLE_BY_KEY[key]
        mine = [s for s in scored if s.role.key == key]
        ok = sorted((s for s in mine if not s.excluded), key=lambda s: -s.score)
        top = ok[:10]
        mean_top = sum(s.score for s in top) / len(top) if top else 0.0
        reasons: dict[str, int] = {}
        for s in mine:
            if s.excluded:
                bucket = s.excluded.split(" (")[0]
                reasons[bucket] = reasons.get(bucket, 0) + 1
        rows.append({
            "role": role.label, "key": key, "eligible": len(ok), "seen": len(mine),
            "top10_mean": round(mean_top, 1),
            "opportunity": mean_top * (1 + math.log10(1 + len(ok))),
            "median_salary": sorted(s.salary for s in ok if s.salary)[len(ok) // 2] if ok else None,
            "avg_lottery": round(sum(s.lottery for s in ok) / len(ok), 2) if ok else None,
            "top_exclusions": sorted(reasons.items(), key=lambda kv: -kv[1])[:3],
            "note": role.note,
        })
    total = sum(r["opportunity"] for r in rows) or 1
    for r in rows:
        r["effort_share"] = round(100 * r["opportunity"] / total)
    return sorted(rows, key=lambda r: -r["opportunity"])


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
