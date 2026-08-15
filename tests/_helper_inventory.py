# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Machine-derived inventory of every module-level callable in ``pisces_sff``.

Derived by walking each module's AST rather than by importing it. Importing
would drag in biosteam (via ``_export``) and defeat Tier 1's import-light rule;
an AST walk gives the same answer for every module without caring what any of
them import, and works identically whether or not the simulator is installed.

The inventory is the required set for the Tier 1 and Tier 2 coverage meta-tests.
:data:`EXEMPT` subtracts from it, and every exemption must carry a reason -- that
is the mechanism by which a coverage hole cannot be opened silently.

Pure library: no assertions, no test classes.
"""

import ast
from pathlib import Path

#: Repository root (this file lives in ``tests/``).
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Directory holding the package under test.
PACKAGE_DIR = REPO_ROOT / 'pisces_sff'

#: Every module whose module-level callables are subject to tier coverage.
MODULES = ('_export', '_validate', '_harness', '_runner', '_quantity_units',
           '_version', '_regenerate_corpus', 'exceptions')

#: Explicit per-tier exemptions: ``(dotted_name, tier) -> reason``. Rule-based
#: exemptions (the ``_check_*`` family, D1) are added by :func:`_rule_exempt`
#: instead, because they are generated from the inventory and so cannot go stale.
EXEMPT = {
    ('pisces_sff._harness.ensure_environment', 2):
        'real conda environment creation takes minutes and needs network '
        'access; it would dominate the tier runtime. Tier 1 covers it with '
        'FakeConda; Tier 6 exercises it for real.',
    ('pisces_sff._harness.export_lock', 2):
        'a filesystem mutex -- a "real" exercise is indistinguishable from the '
        'Tier 1 one. Covered by Tier 1.',
    ('pisces_sff._harness.export_model', 2):
        'calls ensure_environment; cannot run without a provisioned '
        'environment. It is Tier 6\'s subject.',
    ('pisces_sff._runner.run_model_export', 2):
        'executes inside the child pinned environment, not the parent '
        'interpreter. Covered by Tier 6.',
    ('pisces_sff._runner.main', 2):
        'an argv/exit-code wrapper over run_model_export, which is itself '
        'Tier 6\'s. Covered by Tier 1.',
    ('pisces_sff._regenerate_corpus.main', 2):
        'an argv/exit-code wrapper over regenerate_corpus with no real-object '
        'surface of its own. Covered by Tier 1.',
}

#: Reason attached to the rule-based D1 exemptions.
_CHECK_REASON = (
    'a catalogue check: covered by Tier 4 end-to-end through '
    'validate_flowsheet_against_SFF, which also proves it is registered in '
    '_CHECKS. Testing it here as well would duplicate Tier 4.'
)


def module_callables(module_name):
    """
    Return the module-level callables defined in one ``pisces_sff`` module.

    Parameters
    ----------
    module_name : str
        Bare module name, e.g. ``'_export'``.

    Returns
    -------
    tuple of str
        Dotted names, e.g. ``('pisces_sff._export.export_biosteam_flowsheet',
        ...)``, in source order.
    """
    path = PACKAGE_DIR / f'{module_name}.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return tuple(f'pisces_sff.{module_name}.{node.name}'
                 for node in tree.body if isinstance(node, kinds))


def inventory():
    """
    Return every module-level callable across :data:`MODULES`.

    Returns
    -------
    tuple of str
        Sorted, deduplicated dotted names.
    """
    names = set()
    for module_name in MODULES:
        names.update(module_callables(module_name))
    return tuple(sorted(names))


def _rule_exempt(dotted_name, tier):
    """Reason a rule-based exemption applies to `dotted_name` at `tier`, else None."""
    if tier not in (1, 2):
        return None
    prefix, _, leaf = dotted_name.rpartition('.')
    if prefix != 'pisces_sff._validate':
        return None
    if leaf.startswith('_check_') or leaf == '_xref_gate':
        return _CHECK_REASON
    return None


def is_exempt(dotted_name, tier):
    """
    Return the reason `dotted_name` is exempt from `tier`, or ``None``.

    Parameters
    ----------
    dotted_name : str
    tier : int

    Returns
    -------
    str or None
    """
    explicit = EXEMPT.get((dotted_name, tier))
    if explicit is not None:
        return explicit
    return _rule_exempt(dotted_name, tier)


def required_for_tier(tier):
    """
    Return the callables a tier must cover: the inventory minus its exemptions.

    Parameters
    ----------
    tier : int

    Returns
    -------
    tuple of str
    """
    return tuple(name for name in inventory() if is_exempt(name, tier) is None)


def exemptions_for_tier(tier):
    """
    Return ``{dotted_name: reason}`` for everything exempt from `tier`.

    Parameters
    ----------
    tier : int

    Returns
    -------
    dict
    """
    return {name: is_exempt(name, tier)
            for name in inventory() if is_exempt(name, tier) is not None}
