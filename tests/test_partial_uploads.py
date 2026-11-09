"""The challenge uploads unseen files, so the analysis must survive any subset
of the pack: each file alone, every file but one, and nothing at all. Each
case must still produce every output and an answers.json with the template's
exact structure."""
import json
import os

import pytest

from axeom import DataStore, build_model
from axeom.analysis import run_all
from axeom.analysis.reports import write_all

ROOT = os.path.dirname(os.path.dirname(__file__))
ORIG = os.path.join(ROOT, "data", "original")
TEMPLATE = os.path.join(ROOT, "data", "answers_template.json")
FILES = sorted(os.listdir(ORIG))
CASES = [[f] for f in FILES] + [[g for g in FILES if g != f] for f in FILES] + [[]]


def _shape(x):
    if isinstance(x, dict):
        return {k: _shape(v) for k, v in x.items()}
    return type(x).__name__ if not isinstance(x, list) else "list"


@pytest.mark.parametrize("files", CASES, ids=["only-" + c[0] if len(c) == 1 else
                                               "none" if not c else "without-" + (set(FILES) - set(c)).pop()
                                               for c in CASES])
def test_any_subset_of_files_runs(files, tmp_path):
    store = DataStore()
    store.ingest([(f, open(os.path.join(ORIG, f), "rb").read()) for f in files])
    a = run_all(build_model(store.raw))
    write_all(a, str(tmp_path), TEMPLATE)
    for name in ["answers.json", "evidence_ledger.csv", "financial_reconciliation.csv", "client_identity_report.csv",
                 "lost_requests_report.csv", "churn_analysis.csv", "marketing_analysis.csv"]:
        assert (tmp_path / name).exists(), name
    out = json.load(open(tmp_path / "answers.json"))
    tpl = json.load(open(TEMPLATE))
    assert set(out) == set(tpl)
