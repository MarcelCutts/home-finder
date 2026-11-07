"""Where the experiment keeps its data, all under the gitignored data/.

Everything here holds addresses or listing data, so none of it is committed.
"""

from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
INPUTS = DATA / "inputs"
"""Scratch copies of the databases (<dataset>.db) and links to their image caches."""
CACHE = DATA / "cache"
"""Every API answer, so reruns make no requests."""
RESULTS = DATA / "results"
"""run.py's output: one JSON line per listing."""
NARROWED = DATA / "narrowed"
"""narrow.py's output, in the same format."""
HANDCHECK = DATA / "handcheck"
"""Hand-check sheets and the verdicts written into judgements*.csv."""
DATASETS = {"october": INPUTS / "october.db", "february": INPUTS / "february.db"}
