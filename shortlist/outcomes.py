"""Learning from your own results.

Log what happens to each application, and the scorer adjusts how much it
trusts each role family. Each family starts from a prior of a 10% response
rate, Beta(1, 9), and is updated with your outcomes. Below 3 logged
applications in a family, the prior dominates and nothing changes.
"""

from __future__ import annotations

import csv
from datetime import date

from .paths import RESULTS

LOG = RESULTS / "outcomes.csv"
FIELDS = ["date", "company", "title", "role", "channel", "outcome"]
CHANNELS = ("cold", "referral", "direct", "recruiter", "event")
OUTCOMES = ("no_response", "rejected", "screen", "interview", "offer")
RESPONDED = {"screen", "interview", "offer"}
PRIOR_A, PRIOR_B = 1, 9  # 10% prior response rate


def log(company: str, title: str, role: str, channel: str, outcome: str) -> None:
    if channel not in CHANNELS or outcome not in OUTCOMES:
        raise ValueError(f"channel must be one of {CHANNELS}; outcome one of {OUTCOMES}")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({"date": date.today().isoformat(), "company": company, "title": title,
                    "role": role, "channel": channel, "outcome": outcome})


def role_multipliers() -> dict[str, tuple[float, str]]:
    """{role: (multiplier, explanation)} for families with at least 3 logged applications."""
    if not LOG.exists():
        return {}
    counts: dict[str, list[int]] = {}
    with LOG.open() as f:
        for row in csv.DictReader(f):
            c = counts.setdefault(row["role"], [0, 0])
            c[0 if row["outcome"] in RESPONDED else 1] += 1
    out = {}
    prior_mean = PRIOR_A / (PRIOR_A + PRIOR_B)
    for role, (yes, no) in counts.items():
        if yes + no < 3:
            continue
        posterior = (PRIOR_A + yes) / (PRIOR_A + PRIOR_B + yes + no)
        mult = min(max(posterior / prior_mean, 0.5), 2.0)
        out[role] = (mult, f"{yes}/{yes + no} responses so far, x{mult:.2f}")
    return out
