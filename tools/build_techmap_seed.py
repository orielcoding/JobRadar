"""Builds seeds/techmap_companies.json: Israeli companies whose ATS ids are
listed in the Israeli Tech Map (github.com/mluggy/techmap, ODbL v1.0, by
Michael Lugassy). Used by `jobradar import-techmap`.

Refresh:
    git clone --depth 1 https://github.com/mluggy/techmap /tmp/techmap
    python tools/build_techmap_seed.py /tmp/techmap
"""

import json
import sys
from datetime import date
from pathlib import Path

SIZES = {"xs": "1-10", "s": "11-50", "m": "51-200", "l": "201-1,000", "xl": "1,001+"}


def main(repo: str) -> None:
    root = Path(repo)
    cats = json.loads((root / "categories.json").read_text(encoding="utf-8"))
    out = []
    for f in sorted((root / "companies").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if d.get("isActive") is False:
            continue
        entry = {"name": d.get("name", "").strip(), "industry": cats.get(str(d.get("categoryId")), ""),
                 "size": d.get("size", ""), "cities": sorted({a.get("city", "") for a in d.get("addresses") or []} - {""})}
        if d.get("comeetId") and "/" in d["comeetId"]:
            slug, uid = d["comeetId"].split("/")[:2]
            entry.update(ats="comeet", slug=slug, uid=uid, careers_url=f"https://www.comeet.com/jobs/{slug}/{uid}")
        elif d.get("greenhouseId"):
            entry.update(ats="greenhouse", slug=d["greenhouseId"])
        elif d.get("leverId"):
            entry.update(ats="lever", slug=d["leverId"])
        else:
            continue
        out.append(entry)
    payload = {
        "source": "Israeli Tech Map - https://github.com/mluggy/techmap (ODbL v1.0, by Michael Lugassy)",
        "built_on": date.today().isoformat(),
        "sizes": SIZES,
        "companies": out,
    }
    dest = Path(__file__).resolve().parents[1] / "seeds" / "techmap_companies.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(out)} companies to {dest}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "techmap")
