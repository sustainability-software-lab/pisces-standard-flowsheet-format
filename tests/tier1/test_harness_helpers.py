# -*- coding: utf-8 -*-
# Tests for the pure half of pisces_sff/_harness.py.
#
# Two invariants matter here and both are silent when broken:
#
#   1. The environment key is the environment's identity. Two models with the
#      same dependencies must share one environment (so the YAML is proven by
#      being used, not merely declared), and any change to a dependency must
#      fork a new one. Cosmetic edits -- renaming the env, reordering keys --
#      must not fork, or every edit strands a stale environment.
#   2. Package records are derived from the environment specification rather
#      than restated by hand, so metadata.reproducibility cannot disagree with
#      the environment the export actually ran in. That derivation is this
#      parser.
#
# Design notes:
#   * _harness.py is loaded by file path rather than via `import pisces_sff`,
#     which would execute the package __init__ and pull in biosteam. _harness
#     itself imports only the standard library and PyYAML, so this works.

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = REPO_ROOT / "pisces_sff" / "_harness.py"
CORN_ENV = (
    REPO_ROOT
    / "pisces_sff"
    / "models"
    / "biosteam_models"
    / "corn_dry_grind_ethanol"
    / "environment.yaml"
)


def load_harness():
    spec = importlib.util.spec_from_file_location("pisces_sff_harness_under_test", HARNESS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE_YAML = """\
name: sff-example
channels:
  - defaults
dependencies:
  - python=3.9.25
  - pip
  - pip:
      - numpy==1.26.4
      - biosteam @ git+https://github.com/BioSTEAMDevelopmentGroup/biosteam@e2d3942dd1076a4516efc91ae194f9e558428551
"""


class TestEnvironmentKey(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()

    def test_key_is_a_sha256_hex_digest(self):
        """environment_key(BASE_YAML) is a 64-char lowercase-hex string."""
        key = self.harness.environment_key(BASE_YAML)
        self.assertEqual(len(key), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in key))

    def test_key_is_deterministic(self):
        """environment_key called twice on the same text returns the same key."""
        self.assertEqual(
            self.harness.environment_key(BASE_YAML),
            self.harness.environment_key(BASE_YAML),
        )

    def test_key_ignores_the_environment_name(self):
        """Renaming the 'name:' field does not change the environment_key."""
        renamed = BASE_YAML.replace("name: sff-example", "name: something-else")
        self.assertEqual(
            self.harness.environment_key(BASE_YAML),
            self.harness.environment_key(renamed),
        )

    def test_key_ignores_prefix(self):
        """Adding a 'prefix:' field does not change the environment_key."""
        with_prefix = BASE_YAML + "prefix: C:\\\\envs\\\\sff-example\n"
        self.assertEqual(
            self.harness.environment_key(BASE_YAML),
            self.harness.environment_key(with_prefix),
        )

    def test_key_ignores_key_order(self):
        """Reordering the top-level YAML keys does not change the environment_key."""
        reordered = (
            "dependencies:\n"
            "  - python=3.9.25\n"
            "  - pip\n"
            "  - pip:\n"
            "      - numpy==1.26.4\n"
            "      - biosteam @ git+https://github.com/BioSTEAMDevelopmentGroup/biosteam"
            "@e2d3942dd1076a4516efc91ae194f9e558428551\n"
            "channels:\n"
            "  - defaults\n"
            "name: sff-example\n"
        )
        self.assertEqual(
            self.harness.environment_key(BASE_YAML),
            self.harness.environment_key(reordered),
        )

    def test_key_changes_when_a_pin_changes(self):
        """Bumping the numpy version pin changes the environment_key."""
        bumped = BASE_YAML.replace("numpy==1.26.4", "numpy==1.26.5")
        self.assertNotEqual(
            self.harness.environment_key(BASE_YAML),
            self.harness.environment_key(bumped),
        )

    def test_key_changes_when_a_commit_changes(self):
        """Changing the pinned biosteam commit hash changes the environment_key."""
        bumped = BASE_YAML.replace(
            "e2d3942dd1076a4516efc91ae194f9e558428551", "0" * 40
        )
        self.assertNotEqual(
            self.harness.environment_key(BASE_YAML),
            self.harness.environment_key(bumped),
        )

    def test_environment_name_is_prefixed_and_short(self):
        """environment_name is ENV_NAME_PREFIX + the first 12 chars of the environment_key."""
        name = self.harness.environment_name(BASE_YAML)
        self.assertTrue(name.startswith(self.harness.ENV_NAME_PREFIX))
        self.assertEqual(name, "sff-" + self.harness.environment_key(BASE_YAML)[:12])


class TestPipRequirementParsing(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()

    def test_version_pin(self):
        """"numpy==1.26.4" parses to {name: numpy, version: 1.26.4}."""
        self.assertEqual(
            self.harness.parse_pip_requirement("numpy==1.26.4"),
            {"name": "numpy", "version": "1.26.4"},
        )

    def test_version_pin_tolerates_whitespace(self):
        """Extra whitespace around a version pin is stripped before parsing."""
        self.assertEqual(
            self.harness.parse_pip_requirement("  numpy == 1.26.4  "),
            {"name": "numpy", "version": "1.26.4"},
        )

    def test_pep508_direct_reference(self):
        """A "name @ git+URL@commit" PEP 508 reference parses to {name, url, commit}."""
        entry = (
            "biorefineries @ git+https://github.com/BioSTEAMDevelopmentGroup/"
            "Bioindustrial-Park@584232846c999986f108cbd14d53437cd06c8f3d"
        )
        self.assertEqual(
            self.harness.parse_pip_requirement(entry),
            {
                "name": "biorefineries",
                "url": "https://github.com/BioSTEAMDevelopmentGroup/Bioindustrial-Park",
                "commit": "584232846c999986f108cbd14d53437cd06c8f3d",
            },
        )

    def test_bare_git_url_falls_back_to_the_repository_name(self):
        """A bare "git+URL@commit" entry (no "name @ " prefix) derives the package name from the repo."""
        entry = (
            "git+https://github.com/BioSTEAMDevelopmentGroup/biosteam"
            "@e2d3942dd1076a4516efc91ae194f9e558428551"
        )
        self.assertEqual(
            self.harness.parse_pip_requirement(entry),
            {
                "name": "biosteam",
                "url": "https://github.com/BioSTEAMDevelopmentGroup/biosteam",
                "commit": "e2d3942dd1076a4516efc91ae194f9e558428551",
            },
        )

    def test_egg_fragment_names_the_distribution(self):
        """A "#egg=<name>" fragment supplies the distribution name and is stripped from the url."""
        entry = (
            "git+https://github.com/BioSTEAMDevelopmentGroup/Bioindustrial-Park"
            "@584232846c999986f108cbd14d53437cd06c8f3d#egg=biorefineries"
        )
        record = self.harness.parse_pip_requirement(entry)
        self.assertEqual(record["name"], "biorefineries")
        self.assertNotIn("#", record["url"])

    def test_directives_are_ignored(self):
        """pip CLI flags ("--no-deps", "--index-url ...") parse to None, not a package record."""
        self.assertIsNone(self.harness.parse_pip_requirement("--no-deps"))
        self.assertIsNone(self.harness.parse_pip_requirement("--index-url https://x"))

    def test_blank_lines_are_ignored(self):
        """A whitespace-only line parses to None."""
        self.assertIsNone(self.harness.parse_pip_requirement("   "))

    def test_pep508_reference_without_a_commit_is_unparseable(self):
        """A VCS reference with no pinned commit parses to None, not a partial {name, url} record."""
        # A VCS reference with no pinned commit is not a pin -- it resolves to
        # whatever the branch tip happens to be at install time. The parser
        # exists to produce pins, so this must come back None, not a partial
        # {'name', 'url'} record with neither 'commit' nor 'version'.
        entry = "somepkg @ git+https://github.com/org/repo"
        self.assertIsNone(self.harness.parse_pip_requirement(entry))

    def test_bare_git_url_without_a_commit_is_unparseable(self):
        """A bare git+URL entry with no pinned commit parses to None."""
        entry = "git+https://github.com/org/repo.git"
        self.assertIsNone(self.harness.parse_pip_requirement(entry))


class TestPackageRecord(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()

    def test_finds_a_version_pinned_package(self):
        """package_record(BASE_YAML, "numpy") returns its {name, version} pin."""
        self.assertEqual(
            self.harness.package_record(BASE_YAML, "numpy"),
            {"name": "numpy", "version": "1.26.4"},
        )

    def test_finds_a_commit_pinned_package(self):
        """package_record(BASE_YAML, "biosteam") returns its pinned commit and repo url."""
        record = self.harness.package_record(BASE_YAML, "biosteam")
        self.assertEqual(record["commit"], "e2d3942dd1076a4516efc91ae194f9e558428551")
        self.assertEqual(record["url"], "https://github.com/BioSTEAMDevelopmentGroup/biosteam")

    def test_branch_is_attached_when_given(self):
        """Passing branch="master" adds a "branch" key to the returned record."""
        record = self.harness.package_record(BASE_YAML, "biosteam", branch="master")
        self.assertEqual(record["branch"], "master")

    def test_name_matching_ignores_underscore_dash_and_case(self):
        """A requirement named "Free_Properties" is found when queried as "free-properties"."""
        yaml_text = BASE_YAML.replace("numpy==1.26.4", "Free_Properties==0.3.6")
        self.assertEqual(
            self.harness.package_record(yaml_text, "free-properties")["version"], "0.3.6"
        )

    def test_missing_package_raises(self):
        """Querying a package absent from the environment spec raises ValueError."""
        with self.assertRaises(ValueError):
            self.harness.package_record(BASE_YAML, "not-installed-anywhere")

    def test_commit_less_vcs_entry_raises(self):
        """A package whose only entry is an unpinned git+ reference raises ValueError naming the package."""
        # The only entry for this package is an unpinned git+ reference, which
        # parse_pip_requirement now rejects (returns None) -- so, from
        # package_record's point of view, the package is simply not found.
        # This must fail here, loudly, rather than pass through and only be
        # caught later by schema validation after a conda environment has
        # been built and a simulation run.
        yaml_text = BASE_YAML.replace(
            "biosteam @ git+https://github.com/BioSTEAMDevelopmentGroup/biosteam"
            "@e2d3942dd1076a4516efc91ae194f9e558428551",
            "biosteam @ git+https://github.com/BioSTEAMDevelopmentGroup/biosteam",
        )
        with self.assertRaises(ValueError) as ctx:
            self.harness.package_record(yaml_text, "biosteam")
        self.assertIn("biosteam", str(ctx.exception))


class TestCornEnvironmentSpecification(unittest.TestCase):
    """The committed corn recipe must be readable by this parser."""

    def setUp(self):
        self.harness = load_harness()
        self.text = CORN_ENV.read_text(encoding="utf-8")

    def test_simulator_package_is_commit_pinned(self):
        """The committed corn recipe's biosteam entry parses to its pinned commit."""
        record = self.harness.package_record(self.text, "biosteam")
        self.assertEqual(record["commit"], "e2d3942dd1076a4516efc91ae194f9e558428551")

    def test_flowsheet_model_package_is_commit_pinned(self):
        """The committed corn recipe's biorefineries entry parses to its pinned commit and repo url."""
        record = self.harness.package_record(self.text, "biorefineries")
        self.assertEqual(record["commit"], "584232846c999986f108cbd14d53437cd06c8f3d")
        self.assertEqual(
            record["url"],
            "https://github.com/BioSTEAMDevelopmentGroup/Bioindustrial-Park",
        )

    def test_every_pip_entry_is_parseable(self):
        """Every pip requirement listed in the committed corn recipe is parseable by parse_pip_requirement."""
        # An unparseable entry would be installed but absent from the recorded
        # provenance -- silent, and exactly what this catches.
        for entry in self.harness.pip_requirements(self.text):
            with self.subTest(entry=entry):
                self.assertIsNotNone(self.harness.parse_pip_requirement(entry))

    def test_runner_dependencies_are_pinned(self):
        """The corn recipe pins PyYAML and jsonschema, each with a "version" entry."""
        # The child process imports yaml (via _harness) and jsonschema (via
        # _validate); without these pins the export fails inside a freshly
        # created environment.
        for package in ("PyYAML", "jsonschema"):
            with self.subTest(package=package):
                self.assertIn("version", self.harness.package_record(self.text, package))


def fake_conda_exe(directory):
    """Create a file named like a conda executable, for find_conda_exe to accept.

    find_conda_exe refuses an explicitly-given path that does not exist (an
    explicit request must not silently fall back to some other conda), and the
    real `conda` is not on PATH in non-interactive shells here -- so tests hand
    it a real file whose name the fake runner can dispatch on.
    """
    path = Path(directory) / ("conda.exe" if os.name == "nt" else "conda")
    path.write_text("", encoding="utf-8")
    return str(path)


class FakeConda:
    """Records conda invocations and answers `conda env list --json`.

    Environment provisioning is the one deliberately conda-shaped part of the
    harness. Driving it through an injected runner keeps its decisions -- reuse
    an environment that already matches the key, tear down a partial one so a
    broken environment is never reused, honour recreate -- testable without
    spending minutes building real environments.
    """

    def __init__(self, existing=(), fail_create=False, root="C:\\envs"):
        self.existing = list(existing)
        self.fail_create = fail_create
        self.root = root
        self.calls = []
        self.kwargs = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        self.kwargs.append(dict(kwargs))
        if cmd[1:4] == ["env", "list", "--json"]:
            payload = json.dumps(
                {"envs": [self.root + "\\" + name for name in self.existing]}
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")
        if cmd[1:3] == ["env", "create"]:
            name = cmd[cmd.index("-n") + 1]
            if self.fail_create:
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(1, cmd)
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
            self.existing.append(name)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1:3] == ["env", "remove"]:
            name = cmd[cmd.index("-n") + 1]
            if name in self.existing:
                self.existing.remove(name)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def commands(self):
        return [c[1:4] for c in self.calls]


class TestEnsureEnvironment(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()
        self.tmp = tempfile.TemporaryDirectory()
        self.env_yaml = Path(self.tmp.name) / "environment.yaml"
        self.env_yaml.write_text(BASE_YAML, encoding="utf-8")
        self.name = self.harness.environment_name(BASE_YAML)
        self.conda_exe = fake_conda_exe(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_creates_the_environment_when_absent(self):
        """No matching environment exists -> ensure_environment issues "conda env create" and returns its prefix."""
        conda = FakeConda()
        prefix = self.harness.ensure_environment(
            self.env_yaml, conda_exe=self.conda_exe, run=conda
        )
        self.assertIn(["env", "create", "-n"], [c[1:4] for c in conda.calls])
        self.assertTrue(prefix.endswith(self.name))

    def test_reuses_an_existing_environment(self):
        """An environment already named for this key -> ensure_environment does not issue "conda env create"."""
        # The environment key is the reuse criterion; rebuilding a matching
        # environment would cost minutes on every export.
        conda = FakeConda(existing=[self.name])
        self.harness.ensure_environment(
            self.env_yaml, conda_exe=self.conda_exe, run=conda
        )
        self.assertNotIn(["env", "create", "-n"], [c[1:4] for c in conda.calls])

    def test_recreate_removes_then_creates(self):
        """recreate=True on an existing environment issues both "conda env remove" and "conda env create"."""
        conda = FakeConda(existing=[self.name])
        self.harness.ensure_environment(
            self.env_yaml, recreate=True, conda_exe=self.conda_exe, run=conda
        )
        commands = [c[1:3] for c in conda.calls]
        self.assertIn(["env", "remove"], commands)
        self.assertIn(["env", "create"], commands)

    def test_failed_creation_removes_the_partial_environment(self):
        """A failing "conda env create" raises and is followed by a "conda env remove" cleanup call."""
        # A half-built environment matches the content hash, so without this
        # teardown it would be reused -- broken -- forever after.
        conda = FakeConda(fail_create=True)
        with self.assertRaises(Exception):
            self.harness.ensure_environment(
                self.env_yaml, conda_exe=self.conda_exe, run=conda
            )
        self.assertIn(["env", "remove"], [c[1:3] for c in conda.calls])

    def test_pip_dependency_resolution_is_disabled(self):
        """The "conda env create" call is made with env var PIP_NO_DEPS="1"."""
        # Bioindustrial-Park declares biosteam>=2.53.0; with resolution on, pip
        # replaces the pinned biosteam commit and every pin below it becomes
        # fiction. --no-deps cannot be written into the pip: block (pip's
        # requirements-file parser rejects it as an unknown option), so it is
        # applied as the PIP_NO_DEPS environment variable instead.
        conda = FakeConda()
        self.harness.ensure_environment(
            self.env_yaml, conda_exe=self.conda_exe, run=conda
        )
        for cmd, kwargs in zip(conda.calls, conda.kwargs):
            if cmd[1:3] == ["env", "create"]:
                self.assertEqual((kwargs.get("env") or {}).get("PIP_NO_DEPS"), "1")
                break
        else:
            self.fail("conda env create was never invoked")

    def test_an_explicit_missing_conda_is_reported_rather_than_replaced(self):
        """An explicit conda_exe path that does not exist raises FileNotFoundError mentioning conda."""
        # Falling back to a different conda than the one asked for would build
        # the environment somewhere the caller did not expect.
        with self.assertRaises(FileNotFoundError) as caught:
            self.harness.find_conda_exe(str(Path(self.tmp.name) / "absent" / "conda.exe"))
        self.assertIn("conda", str(caught.exception).lower())

    def test_conda_is_discovered_without_an_explicit_path(self):
        """find_conda_exe() with no argument resolves to a real, existing file on this machine."""
        # conda is routinely absent from PATH in non-interactive shells even
        # where it is installed; discovery must not depend on PATH alone.
        self.assertTrue(Path(self.harness.find_conda_exe()).exists())


class TestExportLock(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()

    def test_lock_is_released_after_use(self):
        """LOCK_PATH exists while the export_lock context is held, and is gone after it exits normally."""
        with self.harness.export_lock():
            self.assertTrue(self.harness.LOCK_PATH.exists())
        self.assertFalse(self.harness.LOCK_PATH.exists())

    def test_second_lock_is_refused(self):
        """Acquiring export_lock while it is already held raises RuntimeError."""
        # Two concurrent simulations corrupt the shared numba cache, so this is
        # enforced rather than left to the caller's discipline.
        with self.harness.export_lock():
            with self.assertRaises(RuntimeError):
                with self.harness.export_lock():
                    pass

    def test_lock_is_released_after_an_error(self):
        """A ValueError raised inside the export_lock context still propagates, and LOCK_PATH is removed."""
        with self.assertRaises(ValueError):
            with self.harness.export_lock():
                raise ValueError("boom")
        self.assertFalse(self.harness.LOCK_PATH.exists())


class TestExportModelInvocation(unittest.TestCase):
    """export_model must launch the child in the provisioned environment."""

    def setUp(self):
        self.harness = load_harness()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.model_dir = Path(self.tmp.name) / "some_model"
        self.model_dir.mkdir()
        (self.model_dir / "environment.yaml").write_text(BASE_YAML, encoding="utf-8")
        (self.model_dir / "load.py").write_text("def load():\n    pass\n", encoding="utf-8")
        self.output = Path(self.tmp.name) / "out" / "some_model.json"
        self.conda_exe = fake_conda_exe(self.tmp.name)
        self.recorded = {}

        def fake_run(cmd, **kwargs):
            if Path(cmd[0]).name.startswith("conda"):
                return FakeConda(existing=[self.harness.environment_name(BASE_YAML)])(
                    cmd, **kwargs
                )
            self.recorded["cmd"] = list(cmd)
            self.recorded["env"] = dict(kwargs.get("env") or {})
            return subprocess.CompletedProcess(cmd, 0)

        self.fake_run = fake_run

    def test_child_runs_the_runner_module(self):
        """export_model launches the child as `python -m pisces_sff._runner <model_dir>`."""
        self.harness.export_model(
            self.model_dir, self.output, conda_exe=self.conda_exe, run=self.fake_run
        )
        cmd = self.recorded["cmd"]
        self.assertIn("-m", cmd)
        self.assertIn("pisces_sff._runner", cmd)
        self.assertIn(str(self.model_dir.resolve()), cmd)

    def test_child_python_comes_from_the_provisioned_environment(self):
        """The child command's python executable path includes the provisioned environment's name."""
        self.harness.export_model(
            self.model_dir, self.output, conda_exe=self.conda_exe, run=self.fake_run
        )
        self.assertIn(
            self.harness.environment_name(BASE_YAML), self.recorded["cmd"][0]
        )

    def test_child_pythonpath_is_only_the_repository_root(self):
        """The child process's PYTHONPATH is set to exactly REPO_ROOT."""
        # The reproducibility hole this harness closes: a user-level PYTHONPATH
        # of source clones silently shadows the pinned installs.
        self.harness.export_model(
            self.model_dir, self.output, conda_exe=self.conda_exe, run=self.fake_run
        )
        self.assertEqual(
            self.recorded["env"]["PYTHONPATH"], str(self.harness.REPO_ROOT)
        )

    def test_child_neutralizes_breakpoints(self):
        """The child process's PYTHONBREAKPOINT is set to "0"."""
        # _export.py has bare breakpoint() calls; in a TTY-less child they hang.
        self.harness.export_model(
            self.model_dir, self.output, conda_exe=self.conda_exe, run=self.fake_run
        )
        self.assertEqual(self.recorded["env"]["PYTHONBREAKPOINT"], "0")

    def test_child_ignores_conda_and_user_site_context(self):
        """CONDA_PREFIX/CONDA_DEFAULT_ENV are scrubbed from the child's env, and PYTHONNOUSERSITE is set to "1"."""
        # Seeded explicitly: these variables are often unset in a
        # non-interactive shell, so an unseeded assertion would pass without
        # proving anything was scrubbed.
        from unittest import mock

        with mock.patch.dict(
            os.environ,
            {"CONDA_PREFIX": "C:\\envs\\HP_2024", "CONDA_DEFAULT_ENV": "HP_2024"},
        ):
            self.harness.export_model(
                self.model_dir, self.output, conda_exe=self.conda_exe, run=self.fake_run
            )
        env = self.recorded["env"]
        self.assertNotIn("CONDA_PREFIX", env)
        self.assertNotIn("CONDA_DEFAULT_ENV", env)
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")

    def test_environment_key_is_passed_to_the_child(self):
        """The child command includes "--env-key" followed by the model's environment_key."""
        self.harness.export_model(
            self.model_dir, self.output, conda_exe=self.conda_exe, run=self.fake_run
        )
        cmd = self.recorded["cmd"]
        self.assertIn("--env-key", cmd)
        self.assertEqual(
            cmd[cmd.index("--env-key") + 1], self.harness.environment_key(BASE_YAML)
        )

    def test_nonzero_child_exit_raises(self):
        """A child process exiting with a nonzero return code raises RuntimeError."""
        def failing_run(cmd, **kwargs):
            if Path(cmd[0]).name.startswith("conda"):
                return FakeConda(existing=[self.harness.environment_name(BASE_YAML)])(
                    cmd, **kwargs
                )
            return subprocess.CompletedProcess(cmd, 3)

        with self.assertRaises(RuntimeError):
            self.harness.export_model(
                self.model_dir, self.output, conda_exe=self.conda_exe, run=failing_run
            )

    def test_missing_load_script_is_reported_before_any_work(self):
        """A model directory with no load.py raises FileNotFoundError before any child process is launched."""
        (self.model_dir / "load.py").unlink()
        with self.assertRaises(FileNotFoundError):
            self.harness.export_model(
                self.model_dir, self.output, conda_exe=self.conda_exe, run=self.fake_run
            )


class TestProducedInterfaceIsExported(unittest.TestCase):
    """Pins the "produced interface is exported" contract.

    Every name this task's brief lists under Produces must both exist on the
    loaded module and be listed in its __all__ -- otherwise `pisces_sff.X`
    (via `from ._harness import *`) silently raises AttributeError for X even
    though X is part of the harness's public interface. A future edit that
    adds a function but forgets __all__ should fail here, not go unnoticed.
    """

    def setUp(self):
        self.harness = load_harness()

    def test_produced_names_are_attributes_and_in_all(self):
        """Each brief-listed produced name is both an attribute of _harness and present in _harness.__all__."""
        for name in (
            "environment_python",
            "export_lock",
            "LOCK_PATH",
            "export_model",
            "ensure_environment",
        ):
            with self.subTest(name=name):
                self.assertTrue(
                    hasattr(self.harness, name), f"{name} is not an attribute of _harness"
                )
                self.assertIn(
                    name, self.harness.__all__, f"{name} is missing from _harness.__all__"
                )


class TestCanonicalEnvironmentTextHelper(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()

    def test_drops_name_and_prefix_and_sorts_keys(self):
        """canonical_environment_text drops 'name'/'prefix' and dumps the
        remaining mapping with keys sorted."""
        import yaml
        text = self.harness.canonical_environment_text(BASE_YAML)
        spec = yaml.safe_load(text)
        self.assertNotIn("name", spec)
        self.assertNotIn("prefix", spec)
        self.assertIn("dependencies", spec)
        self.assertEqual(
            text, yaml.safe_dump(spec, sort_keys=True, default_flow_style=False))

    def test_is_exactly_what_environment_key_hashes(self):
        """environment_key(text) is the sha256 hex digest of
        canonical_environment_text(text).encode('utf-8')."""
        import hashlib
        expected = hashlib.sha256(
            self.harness.canonical_environment_text(BASE_YAML).encode("utf-8")
        ).hexdigest()
        self.assertEqual(self.harness.environment_key(BASE_YAML), expected)


class TestSha256BytesHelper(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()

    def test_matches_hashlib_sha256(self):
        """sha256_bytes(data) equals hashlib.sha256(data).hexdigest()."""
        import hashlib
        data = b"pisces-sff"
        self.assertEqual(self.harness.sha256_bytes(data),
                         hashlib.sha256(data).hexdigest())

    def test_digest_is_64_lowercase_hex_characters(self):
        """The digest is always a 64-character lowercase hex string, even for
        empty input."""
        digest = self.harness.sha256_bytes(b"")
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in digest))


class TestNormalizedHelper(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()

    def test_lowercases_and_unifies_separators(self):
        """_normalized lowercases a distribution name and folds '_'/'.' to
        '-' (PEP 503-ish comparison key)."""
        self.assertEqual(self.harness._normalized("Free_Properties.Extra"),
                         "free-properties-extra")

    def test_matches_across_naming_variants(self):
        """Two spellings of the same distribution name normalize identically."""
        self.assertEqual(self.harness._normalized("free-properties"),
                         self.harness._normalized("Free_Properties"))


class TestVcsRecordHelper(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()

    def test_parses_a_commit_pinned_reference(self):
        """_vcs_record parses a 'git+URL@commit' reference into a
        {name, url, commit} record."""
        record = self.harness._vcs_record(
            "biosteam",
            "git+https://github.com/BioSTEAMDevelopmentGroup/biosteam@"
            "e2d3942dd1076a4516efc91ae194f9e558428551")
        self.assertEqual(record, {
            "name": "biosteam",
            "url": "https://github.com/BioSTEAMDevelopmentGroup/biosteam",
            "commit": "e2d3942dd1076a4516efc91ae194f9e558428551"})

    def test_non_git_reference_is_none(self):
        """A reference that does not start with 'git+' is not a VCS pin."""
        self.assertIsNone(self.harness._vcs_record("pkg", "https://example.com/pkg"))

    def test_missing_commit_is_none(self):
        """A git+ reference with no pinned commit is rejected outright, not
        partially parsed into a {name, url}-only record."""
        self.assertIsNone(
            self.harness._vcs_record("pkg", "git+https://github.com/org/repo"))


class TestEnvironmentPrefixHelper(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()

    def test_returns_the_matching_prefix(self):
        """_environment_prefix returns the prefix whose basename equals the
        requested environment name."""
        conda = FakeConda(existing=["sff-abc123"], root="C:\\envs")
        prefix = self.harness._environment_prefix("conda.exe", "sff-abc123", conda)
        self.assertEqual(prefix, "C:\\envs\\sff-abc123")

    def test_returns_none_when_no_environment_matches(self):
        """_environment_prefix returns None when no existing environment's
        basename matches the requested name."""
        conda = FakeConda(existing=["other-env"])
        self.assertIsNone(
            self.harness._environment_prefix("conda.exe", "sff-abc123", conda))


class TestSchemaVersionHelper(unittest.TestCase):
    def setUp(self):
        self.harness = load_harness()

    def test_matches_the_committed_schemas_version_field(self):
        """_harness's private _schema_version() -- a deliberate duplicate of
        _version.read_schema_version(), kept so _harness stays loadable by
        file path -- reads the same value as the committed schema file's
        top-level 'version' field, and DEFAULT_SFF_VERSION follows it."""
        import json
        schema_path = REPO_ROOT / "pisces_sff" / "schema" / "sff_schema.json"
        with schema_path.open("r", encoding="utf-8") as f:
            expected = json.load(f)["version"]
        self.assertEqual(self.harness._schema_version(), expected)
        self.assertEqual(self.harness.DEFAULT_SFF_VERSION, expected)


if __name__ == "__main__":
    unittest.main()
