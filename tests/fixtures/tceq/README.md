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
