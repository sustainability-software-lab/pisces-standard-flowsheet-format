# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Introspect a tier's test classes and report docstring-convention offences.

Spec §6: every test method carries a docstring in two labelled parts -- what the
test does, and an ``Expected:`` line stating the output -- and every test class
carries a docstring naming its subject. Enforcing that here makes the convention
mechanical instead of a review responsibility, which is the only way it survives
a suite that grows continuously.

Pure library: no assertions, no test classes.
"""

import importlib.util
import inspect
import sys
import unittest
from collections import namedtuple
from pathlib import Path

#: One convention violation.
Offence = namedtuple('Offence', 'module cls method reason')

#: The marker a method docstring must contain.
EXPECTED_MARKER = 'Expected:'


def _import_tier_module(tier_dir, path):
    """Import one test module by file path, reusing an existing sys.modules entry.

    Reuse matters: re-executing a Tier 1 module would reinstall the biosteam
    stubs and roughly double the tier's runtime for no benefit.
    """
    name = f'sff_tier_{tier_dir.name}_{path.stem}'
    if name in sys.modules:
        return sys.modules[name]
    for extra in (str(tier_dir), str(tier_dir.parent)):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def iter_test_classes(tier_dir):
    """
    Yield ``(module_name, class)`` for every ``TestCase`` defined in a tier.

    Classes imported *into* a module from elsewhere are skipped, so a shared base
    class is reported once, against the module that defines it.

    Parameters
    ----------
    tier_dir : str or Path
        A tier directory, e.g. ``tests/tier1``.

    Yields
    ------
    (str, type)
    """
    tier_dir = Path(tier_dir)
    for path in sorted(tier_dir.glob('test_*.py')):
        module = _import_tier_module(tier_dir, path)
        for name, obj in vars(module).items():
            if (inspect.isclass(obj) and issubclass(obj, unittest.TestCase)
                    and obj is not unittest.TestCase
                    and obj.__module__ == module.__name__):
                yield module.__name__, obj


def offences_in_class(module_name, cls):
    """
    Return the convention offences in one test class.

    Parameters
    ----------
    module_name : str
    cls : type
        A ``unittest.TestCase`` subclass.

    Returns
    -------
    tuple of Offence
    """
    found = []
    if not (cls.__doc__ or '').strip():
        found.append(Offence(module_name, cls.__name__, '',
                             'test class has no docstring'))
    for name in dir(cls):
        if not name.startswith('test'):
            continue
        method = getattr(cls, name)
        if not callable(method):
            continue
        if name not in cls.__dict__:
            continue  # inherited; reported against the class that defines it
        doc = (method.__doc__ or '').strip()
        if not doc:
            found.append(Offence(module_name, cls.__name__, name,
                                 'missing docstring'))
        elif EXPECTED_MARKER not in doc:
            found.append(Offence(module_name, cls.__name__, name,
                                 f'docstring has no {EXPECTED_MARKER} line'))
    return tuple(found)


def offences(tier_dir):
    """
    Return every convention offence in a tier.

    Parameters
    ----------
    tier_dir : str or Path

    Returns
    -------
    tuple of Offence
    """
    found = []
    for module_name, cls in iter_test_classes(tier_dir):
        found.extend(offences_in_class(module_name, cls))
    return tuple(found)


def format_offences(found):
    """
    Render offences as a report for an assertion message.

    Parameters
    ----------
    found : sequence of Offence

    Returns
    -------
    str
    """
    if not found:
        return ''
    lines = [f'{len(found)} docstring-convention offence(s):']
    for offence in found:
        target = f'{offence.cls}.{offence.method}' if offence.method else offence.cls
        lines.append(f'  {offence.module}::{target} -- {offence.reason}')
    return '\n'.join(lines)


def declared_in(classes, attr, exclude_classes=()):
    """
    Union an iterable-valued class attribute across the given classes.

    Each tier declares its coverage through a class attribute (SFF_CHECK_IDS,
    COVERS, SCHEMA_CONSTRAINTS); a coverage meta-test collects the declared set
    with this helper and diffs it against the mechanically-required set. Factored
    here (a pre-flight decision) so the six tiers share one collector.

    Parameters
    ----------
    classes : iterable of type
    attr : str
        Name of a class attribute whose value is an iterable of hashables.
    exclude_classes : container of str
        Class names to skip -- e.g. a tier's own coverage meta-test class, which
        declares no coverage of its own.

    Returns
    -------
    set
    """
    collected = set()
    for cls in classes:
        if cls.__name__ in exclude_classes:
            continue
        values = getattr(cls, attr, None)
        if values:
            collected.update(values)
    return collected


def declared(tier_dir, attr, exclude_classes=()):
    """
    Union a class attribute across every test class in a tier.

    Convenience wrapper over :func:`declared_in` and :func:`iter_test_classes`.

    Parameters
    ----------
    tier_dir : str or Path
    attr : str
    exclude_classes : container of str

    Returns
    -------
    set
    """
    classes = (cls for _module, cls in iter_test_classes(tier_dir))
    return declared_in(classes, attr, exclude_classes)


class DocstringConventionMixin:
    """Shared body for a tier's docstring-convention meta-test.

    A tier's meta-test declares
    ``class TestXMetaDocstrings(DocstringConventionMixin, unittest.TestCase):``
    and sets ``TIER_DIR``; this walks that tier and asserts every test method and
    class obeys spec §6. Factored here (a pre-flight decision) so the six tiers
    share one body instead of copying it. Not named ``Test*`` and not a
    ``TestCase`` subclass, so pytest never collects the mixin itself.
    """

    #: The tier directory to walk. A subclass MUST override this.
    TIER_DIR = None

    def test_every_test_obeys_the_docstring_convention(self):
        """
        Every test method and class in this tier carries the required docstrings.

        Expected: offences(TIER_DIR) is empty; otherwise the failure names each
        offending method or class and why it failed.
        """
        self.assertIsNotNone(
            self.TIER_DIR,
            'a DocstringConventionMixin subclass must set TIER_DIR')
        found = offences(self.TIER_DIR)
        self.assertEqual(found, (), '\n' + format_offences(found))
