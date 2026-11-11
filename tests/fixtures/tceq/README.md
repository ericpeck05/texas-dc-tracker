# Real TCEQ permit fixtures

For each sample permit, add two files:

- `<name>.pdf`: the permit as downloaded from TCEQ
- `<name>.expected.json`: what you counted by hand, for example:

```json
{
  "rn": "RN112345678",
  "permit_number": "176543",
  "county": "Taylor",
  "equipment": [
    {"category": "diesel_generator", "count": 40, "unit_size": 2500, "unit": "kW",
     "manufacturer": "Caterpillar", "model": "3516C"}
  ]
}
```

Categories: diesel_generator, gas_generator, generator (fuel not stated), gas_turbine,
fuel_tank, other. Units: kW, MW, hp, gal. Leave out fields the permit doesn't state.

The equipment list is compared exactly: an extra row fails the test just like a missing one.
Use `"equipment": []` for a document that names equipment but gives no counts (for example a
Title V Statement of Basis). You can also pin `document_kind`, `site_name`, `applicant`,
`nsr_authorizations`, `permits_by_rule`, and `unit_groups`; see
`longhorn_o4721_statement_of_basis.expected.json`.

Permits that name private individuals (applicant contacts, signers) go in `local/`, which is
git-ignored. They run in `pytest` on your machine and are skipped in CI. `checked_by_hand`
records whether a person has compared the expected file against the PDF.
