# Quickstart

Read one of the 18 corpus files and validate it against the schema:

```python
import os, pisces_sff
from pisces_sff import validate_json_against_schema, validate_flowsheet_against_SFF

pkg = os.path.dirname(pisces_sff.__file__)
schema = os.path.join(pkg, "schema", "sff_schema.json")
sff = os.path.join(pkg, "exported_flowsheets", "bioindustrial_park",
                   "corn_dry_grind_ethanol.json")

# Layer 1: structural JSON-Schema gate
is_valid, errors = validate_json_against_schema(sff, schema)
print("schema valid:", is_valid, "| errors:", errors[:2])

# Layer 2: schema gate + every semantic check from sff_checks.md
ok, results = validate_flowsheet_against_SFF(sff)
print("SFF valid:", ok)
for r in results:
    if r.status != "pass":
        print(r.check_id, r.severity, r.status, r.path)
```

`corn_dry_grind_ethanol.json` is the one corpus file in the current 0.1.3 shape;
it validates clean (benign `info`/`skip` findings only).
