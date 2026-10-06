"""Imports DOL H-1B LCA disclosure data (the quarterly Excel files).

Keeps certified H-1B filings, maps each job title to a role family, and
saves per-employer counts so the scorer can tell which employers actually
sponsor each kind of role. Run once per new DOL release:

    python -m shortlist lca ~/Downloads/LCA_Disclosure_Data_FY2026_Q3.xlsx
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl

from .paths import DATA
from .roles import role_for_title

OUT = DATA / "lca_employers.parquet"
ROLE_STATS = DATA / "lca_roles.parquet"
COLUMNS = [
    "CASE_STATUS", "VISA_CLASS", "JOB_TITLE", "SOC_CODE", "EMPLOYER_NAME", "EMPLOYER_FEIN",
    "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY", "PW_WAGE_LEVEL", "WORKSITE_STATE", "DECISION_DATE",
]
UNIT_TO_YEAR = {"Year": 1, "Hour": 2080, "Week": 52, "Bi-Weekly": 26, "Month": 12}
_SUFFIX = re.compile(r"\b(inc|incorporated|llc|l l c|ltd|limited|corp|corporation|co|company|plc|pbc|lp|llp|holdings|group|usa|us|the)\b")


def norm(name: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"\s+", "", s)


def import_files(paths: list[Path]) -> tuple[int, int]:
    frames = []
    for p in paths:
        df = pl.read_excel(p, engine="calamine", columns=COLUMNS, infer_schema_length=0)
        frames.append(df.with_columns(pl.lit(p.name).alias("source_file")))
    df = pl.concat(frames, how="diagonal_relaxed")
    df = df.filter(
        pl.col("VISA_CLASS").str.contains("H-1B")
        & pl.col("CASE_STATUS").str.starts_with("Certified")
        & pl.col("SOC_CODE").str.contains(r"^(15-|13-1111|13-1161|13-2051|11-3021)")
    )
    df = df.with_columns(
        pl.col("JOB_TITLE").map_elements(lambda t: (r.key if (r := role_for_title(t or "")) else "other"), return_dtype=pl.Utf8).alias("role"),
        pl.col("EMPLOYER_NAME").map_elements(norm, return_dtype=pl.Utf8).alias("employer_norm"),
        (pl.col("WAGE_RATE_OF_PAY_FROM").cast(pl.Float64, strict=False)
         * pl.col("WAGE_UNIT_OF_PAY").replace_strict(UNIT_TO_YEAR, default=1, return_dtype=pl.Float64)).alias("annual_wage"),
    )
    employers = df.group_by("employer_norm").agg(
        pl.col("EMPLOYER_NAME").mode().first().alias("employer"),
        pl.len().alias("filings"),
        pl.col("role").value_counts().alias("by_role"),
        pl.col("annual_wage").median().alias("median_wage"),
    )
    employers.write_parquet(OUT)
    roles = df.group_by("role").agg(
        pl.len().alias("filings"),
        pl.col("annual_wage").median().alias("median_wage"),
        (pl.col("PW_WAGE_LEVEL") == "I").mean().alias("share_level_1"),
        (pl.col("PW_WAGE_LEVEL") == "II").mean().alias("share_level_2"),
        (pl.col("PW_WAGE_LEVEL") == "III").mean().alias("share_level_3"),
        (pl.col("PW_WAGE_LEVEL") == "IV").mean().alias("share_level_4"),
        pl.col("employer_norm").n_unique().alias("employers"),
    )
    roles.write_parquet(ROLE_STATS)
    return df.height, employers.height


def load() -> dict[str, dict] | None:
    """{normalized employer name: {"filings": n, "by_role": {role: n}}} or None if not imported."""
    if not OUT.exists():
        return None
    out = {}
    for row in pl.read_parquet(OUT).iter_rows(named=True):
        by_role = {d["role"]: d["count"] for d in (row["by_role"] or [])}
        out[row["employer_norm"]] = {"employer": row["employer"], "filings": row["filings"], "by_role": by_role}
    return out
