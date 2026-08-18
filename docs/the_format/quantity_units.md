# The quantity-units model

Since v0.0.7, physical quantities in an SFF document are **bare numbers** —
a mass flow, a temperature, a pressure is written as a plain number, with the
unit it is expressed in resolved through a registry rather than carried
alongside the value.

## The global registry

The top-level `quantity_units_global` registry maps each canonical quantity
name to an object with `aliases` and `quantity_units`, where `quantity_units`
gives the unit as a BioSTEAM-native unit string. For example, the quantity
`temperature` maps to the unit string `"K"`. Any bare number in the document
tagged as a `temperature` quantity is understood to be in the unit this
registry entry declares.

## Per-unit design results

Because different units can report design results in different units, each
unit also carries its own `quantity_units_for_design_results` map. This map
is self-contained: it resolves the unit string for that particular unit's
design-result fields directly, on its own.

## The legacy inline shape

Before v0.0.7, quantities were written inline as an object pairing a value
with its unit — `{"value": <number>, "units": "<str>"}` — rather than as a
bare number resolved through a registry. This legacy shape is only emitted by
exporters at version 0.0.6 or earlier; from v0.0.7 onward, every exporter
emits bare numbers plus the registries described above.
