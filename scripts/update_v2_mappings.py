"""
One-time updater for metadata/v2_policy_mappings.csv.

Replaces existing rows for subtypes that need expanded stock coverage
post-universe-expansion. Adds new rows for subtypes that didn't have
explicit mappings.

After universe expansion to 79 stocks, these mappings reflect the
broader universe's V2 Mode A coverage.

Run once: python -m scripts.update_v2_mappings
Verify: python -m scripts.check_v2_mappings
"""

import csv
from pathlib import Path

MAPPINGS_PATH = Path("metadata/v2_policy_mappings.csv")

# Subtypes whose rows we REPLACE (expanded coverage post-universe-expansion)
REPLACE_SUBTYPES = {
    "RBI_RATE_CUT": {
        "stocks": "HDFCBANK,BAJFINANCE,ICICIBANK,KOTAKBANK,AXISBANK,INDUSINDBK,SBIN,SBICARD",
        "magnitude": "0.20",
        "notes": "Expanded to full bank/NBFC universe post-expansion; rate-sensitive lenders",
    },
    "RBI_RATE_HIKE": {
        "stocks": "HDFCBANK,BAJFINANCE,ICICIBANK,KOTAKBANK,AXISBANK,INDUSINDBK,SBIN,SBICARD",
        "magnitude": "-0.20",
        "notes": "Same set hurt by higher rates",
    },
    "BUDGET_DEFENCE": {
        "stocks": "LT,HAL,BEL,BDL,MAZDOCK",
        "magnitude": "0.35",
        "notes": "Defence PSUs primary; LT defence-adjacent. Expanded post-universe-expansion.",
    },
    "BUDGET_INFRASTRUCTURE": {
        "stocks": "LT,HAL,MAZDOCK",
        "magnitude": "0.25",
        "notes": "Direct infra capex beneficiaries",
    },
    "BUDGET_RAILWAYS": {
        "stocks": "LT,BEL",
        "magnitude": "0.30",
        "notes": "L&T railway segment + BEL signaling/electronics",
    },
}

# New rows to APPEND (subtypes that had no explicit mapping previously)
NEW_ROWS = [
    {
        "subtype": "PRIVATIZATION_PSU_BANK",
        "stocks": "SBIN",
        "magnitude": "0.30",
        "notes": "SBIN is the actual PSU bank privatization candidate; HDFCBANK as private bank already covered in PRIVATIZATION_BANK",
    },
    {
        "subtype": "PLI_PHARMA",
        "stocks": "SUNPHARMA,DRREDDY,CIPLA,DIVISLAB,LUPIN,AUROPHARMA,ALKEM",
        "magnitude": "0.20",
        "notes": "All India pharma universe; DIVISLAB most direct API beneficiary. Overrides previous mapping which was SUNPHARMA-only.",
    },
    {
        "subtype": "PLI_TELECOM",
        "stocks": "BHARTIARTL,IDEA",
        "magnitude": "0.25",
        "notes": "Domestic telecom PLI beneficiaries; IDEA more leveraged to government relief",
    },
    {
        "subtype": "PLI_ELECTRONICS",
        "stocks": "DIXON",
        "magnitude": "0.40",
        "notes": "Dixon is THE listed electronics PLI pure-play",
    },
    {
        "subtype": "PLI_SEMICONDUCTORS",
        "stocks": "DIXON",
        "magnitude": "0.30",
        "notes": "Dixon's semiconductor assembly initiative; less direct than electronics",
    },
]

# PLI_PHARMA already exists in current mappings with SUNPHARMA only -
# treat it as a REPLACE too
REPLACE_SUBTYPES["PLI_PHARMA"] = {
    "stocks": "SUNPHARMA,DRREDDY,CIPLA,DIVISLAB,LUPIN,AUROPHARMA,ALKEM",
    "magnitude": "0.20",
    "notes": "All India pharma universe; DIVISLAB most direct API beneficiary. Expanded post-universe-expansion.",
}
# And remove it from the NEW_ROWS list since we're replacing it
NEW_ROWS = [r for r in NEW_ROWS if r["subtype"] != "PLI_PHARMA"]


def main():
    # Read existing rows
    with open(MAPPINGS_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        existing_rows = list(reader)
    
    print(f"Loaded {len(existing_rows)} existing rows")
    
    # Build new row list
    new_rows = []
    replaced = []
    untouched = []
    
    for row in existing_rows:
        subtype = row["subtype"]
        if subtype in REPLACE_SUBTYPES:
            replacement = REPLACE_SUBTYPES[subtype]
            new_row = {
                "subtype": subtype,
                "affected_stocks": replacement["stocks"],
                "magnitude_override": replacement["magnitude"],
                "notes": replacement["notes"],
            }
            new_rows.append(new_row)
            replaced.append(subtype)
        else:
            new_rows.append(row)
            untouched.append(subtype)
    
    # Append new rows
    for row in NEW_ROWS:
        new_row = {
            "subtype": row["subtype"],
            "affected_stocks": row["stocks"],
            "magnitude_override": row["magnitude"],
            "notes": row["notes"],
        }
        new_rows.append(new_row)
    
    # Write back
    with open(MAPPINGS_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(new_rows)
    
    print()
    print(f"Replaced {len(replaced)} existing rows:")
    for s in replaced:
        print(f"  - {s}")
    
    print()
    print(f"Appended {len(NEW_ROWS)} new rows:")
    for r in NEW_ROWS:
        print(f"  - {r['subtype']}")
    
    print()
    print(f"Total rows in updated file: {len(new_rows)}")
    print(f"Untouched rows: {len(untouched)}")
    
    print()
    print("Verify with: python -m scripts.check_v2_mappings")
    print("Then re-run pipeline: python -m scripts.run_daily")


if __name__ == "__main__":
    main()