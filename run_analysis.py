"""Answer the six challenge questions and write all Phase 2 outputs.

    python run_analysis.py                 # original pack -> outputs/
    python run_analysis.py my_folder out   # any folder of pack-format files

The original files are only read, never modified.
"""
import os
import sys

from axeom import DataStore, build_model
from axeom.analysis import run_all
from axeom.analysis.reports import write_all

HERE = os.path.dirname(os.path.abspath(__file__))


def main(data_dir=None, out_dir=None):
    data_dir = data_dir or os.path.join(HERE, "data", "original")
    out_dir = out_dir or os.path.join(HERE, "outputs")
    store, reports = DataStore.from_folder(data_dir)
    for r in reports:
        if not r.accepted:
            print("REJECTED", r.file_name, r.errors)
    model = build_model(store.raw, os.path.join(HERE, "data", "match_decisions.csv"))
    analysis = run_all(model)
    paths = write_all(analysis, out_dir, os.path.join(HERE, "data", "answers_template.json"))
    s = analysis.finance.summary
    print(f"clients: {len(analysis.clients.clients)} | overdue {s['overdue_total']:,.2f} | never billed "
          f"{s['never_billed_total']:,.2f} | we owe {s['we_owe_total']:,.2f}")
    print(f"most likely to leave: {analysis.churn.most_likely['client']} | best channel: "
          f"{analysis.marketing.best['channel']} | requests lost since {analysis.comms.worse_since}")
    for name, p in paths.items():
        print("wrote", os.path.relpath(p, HERE))
    return analysis


if __name__ == "__main__":
    main(*sys.argv[1:3])
