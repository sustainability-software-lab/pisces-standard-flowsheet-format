# Contributing

## Authors & Acknowledgements

Authors of the PISCES Standard Flowsheet Format (SFF) include:

* [Sarang Bhagwat](https://github.com/sarangbhagwat) (lead SFF author, developer, and maintainer; also led the development of the BioSTEAM SFF export)
* [Corinne Scown](https://eta.lbl.gov/people/corinne-scown) (led efforts to found, develop, coordinate, and expand the software)
* [Tyler Huntington](https://github.com/tylerhuntington) (integrated the SFF with the [Project PISCES website](https://projectpisces.org/), helped build the documentation, and led the development of the SuperPro SFF export)
* [Yuting Chen](https://eta.lbl.gov/people/yuting-chen) (built the LLM-based [literature-to-SFF extraction pipeline](https://projectpisces.org/?page=extraction) and contributed general expertise through discussions)
* [Meili Gong](https://github.com/mglbleta) (contributed general expertise through discussions)

## Environment

All development runs in the `HP_2024` conda environment (Python 3.9). The
package has no packaging metadata — no `pyproject.toml` or `setup.py` — and is
instead resolved as a live editable clone, so changes to the source tree take
effect immediately without a reinstall step.

## Canonical validation

Before committing, run the canonical validation:

1. All corpus files validate against the schema.
2. The current-shape corpus file passes full SFF validation.
3. The test suite passes. The suite is organized into six responsibility-gated
   tiers; the fast routine path disables the two simulating tiers, leaving the
   other four to run quickly without needing a real simulation.

## Simulations

Simulations must run strictly sequentially, never concurrently, because they
share a numba on-disk cache — running two at once can corrupt it.

## Line endings

Line endings are pinned to LF via `.gitattributes`. This isn't just a style
preference: it's load-bearing, because reproducibility digests are computed
over recipe file bytes, and a CRLF checkout would change those bytes and
diverge the digests from a Linux/CI export.

## Regenerating tutorial notebooks

To regenerate a tutorial notebook, re-run it in `HP_2024` and re-commit it
with its outputs included. Read the Docs never re-executes notebooks at build
time, so the committed outputs are what readers see — stale outputs are the
accepted trade-off until the next re-run.
