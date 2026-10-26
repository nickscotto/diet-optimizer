"""Append Aldi price rows to data/aldi_prices.json from a text table.

Each line: label|Aldi product title|price in dollars|package size in grams|mode
mode is "pack" (sold in that package) or "lb" (sold by the pound: price is per lb, pack_g is 453.6).
Usage: python price_add.py rows.txt
"""
import datetime
import json
import sys
from pathlib import Path

PATH = Path(__file__).parent / "data" / "aldi_prices.json"


def main(rows_file):
    data = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    today = datetime.date.today().isoformat()
    for line in Path(rows_file).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        label, title, price, grams, mode = [p.strip() for p in line.split("|")]
        data[label] = {"title": title, "price": float(price), "pack_g": float(grams), "mode": mode, "date": today}
    PATH.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(len(data), "prices")


if __name__ == "__main__":
    main(sys.argv[1])
