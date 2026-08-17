# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1 coverage meta-test (Task 3.1). Enumerates every module-level `def` in
# the source modules listed in MODULES via `ast` (import-light: no `import
# pisces_sff`), plus every non-dunder method of a module-level class, subtracts
# the explicit `_EXEMPT` set, and asserts each remaining helper name appears as
# a substring somewhere in the combined source of tests/tier1/test_*.py. This is
# the authoritative gap list drawn on in Task 3.1's Step 3 to add the missing
# fake-object tests.
#
# Scan boundary (known and deliberate): the walk covers module-level functions
# and the direct methods of module-level classes -- the two places helpers
# actually live in pisces_sff. It does NOT descend into nested/closure `def`s
# (never part of a module's tested surface) and it keys everything by bare name,
# so a future untested helper sharing a name with a tested/exempt one elsewhere
# would pass silently (see _EXEMPT's "main" note). Class methods are included
# precisely because _Context.molar_mass once escaped a module-level-only scan.

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "pisces_sff"
TIER1 = Path(__file__).resolve().parent

MODULES = ["_export.py", "_validate.py", "_quantity_units.py", "_harness.py",
           "_runner.py", "_version.py", "_regenerate_corpus.py", "exceptions.py"]

# name -> reason it needs no dedicated Tier 1 test.
_EXEMPT = {
    "export_biosteam_flowsheet": "public dispatcher; covered by version_sync + tier6",

    # -- _export.py: versioned exporters + the shared document assembler they
    # delegate to. Each needs a REAL, simulated biosteam System (sys.flowsheet,
    # sys.units, sys.streams, sys.feeds, sys.products, sys.TEA) to produce a
    # meaningful document; a fake object would only prove attribute access
    # works, not that a conforming document is produced. Already exercised
    # against real objects in tests/tier2/test_version_shape_guard.py (every
    # version 0.0.5-0.1.0) and end-to-end in tests/tier3. (_write_sff_json is
    # NOT here -- it is pure I/O and gets a real Tier 1 test below.)
    "export_biosteam_flowsheet_sff_0_0_5": "real-only; needs a simulated System, covered in Tier 2/3",
    "export_biosteam_flowsheet_sff_0_0_6": "real-only; needs a simulated System, covered in Tier 2/3",
    "export_biosteam_flowsheet_sff_0_0_7": "real-only; needs a simulated System, covered in Tier 2/3",
    "export_biosteam_flowsheet_sff_0_0_8": "real-only; needs a simulated System, covered in Tier 2/3",
    "export_biosteam_flowsheet_sff_0_0_9": "real-only; needs a simulated System, covered in Tier 2/3",
    "export_biosteam_flowsheet_sff_0_0_10": "real-only; needs a simulated System, covered in Tier 2/3",
    "export_biosteam_flowsheet_sff_0_0_11": "real-only; needs a simulated System, covered in Tier 2/3",
    "export_biosteam_flowsheet_sff_0_0_12": "real-only; needs a simulated System, covered in Tier 2/3",
    "export_biosteam_flowsheet_sff_0_1_0": "real-only; needs a simulated System, covered in Tier 2/3",
    "export_biosteam_flowsheet_sff_0_1_1": "real-only; needs a simulated System, covered in Tier 2/3",
    "export_biosteam_flowsheet_sff_0_1_2": "real-only; needs a simulated System, covered in Tier 2/3",
    "_build_sff_dict": "real-only; needs a simulated System (sys.flowsheet/units/streams/feeds/products/TEA), covered in Tier 2/3",

    # -- _runner.py: the child process of the full reproducible-export harness.
    # load_extended_metadata and build_reproducibility need no real simulator
    # (pure YAML/file I/O) and are exercised directly in
    # tests/tier1/test_extended_metadata.py; load_model_module and the small
    # I/O helpers (_file_record, _installed_versions) are exercised directly
    # in that same file as part of this task. run_model_export and main() are
    # different in kind: they call a model's load() and simulate a real
    # System, which a fake object cannot stand in for meaningfully. Per this
    # task's brief resolution, no test_runner_helpers.py is created; these two
    # are exempted rather than faked.
    "run_model_export": "real-only; calls module.load() and simulates a real System, covered in Tier 6",
    "main": "real-only CLI entry point (both _runner.py's and _regenerate_corpus.py's -- _EXEMPT is keyed by name only) delegating to a real export/harness call, covered in Tier 6",

    # -- _validate.py: QU-02 and UTIL-03's *substantive* parseable/unparseable
    # assertions, and _unit_is_parseable itself, need the REAL
    # thermosteam.units_of_measure pint registry. That is more than merely
    # slow to import here: tests/_fakes.py's Tier-1 biosteam/thermosteam stub
    # (installed by test_export_helpers.py, which runs first alphabetically in
    # `pytest tests/tier1`) poisons `_unit_is_parseable`'s lazy `from
    # thermosteam.units_of_measure import ureg` for the rest of the pytest
    # process -- verified empirically: a real, valid unit string such as
    # 'kg/hr' comes back reported as unparseable once the stub is resident.
    # That is exactly the failure mode tests/_stub_eviction.py and
    # tests/tier4/test_stub_eviction.py exist to document and guard against
    # for the tiers that run the real validator; reproducing that dance inside
    # Tier 1 would blur the fake/real tier boundary Tier 1 exists to keep
    # clean. (By contrast, _molar_mass_from_formula and the CHEM-03/STR-10
    # checks built on it only need the lightweight, non-biosteam `chemicals`
    # library -- confirmed importable standalone in ~0.1s without touching
    # sys.modules['thermosteam']/['biosteam'] -- so those get real Tier 1
    # tests below rather than an exemption; likewise QU-02/UTIL-03's
    # documented vacuous-pass-on-no-input path never reaches
    # _unit_is_parseable, so that specific behavior is tested for real too.)
    # All of the substantive real-unit-string behavior is already
    # comprehensively covered, with the stub correctly evicted first, in
    # tests/tier2/test_validate_helpers_real.py (TestUnitParseable,
    # TestQuantityUnitStringsParseable, TestUtilityResultUnitsParseable).
    "_unit_is_parseable": "real-only; needs real thermosteam.units_of_measure (poisoned by the Tier-1 biosteam stub -- see tests/_stub_eviction.py), covered in Tier 2 test_validate_helpers_real.py (TestUnitParseable)",
}


def _is_dunder(name):
    return name.startswith("__") and name.endswith("__")


def source_helpers():
    names = set()
    for m in MODULES:
        tree = ast.parse((SRC / m).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                names.add(node.name)
            elif isinstance(node, ast.ClassDef):
                # Direct methods of a module-level class are helpers too; the
                # module-level-only scan missed _Context.molar_mass. Dunders are
                # framework hooks, not independently-tested helpers, so skip them.
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef) and not _is_dunder(sub.name):
                        names.add(sub.name)
    return names


def tier1_text():
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in TIER1.glob("test_*.py"))


class TestTier1Coverage(unittest.TestCase):
    def test_every_helper_has_a_tier1_test(self):
        """Every module-level helper AND non-dunder class method in
        pisces_sff/*.py (minus _EXEMPT) is named in some tests/tier1/test_*.py
        -> expected: empty 'missing' set."""
        text = tier1_text()
        missing = sorted(n for n in source_helpers()
                         if n not in _EXEMPT and n not in text)
        self.assertEqual(missing, [], f"helpers with no Tier 1 test: {missing}")


if __name__ == "__main__":
    unittest.main()
