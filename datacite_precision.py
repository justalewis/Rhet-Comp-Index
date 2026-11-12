"""datacite_precision.py — measure how well the DataCite scope holds.

Two steps, per the planning document's "Measuring scope quality":

  python datacite_precision.py sample [--per-tier 100] [--seed 1]
      Draw a reproducible random sample from each tier and write
      data/datacite_precision_sample.tsv with an empty `in_field` column.

  (hand-code the file: put 1 for "Writing Studies work, or a research object
   the field uses", 0 for "not", leave blank to skip)

  python datacite_precision.py report [--sample path]
      Read the coded file and write data/datacite_precision.json, which the
      /datacite and /coverage pages display. Re-run the sample annually, since
      the vocabulary and seed lists change.
"""

import argparse
import csv
import datetime
import json
import os
import random

from db import get_conn, init_db

SAMPLE_DEFAULT = "data/datacite_precision_sample.tsv"
REPORT_PATH = "data/datacite_precision.json"
TIERS = ("A", "B", "curated", "C")
COLUMNS = ["doi", "tier", "resource_type", "year", "publisher", "title", "description", "in_field"]


def draw_sample(conn, per_tier=100, seed=1):
    rng = random.Random(seed)
    rows = []
    for tier in TIERS:
        dois = [r["doi"] for r in conn.execute(
            "SELECT doi FROM datacite_works WHERE tier = ? ORDER BY doi", (tier,))]
        for doi in rng.sample(dois, min(per_tier, len(dois))):
            w = conn.execute("SELECT * FROM datacite_works WHERE doi = ?", (doi,)).fetchone()
            rows.append({
                "doi": w["doi"], "tier": w["tier"],
                "resource_type": w["resource_type_gen"] or "", "year": w["year"] or "",
                "publisher": w["publisher"] or "", "title": w["title"] or "",
                "description": (w["description"] or "")[:400], "in_field": "",
            })
    return rows


def write_sample(rows, path=SAMPLE_DEFAULT):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter="\t")
        w.writeheader()
        w.writerows(rows)


def compute_report(rows):
    """Per-tier precision from coded rows. Blank `in_field` rows are skipped."""
    tiers = []
    for tier in TIERS:
        coded = [r for r in rows if r["tier"] == tier and str(r.get("in_field", "")).strip() in ("0", "1")]
        if not coded:
            continue
        hits = sum(1 for r in coded if str(r["in_field"]).strip() == "1")
        tiers.append({"tier": tier, "n": len(coded), "in_field": hits,
                      "precision": hits / len(coded)})
    return {"coded_on": datetime.date.today().isoformat(), "tiers": tiers}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--per-tier", type=int, default=100)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--out", default=SAMPLE_DEFAULT)
    r = sub.add_parser("report")
    r.add_argument("--sample", default=SAMPLE_DEFAULT)
    r.add_argument("--out", default=REPORT_PATH)
    args = ap.parse_args()

    if args.cmd == "sample":
        init_db()
        with get_conn() as conn:
            rows = draw_sample(conn, args.per_tier, args.seed)
        write_sample(rows, args.out)
        print(f"Wrote {len(rows)} rows to {args.out}. Code the in_field column (1/0), then run `report`.")
    else:
        with open(args.sample, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        report = compute_report(rows)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        for t in report["tiers"]:
            print(f"tier {t['tier']:<8} {t['in_field']}/{t['n']}  precision {t['precision']:.0%}")


if __name__ == "__main__":
    main()
