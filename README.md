# Shortlist

**Which jobs can you realistically get, given your skills, your experience, and a visa?**

Shortlist reads live postings from about 500 employers' public job boards. It scores every posting for one person and ranks both role families and individual openings by realistic chance. A local page then works through a daily queue of the freshest ones, with a resume tailored to each from lines you approved. It was built for an international new grad who needs H-1B sponsorship, where "a good fit" and "a job you can actually take" are different questions.

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
- senior titles, internships and postdocs;
- non-US roles;
- contract roles, because STEM OPT needs W-2 employment;
- off-profile specialties (mobile, hardware, games, clinical, teaching, finance and HR advisory);
- citizenship or clearance requirements;
- explicit "no sponsorship";
- green-card (PERM) postings written for one person, and roles that need a clinical license;
- asks of 3+ more years than you have;
- salaries below the H-1B minimum for every occupation code that fits the role.

**Role families** are ranked by `mean(top-10 posting scores) × (1 + log10(1 + eligible postings))`. The result becomes a suggested split of your effort.

**It learns from your results.** Log each application's outcome with `python -m shortlist log …`. Each role family starts from a Beta(1, 9) prior, about a 10% response rate. Once a family has 3 or more outcomes, its scores shift by your actual response rate.

## The daily queue

```bash
.venv/bin/python -m shortlist today          # rescans if results are 6+ hours old, then opens today's queue
```

`today` picks the day's applications and opens them as a page at `http://127.0.0.1:8765`. It keeps only postings published in the last 24 hours (`--max-age-hours`, 0 to turn it off), using each board's publish time; Workday only says "today" or "yesterday", so its "yesterday" counts as older. Startups come first: every employer is one unless it hires through Workday or `shortlist/established.yaml` lists it. Established employers follow. Within each group it ranks by score × freshness: posted in the last 2 days counts 2×, the last week 1.3×, last month 0.6×. Most applications to a posting arrive in its first 2 to 4 days, and reviewers often stop once the shortlist is strong. It takes at most 2 applications per employer in any 60 days, because interview rates fall with each extra application to one company. It also takes one per job title, and it skips anything you've applied to or skipped. The top 20 jobs get a note for one person there, which fits LinkedIn's limit of about 100 invitations a week. Cards for graduate programs remind you to check the graduation window.

Each card shows:
- the sponsorship evidence and lottery odds;
- the years asked against yours;
- the pay to quote;
- which resume version to send;
- the posting's own skill words to reuse;
- links to find alumni, recruiters and hiring managers there;
- a short message to send one of them.

**Mark applied** logs the application to `results/applications.csv` and schedules one follow-up 6 business days later, since first interviews typically come about 6 days after applying. It shows at the top of the page when it's due. Status changes there (screen, interview, rejected) feed the learning loop above.

**Tailor to this job** builds a one-page resume for that posting from your facts file (`resume_facts` in profile.yaml), using only lines you approved, and picks the points, summary and skills that fit the job. When the Claude Code command line is installed and signed in (`claude auth login`), Claude first rewrites your points toward the posting. A rewrite is used only if it keeps your numbers attached to the same things, names no tool, word or claim your line doesn't already make (beyond plain common words), and claims no more scope; otherwise your own line is used. The preview marks the words each rewrite added and shows your wording under it, **my wording** switches one back, and nothing is saved until **Save PDF**. Claude runs with no tools, no MCP servers and no skills, in an empty folder, with the posting passed as data, so a posting can only change the wording it returns. Lines in a posting aimed at AI tools or applicants (such as "include the word ...", which some employers use to spot AI-written applications) are left out, and the preview tells you so you can follow them yourself. While the page is open, it prepares rewrites for the next 5 jobs in the background, one call at a time, so they open in seconds. Set how many with `rewrite_warmup` (0 turns it off), or turn rewriting off with `rewrite_points: false`. Your lines print with your own periods and colons; the summary never has a colon or semicolon, no line has a double dash, and dates read May 2026 - Present. Headings print as one run of capitals so every parser reads them. The page is checked for these before any PDF is made. **Save PDF** copies the PDF's path for the file picker. **Group by job board** batches forms that look alike.

**It writes to the job's requirements.** Claude first lists what the posting asks for, one requirement per bullet, each marked required, responsibility or preferred. Lines are picked for coverage: required items first, then responsibilities, then preferred ones. The preview says how many required items the page shows, and which lines to change when one is missing. Each job gets its own summary, held to the same fact check, or an approved summary is used. A skill the job wants that your lines don't show gets one question, and your answer is saved to the facts file for later jobs. For a missing requirement, **Suggest lines** drafts full sentences from your approved facts and from facts read from the project's own code: its tests, docs and CI, for projects with a `repo`. It marks a recommended and a strongest draft, and adds nothing until you pick one. Words in `resume_never_words` never print: headings drop them, and lines that use one stay off.

**Refresh** reads every employer's board again (a board read in the last 15 minutes is reused) and rebuilds the queue for a window you pick on the page, 24 to 48 hours.

The page also links to Indeed and Google Jobs searches for each role family, limited to the last 24 hours and newest first. The **LinkedIn card** links each role's job listings and hiring posts from the last 24 hours. To check what you find, copy a results page (Cmd+A, then Cmd+C) into the card. Claude, with no tools, lists the jobs in that copy, and the page adds each company's H-1B filings, whether Shortlist has the posting on the employer's own board, and Mark applied. These are plain links for you to open: those sites' terms forbid automated reading, so Shortlist never fetches them.

The page listens only on 127.0.0.1, checks the Host and Origin headers, and needs a per-run token for every write. Options: `--n 30 --cap 2 --messages 20 --max-age 60 --max-age-hours 24 --no-scan --no-serve`. Terminal equivalents: `applied URL|N`, `update URL --outcome screen`, `followups`. For a phone, `phone-export --out queue.json` writes today's queue and tracker as one document (`--html page.html` adds a script-free copy for the Files app), and `phone-import FOLDER` records the marks made there.

## Data sources

All public. Run `python -m shortlist setup` to fetch the static files.

- **Job postings:** Greenhouse, Lever and Ashby public job-board APIs. No login. At most 1 request per second per host, cached 6 hours.
- **Workday:** the public JSON API behind each employer's myworkdayjobs.com site, searched for your role families, with the same limits. Tenants whose `robots.txt` disallows the API or the job pages are skipped.
- **Workable:** the public widget API behind apply.workable.com boards, with the same limits; `discover --workable` finds H-1B filers there. SmartRecruiters isn't read: its API's `robots.txt` disallows crawlers.
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
- **Coverage is limited to five job-board platforms.** Workday is read through keyword searches, so postings those searches miss aren't seen. Cap-exempt hospitals and universities on other portals are listed in the report to check by hand.
- **Filing counts are by job title,** so titles the classifier doesn't recognize (for example "Member of Technical Staff") don't count toward any role family.
- **Pending rules could lower the odds:** the proposed DOL wage-level rule and the proposed $103K cap-subject fee would shift the lottery numbers if finalized.

## License

MIT. See [LICENSE](LICENSE).
