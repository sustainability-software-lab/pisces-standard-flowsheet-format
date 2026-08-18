# -*- coding: utf-8 -*-
# Sphinx configuration for the Standard Flowsheet Format (SFF) documentation.
import json
import os
import shutil
import sys

# Put the repo root on sys.path so `import pisces_sff` works without packaging
# metadata (there is no pyproject.toml/setup.py). autodoc imports the package
# with biosteam/thermosteam mocked (see autodoc_mock_imports below).
sys.path.insert(0, os.path.abspath(".."))

# Read the version straight from the schema -- the single source of truth --
# rather than importing pisces_sff (which would import biosteam at conf time).
_schema_path = os.path.join(
    os.path.dirname(__file__), "..", "pisces_sff", "schema", "sff_schema.json"
)
with open(_schema_path, "r", encoding="utf-8") as _f:
    release = str(json.load(_f)["version"])
version = release

# --- "Visualize Schema" page: ship the exact committed spec -----------------
# Copy the schema next to the pre-built jsoncrack bundle so the page's JS can
# fetch() it at view time. The copy is .gitignore'd (a build product); the
# committed schema file stays the single source of truth -- nobody ever
# hand-updates the visualized JSON. Same discipline as __version__ above.
_viz_static_dir = os.path.join(os.path.dirname(__file__), "_static", "jsoncrack")
os.makedirs(_viz_static_dir, exist_ok=True)
shutil.copyfile(_schema_path, os.path.join(_viz_static_dir, "sff_schema.json"))

project = "Standard Flowsheet Format (SFF)"
author = "Sarang S. Bhagwat and the Project PISCES contributors"
copyright = "2025-, Sarang S. Bhagwat"

extensions = [
    "myst_nb",                    # MyST Markdown + notebook rendering (pulls myst_parser)
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "numpydoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx-jsonschema",  # NOTE: upstream's installed package dir is hyphenated
                          # ("sphinx-jsonschema", not "sphinx_jsonschema"), and
                          # only the hyphenated name is importable/documented.
]

# NOTE: `.rst` must map to the built-in RST parser so autosummary's generated
# stub pages (docs/generated/*.rst) render as reStructuredText. Without a
# `.rst` entry, autosummary emits its RST-templated stubs under the `.md`
# suffix, and the MyST parser then renders the `.. autoclass::`/`.. rubric::`
# directives as literal text instead of expanded docstrings.
source_suffix = {".rst": "restructuredtext", ".md": "myst-nb", ".ipynb": "myst-nb"}

exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "superpowers/**",
    "_viz_src/**",  # bundle build recipe, not a docs source (see its README)
]

# --- MyST / notebooks ---
myst_enable_extensions = ["colon_fence", "deflist"]
nb_execution_mode = "off"         # RTD renders committed outputs; never re-executes

# --- autodoc / autosummary / numpydoc ---
autosummary_generate = True
autodoc_mock_imports = ["biosteam", "thermosteam"]
autodoc_default_options = {"members": True, "show-inheritance": True}
numpydoc_show_class_members = False   # avoid autosummary/numpydoc double-listing warnings

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

# --- HTML theme (pydata, biosteam-style three-column) ---
html_theme = "pydata_sphinx_theme"
html_title = "Standard Flowsheet Format"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_theme_options = {
    "github_url": "https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format",
    "navbar_end": ["theme-switcher", "navbar-icon-links"],
    "show_prev_next": False,
    "logo": {"text": "Standard Flowsheet Format"},
    # First five sections stay visible in the navbar; the rest (Validation &
    # Checks, API Reference, Extending SFF, Contributions) collapse into the
    # theme's "More" dropdown.
    "header_links_before_dropdown": 5,
}
