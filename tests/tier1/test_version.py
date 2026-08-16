# -*- coding: utf-8 -*-
# Tests that the SFF version is stated in exactly one place: the schema file.
#
# Two links are pinned here:
#   1. pisces_sff.__version__ is read from sff_schema.json's "version" field,
#      so the package version cannot drift from the spec it describes.
#   2. Each versioned exporter in _export.py encodes its version in its own
#      name, and reports that same version as its `sff_version` default. The
#      dispatcher resolves a requested version to a function by that name, and
#      the exporter writes the requested version into metadata.sff_version --
#      so a name/default mismatch would silently mislabel exported flowsheets
#      (exactly the failure that made every export report '0.0.3').
#
# Design notes:
#   * As in tests/tier1/test_schema_microorganisms.py, these tests stay import-light:
#     importing the `pisces_sff` package would pull in the heavy optional
#     biosteam/thermosteam stack via _export. We therefore load _version.py
#     directly by file path (bypassing pisces_sff/__init__.py), and inspect
#     _export.py and __init__.py with `ast` instead of importing them. That
#     keeps these tests runnable anywhere jsonschema/stdlib are available.

import ast
import importlib.util
import json
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "pisces_sff"
SCHEMA_PATH = PACKAGE_ROOT / "schema" / "sff_schema.json"
VERSION_PATH = PACKAGE_ROOT / "_version.py"
EXPORT_PATH = PACKAGE_ROOT / "_export.py"
INIT_PATH = PACKAGE_ROOT / "__init__.py"

# Mirrors _EXPORTER_PREFIX in _export.py. Duplicated rather than imported
# because importing _export would drag in biosteam; test_prefix_is_unchanged
# below fails if the two ever diverge.
EXPORTER_PREFIX = "export_biosteam_flowsheet_sff_"

# The versioned exporters delegate document assembly to this shared builder,
# which is where metadata['sff_version'] is assigned. Named here so the
# "no hardcoded version" check below follows the assignment instead of passing
# vacuously once the exporters became thin wrappers.
BUILDER_NAME = "_build_sff_dict"


def load_version_module():
    """Load pisces_sff/_version.py without executing the package __init__."""
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_version_under_test", VERSION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def schema_version():
    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)["version"]


def parse(path):
    with path.open("r", encoding="utf-8") as f:
        return ast.parse(f.read())


def versioned_exporters():
    """Map version string -> ast.FunctionDef for each versioned exporter."""
    exporters = {}
    for node in parse(EXPORT_PATH).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith(EXPORTER_PREFIX):
            version = node.name[len(EXPORTER_PREFIX):].replace("_", ".")
            exporters[version] = node
    return exporters


def metadata_writers():
    """Map name -> ast.FunctionDef for every function that may build metadata."""
    writers = {
        f"{EXPORTER_PREFIX}{v.replace('.', '_')}": node
        for v, node in versioned_exporters().items()
    }
    for node in parse(EXPORT_PATH).body:
        if isinstance(node, ast.FunctionDef) and node.name == BUILDER_NAME:
            writers[BUILDER_NAME] = node
    return writers


def default_of(func_node, param_name):
    """Return the literal default of a parameter, or raise if it has none."""
    args = func_node.args
    # Defaults align with the tail of the positional args; keyword-only args
    # carry their defaults positionally alongside them (None where absent).
    positional = args.args[len(args.args) - len(args.defaults):]
    pairs = list(zip(positional, args.defaults))
    pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
    for arg, default in pairs:
        if arg.arg == param_name:
            return ast.literal_eval(default)
    raise AssertionError(
        f"{func_node.name} has no default for parameter {param_name!r}"
    )


class TestPackageVersionFollowsSchema(unittest.TestCase):
    """__version__ is derived from the schema, not restated alongside it."""

    def test_read_schema_version_returns_the_schema_field(self):
        self.assertEqual(load_version_module().read_schema_version(), schema_version())

    def test_default_schema_file_is_the_committed_schema(self):
        # read_schema_version() with no argument must read the schema shipped in
        # this package -- that call is what sets __version__.
        self.assertEqual(load_version_module().SCHEMA_FILE.resolve(), SCHEMA_PATH)

    def test_missing_version_field_is_reported_clearly(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "no_version.json"
            bad.write_text(json.dumps({"title": "SFF"}), encoding="utf-8")
            with self.assertRaises(KeyError):
                load_version_module().read_schema_version(bad)

    def test_init_computes_version_instead_of_hardcoding_it(self):
        # A string literal here would reintroduce the drift this change removes.
        assignments = [
            node
            for node in ast.walk(parse(INIT_PATH))
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "__version__"
                for t in node.targets
            )
        ]
        self.assertEqual(len(assignments), 1, "__version__ must be assigned exactly once")
        self.assertIsInstance(
            assignments[0].value,
            ast.Call,
            "__version__ must be computed from the schema, not a hardcoded literal",
        )


class TestVersionedExporterNaming(unittest.TestCase):
    """Exporter names are the dispatch mechanism, so they must stay truthful."""

    def setUp(self):
        self.exporters = versioned_exporters()

    def test_prefix_is_unchanged(self):
        # get_versioned_exporter builds the function name from this prefix; if it
        # is renamed in _export.py, the constant above (and this suite) is stale.
        source = EXPORT_PATH.read_text(encoding="utf-8")
        self.assertIn(f"_EXPORTER_PREFIX = '{EXPORTER_PREFIX}'", source)

    def test_at_least_one_exporter_exists(self):
        self.assertTrue(self.exporters, "no versioned exporter functions found")

    def test_current_schema_version_has_an_exporter(self):
        # Part 2 of the version-bump protocol: a schema bump requires a new
        # export_biosteam_flowsheet_sff_<M>_<m>_<p> function, or the dispatcher
        # cannot export against the current schema at all.
        self.assertIn(
            schema_version(),
            self.exporters,
            f"schema declares version {schema_version()!r} but no exporter "
            f"named {EXPORTER_PREFIX}{schema_version().replace('.', '_')} exists",
        )

    def test_each_exporter_defaults_to_the_version_in_its_name(self):
        # The dispatcher always passes sff_version explicitly, so this default
        # only applies to direct calls -- but a wrong default there mislabels
        # exports just as badly, and copy-pasting a function for a new version
        # is exactly how that happens.
        for version, node in self.exporters.items():
            with self.subTest(version=version):
                self.assertEqual(default_of(node, "sff_version"), version)

    def test_shared_builder_exists(self):
        # If the builder is renamed or inlined, the check below silently stops
        # inspecting the code that actually assigns metadata['sff_version'].
        self.assertIn(BUILDER_NAME, metadata_writers())

    def test_no_exporter_hardcodes_a_version_into_metadata(self):
        # metadata['sff_version'] must come from the sff_version parameter.
        found = 0
        for name, node in metadata_writers().items():
            for sub in ast.walk(node):
                if not (isinstance(sub, ast.Assign) and len(sub.targets) == 1):
                    continue
                target = sub.targets[0]
                if not isinstance(target, ast.Subscript):
                    continue
                if not (
                    isinstance(target.value, ast.Name)
                    and target.value.id == "metadata"
                ):
                    continue
                # Version-agnostic unwrap: on Python < 3.9, target.slice is an
                # ast.Index wrapper around the real key node; on 3.9+ it is
                # already the bare ast.Constant. The old
                # `getattr(slice, "value", slice)` idiom silently unwrapped the
                # 3.9+ Constant to a plain `str` and then discarded it (the
                # following isinstance(..., ast.Constant) check is False for a
                # str), so this check matched nothing and passed vacuously.
                slice_node = target.slice
                if slice_node.__class__.__name__ == "Index":  # Python < 3.9 wrapper
                    slice_node = slice_node.value
                key = slice_node.value if isinstance(slice_node, ast.Constant) else None
                if key != "sff_version":
                    continue
                found += 1
                with self.subTest(function=name):
                    self.assertIsInstance(
                        sub.value,
                        ast.Name,
                        "metadata['sff_version'] must be assigned from the "
                        "sff_version argument, not a literal",
                    )
                    self.assertEqual(sub.value.id, "sff_version")
        self.assertEqual(
            found, 1, "metadata['sff_version'] must be assigned in exactly one place"
        )


if __name__ == "__main__":
    unittest.main()
