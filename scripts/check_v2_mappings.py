"""Quick verification of v2_policy_mappings.csv parsing + event breakdown."""

import csv
from data.schema import get_session, PolicyEvent
from sqlalchemy import func

print("=" * 60)
print("V2 MAPPINGS CSV")
print("=" * 60)

with open("metadata/v2_policy_mappings.csv") as f:
    rows = list(csv.DictReader(f))

print(f"Loaded {len(rows)} rows total")
print()
for r in rows:
    print(f"{r['subtype']:<32} -> {r['affected_stocks']:<40} @ {r['magnitude_override']}")

print()
print("=" * 60)
print("EVENTS IN DB BY SUBTYPE")
print("=" * 60)

session = get_session()
event_counts = (
    session.query(PolicyEvent.subtype, func.count(PolicyEvent.id))
    .group_by(PolicyEvent.subtype)
    .all()
)

if not event_counts:
    print("No policy events in DB.")
else:
    print(f"{'Subtype':<32} {'Count':>5}")
    print("-" * 40)
    for subtype, count in sorted(event_counts, key=lambda x: -x[1]):
        print(f"{subtype:<32} {count:>5}")

print()
print("=" * 60)
print("MAPPING COVERAGE CHECK")
print("=" * 60)

mapped_subtypes = {r["subtype"] for r in rows}
event_subtypes = {st for st, _ in event_counts}

mapped_with_events = mapped_subtypes & event_subtypes
mapped_without_events = mapped_subtypes - event_subtypes
events_without_explicit_mapping = event_subtypes - mapped_subtypes

print(f"\nMode A active (mapped subtype with events): {len(mapped_with_events)}")
for st in sorted(mapped_with_events):
    print(f"  - {st}")

print(f"\nMode A configured but no events yet: {len(mapped_without_events)}")
for st in sorted(mapped_without_events):
    print(f"  - {st}")

print(f"\nEvents falling to Mode B/C (no explicit mapping): {len(events_without_explicit_mapping)}")
for st in sorted(events_without_explicit_mapping):
    print(f"  - {st}")