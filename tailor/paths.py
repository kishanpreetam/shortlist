from pathlib import Path

# The same folders Shortlist uses, so Tailor's caches (results/requirements, rewrites, summaries, suggestions) and
# the learned tools (data/lexicon.json) stay where they are. Tailor only reads Shortlist's own files.
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RESULTS = ROOT / "results"
