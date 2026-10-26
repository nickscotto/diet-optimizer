"""Build data/contaminants.json from the FDA Total Diet Study elements data.

Source: FDA Total Diet Study, Fiscal Years 2018-2020 Elements Report Supplement (Excel),
https://www.fda.gov/food/fda-total-diet-study-tds/fda-total-diet-study-tds-results
(305+ foods as eaten, 3,000+ samples; mean concentration per food and analyte, in ppb = µg/kg).
A food with no detects has no mean in the file; it is stored as 0 (the lower bound). Output is µg per 100 g.

    python build_contaminants.py [path/to/supplement.xlsx]     (downloads it when no path is given)
"""
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

URL = "https://www.fda.gov/media/159749/download?attachment"
OUT = Path(__file__).parent / "data" / "contaminants.json"
ANALYTES = {"Arsenic (total), As (ppb)": "as", "Cadmium, Cd (ppb)": "cd", "Lead, Pb (ppb)": "pb", "Mercury, Hg (ppb)": "hg"}
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def rows(path):
    z = zipfile.ZipFile(path)
    strings = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t"))
               for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)]
    for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(f"{{{NS['m']}}}row"):
        out = {}
        for c in row.findall("m:c", NS):
            v = c.find("m:v", NS)
            if v is not None:
                out[re.match(r"[A-Z]+", c.get("r")).group(0)] = strings[int(v.text)] if c.get("t") == "s" else v.text
        yield out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        path = Path(__file__).parent / "data" / "tds_elements_supplement.xlsx"
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        path.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    foods = {}
    for r in rows(path):
        key = ANALYTES.get(r.get("D"))
        if not key or not r.get("B"):
            continue
        try:
            mean_ppb = float(r.get("G"))
        except (TypeError, ValueError):
            mean_ppb = 0.0
        foods.setdefault(r["B"], {})[key] = round(mean_ppb / 10, 3)    # ppb (µg/kg) -> µg per 100 g
    OUT.write_text(json.dumps(foods, indent=0, ensure_ascii=False), encoding="utf-8")
    print(f"{len(foods)} foods -> {OUT}")


if __name__ == "__main__":
    main()
