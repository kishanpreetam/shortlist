# Shortlist

**Which jobs can you realistically get, given your skills, your experience, and a visa?**

Shortlist reads live postings from about 200 employers' public job boards. It scores every posting for one person and ranks both role families and individual openings by realistic chance. It was built for an international new grad who needs H-1B sponsorship, where "a good fit" and "a job you can actually take" are different questions.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp profile.example.yaml profile.yaml        # your skills, experience, home area, contacts (stays local)
.venv/bin/python -m shortlist setup         # download the public wage table and job-board lists
.venv/bin/python -m shortlist discover      # find employers that sponsor your roles and have readable boards
.venv/bin/python -m shortlist run           # scan, score, write results/report.html and results/targets.md
```

## The score

```
score = fit × experience × sponsor × (0.5 + 0.5 × lottery) × freshness × access × leverage
```

| Factor | What it measures |
|---|---|
| **fit** | Skills the posting asks for that you have, weighted by your level (60%), blended with how well you fit the role family overall (40%). |
| **experience** | 1.0 if the years asked for are within reach, 0.75 at +1 year, 0.45 at +2. Postings asking 3+ more years are excluded. |
| **sponsor** | In order of precedence: the posting's own wording ("we sponsor" / "no sponsorship"), cap-exempt status, then the employer's H-1B filings for that role family in 2025–26. |
| **lottery** | Your chance of an H-1B selection within your remaining lottery seasons. Since FY2027 the lottery is weighted by wage level, so the salary's level against the official prevailing wage (by occupation code and worksite area) sets the odds. Cap-exempt employers score 1.0. It counts half, because the offer itself doesn't depend on it. |
| **freshness** | Postings up 60+ days are often filled or stale. |
| **access** | How open the role family is to 0–2 years of experience. Explicit new-grad and "I/associate" titles get a floor of 0.8. |
| **leverage** | Warm contacts, alumni from your school, and proof-of-work projects that match the employer (your `proof` settings). |

**Hard exclusions:**
- senior titles and internships;
- non-US roles;
- contract roles, because STEM OPT needs W-2 employment;
- off-profile specialties (mobile, hardware, games);
- citizenship or clearance requirements;
- explicit "no sponsorship";
- asks of 3+ more years than you have;
- salaries below the H-1B minimum for every occupation code that fits the role.

**Role families** are ranked by `mean(top-10 posting scores) × (1 + log10(1 + eligible postings))`. The result becomes a suggested split of your effort.

**It learns from your results.** Log each application's outcome with `python -m shortlist log …`. Each role family starts from a Beta(1, 9) prior, about a 10% response rate. Once a family has 3 or more outcomes, its scores shift by your actual response rate.

## Data sources

All public. Run `python -m shortlist setup` to fetch the static files.

- **Job postings:** Greenhouse, Lever and Ashby public job-board APIs. No login. At most 1 request per second per host, cached 6 hours.
- **Employer-to-board mapping:** [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) (MIT). Each discovered board is confirmed against the company's own name.
- **H-1B filings:** h1bdata.info, a mirror of DOL LCA disclosure data; its `robots.txt` allows crawling. Cached a week. `python -m shortlist lca <DOL file.xlsx>` imports the official file directly and takes precedence.
- **Prevailing wages:** the OFLC wage file for July 2026 to June 2027, from flag.dol.gov.
- **Lottery odds by wage level:** DHS final rule, 90 FR 60864 (Dec 2025).
- **Role priors** (fit, access, typical wage, plausible occupation codes): 2026 job-market and LCA research, documented in `shortlist/roles.py`.

## Responsible use

- **Respect the public data sources.**
  - Every source is public and read-only.
  - Requests are spaced at least a second apart per host.
  - Results are cached: job boards for 6 hours, filing data for a week.
- **Run it for your own search.** Don't turn it into a high-volume scraper.
- **Don't republish downloaded postings or filing data.** `data/` and `results/` are gitignored for that reason.
- **Keep your profile private.** `profile.yaml` describes you and never leaves your machine; it's gitignored.

## Limits

- **These are estimates, not legal advice.** Confirm sponsorship with each employer and your status with your school's international office.
- **Coverage is limited to three job-board platforms.** Employers on Workday and similar portals can't be read; cap-exempt hospitals and universities are listed in the report to check by hand.
- **Filing counts are by job title,** so titles the classifier doesn't recognize (for example "Member of Technical Staff") don't count toward any role family.
- **Pending rules could lower the odds:** the proposed DOL wage-level rule and the proposed $103K cap-subject fee would shift the lottery numbers if finalized.

## License

MIT. See [LICENSE](LICENSE).
