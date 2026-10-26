# Does a big public skills list help pick a posting's keywords?

Applicant tracking systems match resumes against large skills lists. Tailor finds a posting's keywords with a built-in list of about 175 tools and skills, plus a read of the posting itself. This test asks whether a much bigger public list would catch tools Tailor misses.

**Answer: almost never.** Across 46 real data and AI postings, the US Department of Labor's O\*NET list of 8,753 software names found 197 tool mentions. Tailor had already caught 193 of them.

## Results

| O\*NET tool mentions in the 46 postings | 197 |
|---|---|
| Recognized by Tailor's built-in list | 176 |
| Not in that list, but caught by the read of the posting | 17 |
| Missed by both | 4 |

The 4 misses:
- **2 real tools:** NetSuite and Shopify, in one preferred line of a Formlabs posting: "ERP, CRM, or eCommerce (NetSuite, Salesforce, Shopify, or similar)".
- **2 noise matches:** "3D printing", from the company's description, and "Flex", from a "Flex Work" policy.

Of the 176 the built-in list recognized, 175 made the final keyword list; the cap on tools a posting doesn't require cut the other one.

**The reverse is the bigger gap.** O\*NET 31.0 has no entry for Databricks, dbt, Flink, Dagster, Presto, Superset, Streamlit, FastAPI, Polars, LangGraph, LlamaIndex, Bedrock or Microsoft Fabric, though these postings ask for them. Tailor finds them all.

**Without the read of the posting,** the built-in list alone caught 176 of 197, or 89%.

Every match is in `results.json`: the posting, the O\*NET name, where the posting asks for it, and whether Tailor caught it. Each posting also carries Tailor's full keyword list.

## Data

- **O\*NET 31.0 Software Skills.** The file is `software_skills.csv` from https://www.onetcenter.org/database.html, downloaded Oct 26, 2026. Its SHA-256 is `6aabb96b464288db849580e6510530efff3f80e25dde2bb23f1fc36bb526b016`. It isn't copied here; `compare.py` reads your own download.
- **The postings.** These are 46 real postings from employers' own job boards, collected in October 2026. They cover AI engineer, data engineer, data scientist and data analyst roles. `postings.json` gives each one's company, title, role family and link. The posting texts aren't included, since they belong to the employers. Two more postings had no saved read of the posting and were left out.

This page includes information from the O\*NET 31.0 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA). Used under the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license. O\*NET® is a trademark of USDOL/ETA. Kishan Preetam Kommana has modified all or some of this information: the match forms described below are derived from O\*NET's names. USDOL/ETA has not approved, endorsed, or tested these modifications.

## Method

`compare.py` turns each O\*NET name into the forms a posting writes it in:
- "Amazon Web Services AWS software" becomes "Amazon Web Services" and "AWS".
- "Microsoft SQL Server" also matches "SQL Server".
- "Structured query language SQL" also matches "SQL".

The matching rules:
- **Whole words only.**
- **One ordinary word or an acronym matches only as written.** "Spark" counts, but "spark curiosity" doesn't.
- **Some names are skipped:**
  - kinds of software, such as "Email software";
  - lone ordinary words, such as "Analyze";
  - generic acronyms, such as AI, ERP and CRM;
  - company names on their own;
  - the employer's own products.
- **A match inside a longer one counts once.** "Spark" inside "Apache Spark" is one mention.

Each match is then checked against the keyword list `resume.job_keywords` builds for that posting:
- **Recognized by the built-in list:** the engine's own tool list knows it.
- **Caught by the read of the posting:** only the saved read of the posting listed it. That read is `rewrite.keywords`, which Tailor caches the first time it opens a posting.
- **Missed:** neither did.

"Where" is the requirement kind the posting gives the tool, or the nearest section heading above it.

## Limits

- **46 postings is a small set,** and all are data and AI roles. Treat this as a sanity check, not a benchmark.
- **This measures recall against O\*NET's names only.** It says nothing about precision.
- **The matching rules are heuristics.** Every miss was checked by hand.
- **The read of each posting is a Claude step,** cached when Tailor first read the posting. A fresh read can differ.

## Run it

1. Download `software_skills.csv` from the O\*NET 31.0 database.
2. Put the postings in a folder: a `postings.json` like the one here, plus one `<key>.txt` per posting with its full text.
3. Run:

   ```
   .venv/bin/python evals/onet/compare.py software_skills.csv POSTINGS_DIR
   ```

The script never calls Claude:
- It uses the posting reads Tailor saved in `results/requirements`.
- A posting without a saved read uses the built-in list alone.
- Reads are filed under your profile's never-words, as Tailor files them, so the script reads `profile.yaml` for those words only and never prints them.
- It needs a plain word list to tell product names from ordinary words. The default is `/usr/share/dict/words`; pass another with `--words`.

It writes `results.json` and prints the totals and every miss.
