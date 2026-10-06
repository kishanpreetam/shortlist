"""Writes results/report.html (sortable, filterable) and CSVs."""

from __future__ import annotations

import csv
import html
import re
from datetime import datetime

from .paths import RESULTS
from .score import Scored

LEVEL_NAMES = {0: "below I", 1: "I", 2: "II", 3: "III", 4: "IV", None: "?"}


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _loc(s: Scored) -> str:
    loc = (s.posting.location or "").lower()
    if re.search(r"boston|cambridge|somerville|waltham|needham|woburn|burlington|andover|lexington|quincy|newton|, ma\b|massachusetts", loc):
        return "boston"
    if s.posting.remote or "remote" in loc:
        return "remote"
    return "elsewhere"


def write(scored: list[Scored], roles: list[dict], profile: dict, multipliers: dict, data_note: str, manual=()) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    ranked = sorted((s for s in scored if not s.excluded), key=lambda s: -s.score)

    with (RESULTS / "postings.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["score", "company", "title", "role", "location", "salary", "salary_source", "wage_level",
                    "lottery_odds", "sponsor", "sponsor_reason", "fit", "matched", "missing", "years_required",
                    "age_days", "leverage", "excluded", "url"])
        for s in sorted(scored, key=lambda s: -s.score):
            w.writerow([round(s.score, 1), s.company.name, s.posting.title, s.role.key, s.posting.location,
                        round(s.salary or 0), s.salary_source, LEVEL_NAMES.get(s.wage_level), round(s.lottery, 2),
                        round(s.sponsor, 2), s.sponsor_reason, round(s.fit, 2), " ".join(s.matched), " ".join(s.missing),
                        s.years_required, s.age_days, s.leverage_reason, s.excluded or "", s.posting.url])

    role_rows = "".join(
        f"<tr><td><b>{_e(r['role'])}</b><div class=note>{_e(r['note'])}</div></td>"
        f"<td class=num>{r['effort_share']}%<div class=bar><span style='width:{r['effort_share']}%'></span></div></td>"
        f"<td class=num>{r['eligible']}<span class=dim> / {r['seen']}</span></td><td class=num>{r['top10_mean']}</td>"
        f"<td class=num>{'$' + format(round(r['median_salary']), ',') if r['median_salary'] else '-'}</td>"
        f"<td class=num>{'' if r['avg_lottery'] is None else str(round(100 * r['avg_lottery'])) + '%'}</td>"
        f"<td class=dim>{_e(', '.join(f'{k}: {v}' for k, v in r['top_exclusions']))}"
        f"{'<br>' + _e(multipliers[r['key']][1]) if r['key'] in multipliers else ''}</td></tr>"
        for r in roles
    )
    rows = "".join(
        f"<tr data-role='{_e(s.role.key)}' data-loc='{_loc(s)}'><td class=num><b>{s.score:.0f}</b></td><td>{_e(s.company.name)}</td>"
        f"<td><a href='{_e(s.posting.url)}' target=_blank rel=noopener>{_e(s.posting.title)}</a>"
        f"<div class=dim>{_e(s.posting.location)}</div></td><td>{_e(s.role.label)}</td>"
        f"<td class=num>{'$' + format(round(s.salary), ',') if s.salary else '-'}<div class=dim>{_e(s.salary_source)}</div></td>"
        f"<td class=num>{LEVEL_NAMES.get(s.wage_level)}<div class=dim>{round(100 * s.lottery)}% lottery</div></td>"
        f"<td>{round(100 * s.sponsor)}%<div class=dim>{_e(s.sponsor_reason)}</div></td>"
        f"<td>{round(100 * s.fit)}%<div class=dim>has: {_e(', '.join(s.matched[:6]))}"
        f"{'<br>gaps: ' + _e(', '.join(s.missing[:4])) if s.missing else ''}</div></td>"
        f"<td class=num>{_e(s.years_required) if s.years_required is not None else '-'}</td>"
        f"<td class=num>{_e(s.age_days) if s.age_days is not None else '-'}</td>"
        f"<td class=dim>{_e(s.leverage_reason)}</td></tr>"
        for s in ranked[:300]
    )
    options = "".join(f"<option value='{_e(r['key'])}'>{_e(r['role'])}</option>" for r in roles)
    manual_rows = "".join(
        f"<tr><td><b>{_e(c.name)}</b></td><td class=num>{(rec or {}).get('filings', '-')}</td>"
        f"<td class=dim>{_e(', '.join(f'{k} {v}' for k, v in sorted(((rec or {}).get('by_role') or {}).items(), key=lambda kv: -kv[1])[:4]))}</td>"
        f"<td><a href='{_e(c.portal)}' target=_blank rel=noopener>careers page</a></td></tr>"
        for c, rec in sorted(manual, key=lambda cr: -((cr[1] or {}).get('filings', 0)))
    )
    excluded = {}
    for s in scored:
        if s.excluded:
            k = s.excluded.split(" (")[0]
            excluded[k] = excluded.get(k, 0) + 1
    excl = ", ".join(f"{k}: {v}" for k, v in sorted(excluded.items(), key=lambda kv: -kv[1]))

    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Shortlist</title><style>
:root{{--bg:#fbfaf7;--fg:#1d1c1a;--dim:#6f6b63;--line:#e6e2d9;--accent:#2f5d50;--bar:#d7e5df}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151513;--fg:#ecebe6;--dim:#9a968c;--line:#2d2c28;--accent:#8cc7b2;--bar:#24392f}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:24px 16px;max-width:1400px;margin:auto}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}} a{{color:var(--accent)}}
.dim,.note{{color:var(--dim);font-size:12px}} table{{border-collapse:collapse;width:100%}} .wrap{{overflow-x:auto}}
th,td{{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid var(--line)}} th{{cursor:pointer;font-size:12px;color:var(--dim);white-space:nowrap}}
.num{{text-align:right;white-space:nowrap}} .bar{{height:4px;background:var(--line);margin-top:4px}} .bar span{{display:block;height:4px;background:var(--accent)}}
select{{font:inherit;padding:4px 8px;background:var(--bg);color:var(--fg);border:1px solid var(--line)}}
</style></head><body>
<h1>Shortlist</h1>
<div class=dim>{_e(profile.get('name', ''))} · {len(scored)} postings read, {len(ranked)} you can realistically go for ·
generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · {_e(data_note)}</div>

<h2>Where to spend your effort</h2>
<div class=wrap><table><tr><th>Role family</th><th class=num>Effort</th><th class=num>Eligible / seen</th><th class=num>Top-10 score</th>
<th class=num>Median salary</th><th class=num>Lottery odds</th><th>Most common reasons for exclusion</th></tr>{role_rows}</table></div>

<h2>Best postings for you right now</h2>
<div>Role: <select id=f onchange="filter()"><option value=''>All</option>{options}</select>
&nbsp; Location: <select id=l onchange="filter()"><option value=''>Anywhere</option><option value='boston'>Boston area</option>
<option value='remote'>Remote</option><option value='elsewhere'>Elsewhere (relocate)</option></select></div>
<div class=wrap><table id=t><thead><tr><th class=num>Score</th><th>Company</th><th>Posting</th><th>Role</th><th class=num>Salary</th>
<th class=num>Wage level</th><th>Sponsor odds</th><th>Fit</th><th class=num>Yrs asked</th><th class=num>Days up</th><th>Leverage</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class=dim>Excluded: {_e(excl)}.</p>

<h2>No lottery: cap-exempt employers to check by hand</h2>
<p class=dim>Universities, their hospitals and nonprofit research institutes can sponsor an H-1B any time, with no lottery. Their jobs sit on
portals this tool can't read, so search them for data engineer, data scientist, data analyst and research software engineer. Confirm cap-exempt status with each.</p>
<div class=wrap><table><tr><th>Employer</th><th class=num>Tech H-1B filings 2025-26</th><th>Roles they file for</th><th></th></tr>{manual_rows}</table></div>
<p class=dim>Score = fit × experience × sponsor odds × (0.5 + 0.5 × lottery odds) × freshness × role access × leverage. Lottery odds use
DHS's projected selection rates by wage level over {profile.get('lottery_seasons', 2)} lottery seasons. They're estimates, not legal advice;
confirm sponsorship with each employer.</p>
<script>
function filter(){{const v=document.getElementById('f').value,l=document.getElementById('l').value;
for(const r of document.querySelectorAll('#t tbody tr'))r.style.display=(!v||r.dataset.role===v)&&(!l||r.dataset.loc===l)?'':'none'}}
document.querySelectorAll('#t th').forEach((th,i)=>th.onclick=()=>{{const b=document.querySelector('#t tbody');const rs=[...b.rows];
const num=x=>parseFloat(x.replace(/[^0-9.-]/g,''));const asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
rs.sort((a,c)=>{{const x=a.cells[i].innerText,y=c.cells[i].innerText;const nx=num(x),ny=num(y);
return (isNaN(nx)||isNaN(ny)?x.localeCompare(y):nx-ny)*(asc?1:-1)}});rs.forEach(r=>b.appendChild(r))}});
</script></body></html>"""
    (RESULTS / "report.html").write_text(page)


def write_targets(scored: list[Scored], records: dict, profile: dict, manual=(), limit: int = 30) -> str:
    """results/targets.md: employers ranked by your best openings there, with the evidence and a next step."""
    import urllib.parse
    by_company: dict[str, list[Scored]] = {}
    for s in scored:
        if not s.excluded:
            by_company.setdefault(s.company.name, []).append(s)
    ranked = []
    for name, items in by_company.items():
        items.sort(key=lambda s: -s.score)
        top = [s.score for s in items[:3]] + [0, 0]
        ranked.append((top[0] + 0.5 * top[1] + 0.25 * top[2], name, items))
    ranked.sort(reverse=True)

    def evidence(company, rec):
        if company.cap_exempt:
            return "cap-exempt: no H-1B lottery (confirm with them)"
        if not rec:
            return "no H-1B filing data found; ask about sponsorship in the first call"
        roles = ", ".join(f"{k.replace('_', ' ')} {n}" for k, n in sorted(rec["by_role"].items(), key=lambda kv: -kv[1])[:3])
        return f"{rec['filings']} H-1B filings for your role families in 2025-26 ({roles})"

    def angle(company, items):
        out = []
        for tag, proof in (profile.get("proof") or {}).items():
            if tag in company.tags and proof.get("angle"):
                out.append(proof["angle"])
        data_roles = ("data_engineer", "analytics_engineer", "data_analyst", "data_scientist")
        if profile.get("data_angle") and ("data" in company.tags or any(s.role.key in data_roles for s in items[:3])):
            out.append(profile["data_angle"])
        if any(_loc(s) == "boston" for s in items) and profile.get("home_label"):
            out.append(f"local: you're in {profile['home_label']}")
        return "; ".join(out) or "lead with the project closest to the role"

    school = profile.get("school", "your school")
    school_slug = profile.get("school_linkedin")

    lines = ["# Target companies", "",
             "Generated by `python -m shortlist run`. Ranked by your best openings at each employer: "
             "fit x experience x sponsorship x lottery odds x freshness. Re-run any time; postings change daily.", "",
             "How to use each entry:",
             f"1. Find a {school} alum there (link below) and ask a specific question about their work.",
             "2. After they reply, ask for a referral to the exact job ID.",
             "3. Ask about sponsorship in the first call.", ""]
    for i, (score, name, items) in enumerate(ranked[:limit], 1):
        company = items[0].company
        lines += [f"## {i}. {name}", "",
                  f"- **Sponsorship:** {evidence(company, records.get(name))}",
                  f"- **Your angle:** {angle(company, items)}"]
        if school_slug:
            q = urllib.parse.quote(re.sub(r"\s+(Inc|Llc|Corp|Corporation)$", "", name, flags=re.I))
            lines.append(f"- **Find alumni:** https://www.linkedin.com/school/{school_slug}/people/?keywords={q}")
        lines.append("- **Best openings:**")
        for s in items[:3]:
            lines.append(f"  - [{s.posting.title}]({s.posting.url}): {s.posting.location or 'location n/a'}. "
                         f"Score {s.score:.0f} (fit {round(100 * s.fit)}%, sponsor {round(100 * s.sponsor)}%, lottery {round(100 * s.lottery)}%)")
        lines.append("")
    if manual:
        lines += ["## No lottery: cap-exempt employers to search by hand", "",
                  "Hospitals, universities and nonprofit research institutes skip the H-1B lottery. Data analyst roles are common "
                  "there, which makes them the best place to apply for analyst jobs. Search each portal for data engineer, data scientist, "
                  "data analyst and research software engineer.", ""]
        for company, rec in sorted(manual, key=lambda cr: -((cr[1] or {}).get("filings", 0))):
            lines.append(f"- **{company.name}**: {(rec or {}).get('filings', 'unknown')} tech H-1B filings in 2025-26. {company.portal}")
        lines.append("")
    out = "\n".join(lines)
    (RESULTS / "targets.md").write_text(out)
    return out
