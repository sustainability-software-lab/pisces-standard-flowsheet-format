import importlib.util
import json
import sys
import tempfile
import types
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_export_module(sff_version="0.0.5"):
    package = types.ModuleType("pisces_sff")
    package.__path__ = [str(ROOT / "pisces_sff")]

    version_module = types.ModuleType("pisces_sff._version")
    version_module.CURRENT_SFF_VERSION = sff_version

    thermosteam = types.ModuleType("thermosteam")
    thermosteam.Reaction = type("Reaction", (), {})
    thermosteam.ReactionSet = type("ReactionSet", (), {})
    thermosteam.SeriesReaction = type("SeriesReaction", (), {})
    thermosteam.ParallelReaction = type("ParallelReaction", (), {})
    thermosteam.Chemical = type("Chemical", (), {})

    reaction_package = types.ModuleType("thermosteam.reaction")
    reaction_module = types.ModuleType("thermosteam.reaction._reaction")
    reaction_module.get_stoichiometric_string = lambda *_args, **_kwargs: ""

    biosteam = types.ModuleType("biosteam")
    biosteam.PowerUtility = type("PowerUtility", (), {})
    biosteam.System = type("System", (), {})
    biosteam.__version__ = "test"

    numpy = types.ModuleType("numpy")
    numpy.generic = type("generic", (), {})
    numpy.ndarray = type("ndarray", (), {})

    modules = {
        "pisces_sff": package,
        "pisces_sff._version": version_module,
        "numpy": numpy,
        "thermosteam": thermosteam,
        "thermosteam.reaction": reaction_package,
        "thermosteam.reaction._reaction": reaction_module,
        "biosteam": biosteam,
    }
    spec = importlib.util.spec_from_file_location("pisces_sff._export", ROOT / "pisces_sff/_export.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class ExportVersionTest(unittest.TestCase):
    def test_v005_export_stamps_v005(self):
        export_module = load_export_module()
        chemical = SimpleNamespace(ID="Water", formula="H2O", CAS="7732-18-5", MW=18.015)
        stream = SimpleNamespace(
            ID="feed",
            source=None,
            sink=None,
            chemicals=[chemical],
            vle_chemicals=[chemical],
        )
        system = SimpleNamespace(
            flowsheet=SimpleNamespace(),
            units=[],
            streams=[stream],
            feeds=[],
            products=[],
            TEA=SimpleNamespace(duration=(2025, 2045)),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "export.json"
            export_module.export_biosteam_flowsheet(system, output, sff_version="0.0.5")
            document = json.loads(output.read_text())

        self.assertEqual(document["metadata"]["sff_version"], "0.0.5")

    def test_export_stamp_uses_central_version_authority(self):
        export_module = load_export_module(sff_version="9.9.9")
        chemical = SimpleNamespace(ID="Water", formula="H2O", CAS="7732-18-5", MW=18.015)
        stream = SimpleNamespace(
            ID="feed",
            source=None,
            sink=None,
            chemicals=[chemical],
            vle_chemicals=[chemical],
        )
        system = SimpleNamespace(
            flowsheet=SimpleNamespace(),
            units=[],
            streams=[stream],
            feeds=[],
            products=[],
            TEA=SimpleNamespace(duration=(2025, 2045)),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "export.json"
            export_module.export_biosteam_flowsheet_sff_0_0_5(system, output)
            document = json.loads(output.read_text())

        self.assertEqual(document["metadata"]["sff_version"], "9.9.9")

    def test_composition_reports_converged_fractions_without_rescaling(self):
        """Fractions are the converged values, divided by the phase total.

        The exporter reports what the solver converged on. It does not rescale a
        component set to make the listed fractions sum to 1, because that would
        restate a converged number and could propagate an inconsistency through
        the material and energy balance downstream. A negative flow is still
        omitted from the emitted list, since a negative fraction is not
        expressible, but it stays in the denominator where the solver put it.

        This test exists to keep that convention from being quietly reversed. It
        uses an exaggerated residual so the two possible conventions give
        different numbers: normalizing over the positive components alone would
        report 0.4 and 0.6 here.
        """
        export_module = load_export_module()
        chemicals = [
            SimpleNamespace(ID="Water"),
            SimpleNamespace(ID="Ethanol"),
            SimpleNamespace(ID="NumericalResidual"),
        ]
        phase = SimpleNamespace(
            imol={"Water": 2.0, "Ethanol": 3.0, "NumericalResidual": -1.0},
            imass={"Water": 36.0, "Ethanol": 138.0, "NumericalResidual": -10.0},
            F_mol=4.0,
            F_mass=164.0,
        )
        stream = SimpleNamespace(
            phases=("l",),
            chemicals=chemicals,
            __getitem__=lambda _self, _phase: phase,
        )
        stream = type("Stream", (), dict(vars(stream)))()

        composition = export_module.get_composition(stream)

        self.assertEqual([item["component_name"] for item in composition], ["Water", "Ethanol"])
        self.assertAlmostEqual(composition[0]["mol_fraction"], 2.0 / 4.0)
        self.assertAlmostEqual(composition[1]["mol_fraction"], 3.0 / 4.0)
        self.assertAlmostEqual(composition[0]["mass_fraction"], 36.0 / 164.0)
        self.assertAlmostEqual(composition[1]["mass_fraction"], 138.0 / 164.0)

    def test_json_default_converts_numpy_values_without_debugger_fallbacks(self):
        export_module = load_export_module()

        class Scalar(export_module.np.generic):
            def item(self):
                return 1.25

        class Array(export_module.np.ndarray):
            def tolist(self):
                return [1.0, 2.0]

        encoded = json.dumps(
            {"scalar": Scalar(), "array": Array(), "queue": deque(["a", "b"])},
            default=export_module._json_default,
        )

        self.assertEqual(
            json.loads(encoded),
            {"scalar": 1.25, "array": [1.0, 2.0], "queue": ["a", "b"]},
        )
        self.assertNotIn("breakpoint()", (ROOT / "pisces_sff/_export.py").read_text())

    def test_nonfinite_optional_results_are_omitted_and_json_is_strict(self):
        export_module = load_export_module()

        cleaned = export_module._finite_mapping(
            {"Known cost": 125.0, "Undefined cost": float("nan")}
        )

        self.assertEqual(cleaned, {"Known cost": 125.0})
        source = (ROOT / "pisces_sff/_export.py").read_text()
        self.assertIn("allow_nan=False", source)

    def test_top_level_reactions_are_deduplicated_in_attribute_order(self):
        export_module = load_export_module()
        parent = export_module.ReactionSet()
        child = export_module.Reaction()
        child._parent = parent
        standalone = export_module.Reaction()
        unit = SimpleNamespace(child=child, parent=parent, duplicate=parent, standalone=standalone)

        reactions = export_module._top_level_reactions(unit)

        self.assertEqual(reactions, [parent, standalone])


if __name__ == "__main__":
    unittest.main()
