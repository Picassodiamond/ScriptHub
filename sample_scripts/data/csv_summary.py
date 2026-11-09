# --- scripthub ---
# name: Summarise a CSV file
# description: Group the rows of a CSV file by one column and add up another. Try it on sales.csv next to it.
# category: Data
# parameters:
#   - name: file
#     label: CSV file
#     type: file
#     default: sales.csv
#     required: true
#     help: "Relative paths start in this script's folder"
#   - name: group_by
#     label: Group by column
#     type: text
#     default: region
#     required: true
#   - name: value
#     label: Sum of column
#     type: text
#     default: amount
#     required: true
#   - name: sort
#     label: Sort by
#     type: select
#     choices: [total, name, count]
#     default: total
# --- end scripthub ---
"""Print count, total and average of VALUE per GROUP_BY."""
import argparse
import csv
import sys
from collections import defaultdict

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--file", required=True)
parser.add_argument("--group-by", default="region")
parser.add_argument("--value", default="amount")
parser.add_argument("--sort", choices=["total", "name", "count"], default="total")
args = parser.parse_args()

totals, counts, skipped = defaultdict(float), defaultdict(int), 0
try:
    with open(args.file, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in (args.group_by, args.value) if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"No column {', '.join(missing)}. Columns: {', '.join(reader.fieldnames or [])}")
        for row in reader:
            try:
                amount = float(row[args.value])
            except (TypeError, ValueError):
                skipped += 1
                continue
            totals[row[args.group_by]] += amount
            counts[row[args.group_by]] += 1
except OSError as exc:
    sys.exit(f"Cannot read {args.file}: {exc}")

key = {"total": lambda k: -totals[k], "count": lambda k: -counts[k], "name": lambda k: k}[args.sort]
width = max([len(args.group_by)] + [len(k) for k in totals])
print(f"{args.group_by:<{width}}  {'rows':>5}  {'total':>12}  {'average':>10}")
for k in sorted(totals, key=key):
    print(f"{k:<{width}}  {counts[k]:>5}  {totals[k]:>12,.2f}  {totals[k] / counts[k]:>10,.2f}")
print(f"{'all':<{width}}  {sum(counts.values()):>5}  {sum(totals.values()):>12,.2f}")
if skipped:
    print(f"{skipped} row(s) without a number in '{args.value}' were skipped.", file=sys.stderr)
