# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
The design-input-spec registry: ``pisces_sff/design_specs/biosteam.yaml``.

The registry declares, per simulator unit class, which initialization
parameters constitute that unit type's design input specification and how to
read each one from a live unit object. Entries are keyed by **class name**
(each entry also records the class's display ``line``, informational only) and
resolved by an MRO walk, so a subclass without its own entry uses its nearest
listed ancestor's entry as-is. Each parameter carries an ordered list of
accessor paths (e.g. ``P``, then ``outs[0].P``); the first accessor yielding a
non-None value wins and is exported under the parameter name. A parameter
whose accessors are all exhausted is omitted -- the 0.1.4+ exporter emits no
null design specs. Consumed by ``pisces_sff._export.get_design_input_specs``
from SFF 0.1.4 on (``_DESIGN_SPEC_REGISTRY_SINCE``); older exporters keep the
legacy fixed probe for byte-stable output.

Import-light by design: no package-relative imports at module top and ``yaml``
is imported lazily inside functions, so Tier 1 can load this file by path
without pulling in biosteam (same pattern as ``_registry.py``). The
:func:`generate_design_spec_registry` refresh path imports biosteam lazily.
"""

import argparse
import inspect
import re
from pathlib import Path

# REGISTRY_PATH is a module attribute but deliberately NOT in __all__: the
# star-import aggregation in pisces_sff/__init__.py would otherwise surface an
# ambiguous package-level REGISTRY_PATH next to _registry.py's model registry.
__all__ = ('load_design_spec_registry', 'resolve_design_input_specs', 'DesignSpecReadError')

_PKG_ROOT = Path(__file__).resolve().parent

#: The committed registry file.
REGISTRY_PATH = _PKG_ROOT / 'design_specs' / 'biosteam.yaml'

# Accessor path syntax: dotted attribute names, each optionally followed by ONE
# non-negative integer index -- 'P', 'outs[0].P', 'effluent.T'. Anything else
# is rejected at load time, so the resolver never needs eval().
_ACCESSOR_RE = re.compile(
    r'^[A-Za-z_][A-Za-z0-9_]*(\[\d+\])?'
    r'(\.[A-Za-z_][A-Za-z0-9_]*(\[\d+\])?)*$')
_STEP_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?$')

#: Distinguishes "accessor could not produce a value" from a real None value
#: inside _read_accessor (both mean "try the next accessor" to the resolver).
_MISSING = object()


class DesignSpecReadError(Exception):
    """An accessor read failed for an unexpected reason (i.e., not the
    tolerated AttributeError/IndexError/KeyError "not available" outcomes).

    Defined here rather than reusing :class:`pisces_sff.exceptions
    .DesignInputSpecError` so this module stays loadable by file path with no
    package-relative import (the same deliberate-duplication trade-off as
    ``_harness._schema_version``). ``pisces_sff._export`` catches this and
    re-raises it as ``DesignInputSpecError``, so the package's public
    exception contract is unchanged.
    """


def parse_accessor(accessor):
    """
    Parse one accessor path into resolution steps.

    Parameters
    ----------
    accessor : str
        A dotted attribute path, each segment optionally carrying one
        non-negative integer index; e.g. ``'P'``, ``'outs[0].P'``.

    Returns
    -------
    list of (str, int or None)
        One ``(attribute_name, index)`` pair per segment; ``index`` is None
        for un-indexed segments.

    Raises
    ------
    ValueError
        If `accessor` does not match the accessor syntax.
    """
    if not isinstance(accessor, str) or not _ACCESSOR_RE.match(accessor):
        raise ValueError(
            f'malformed accessor {accessor!r}: expected dotted attribute '
            f"names with optional [<int>] indexes, e.g. 'P' or 'outs[0].P'")
    steps = []
    for segment in accessor.split('.'):
        name, index = _STEP_RE.match(segment).groups()
        steps.append((name, int(index) if index is not None else None))
    return steps


_ENTRY_KEYS = frozenset({'line', 'design_input_spec_params'})


def load_design_spec_registry(path=None):
    """
    Parse and validate the design-input-spec registry.

    Parameters
    ----------
    path : str or Path, optional
        Registry file to load. Defaults to the committed
        ``pisces_sff/design_specs/biosteam.yaml``.

    Returns
    -------
    dict
        ``{class_name: {'line': str, 'design_input_spec_params':
        {param: {'accessors': [str, ...]}}}}``.

    Raises
    ------
    ValueError
        On a missing/unreadable file, malformed YAML, a non-mapping shape, a
        missing or unknown entry key, a non-string ``line``, or a parameter
        without a non-empty list of syntactically valid accessors.
    """
    import yaml  # lazy: keep the module import-light for Tier 1

    path = Path(path) if path is not None else REGISTRY_PATH
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as e:
        raise ValueError(
            f'design-spec registry not readable: {path}: {e}') from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(
            f'design-spec registry is not valid YAML: {path}: {e}') from e
    if not isinstance(data, dict):
        raise ValueError(
            f'design-spec registry must be a mapping of class names to '
            f'entries: {path}')
    for class_name, entry in data.items():
        if not isinstance(entry, dict):
            raise ValueError(f'{class_name}: entry must be a mapping')
        unknown = set(entry) - _ENTRY_KEYS
        if unknown:
            raise ValueError(
                f'{class_name}: unknown key(s) {sorted(unknown)}; '
                f'expected only {sorted(_ENTRY_KEYS)}')
        if not isinstance(entry.get('line'), str):
            raise ValueError(f"{class_name}: 'line' must be a string")
        params = entry.get('design_input_spec_params')
        if not isinstance(params, dict):
            raise ValueError(
                f"{class_name}: 'design_input_spec_params' must be a "
                f'mapping (possibly empty)')
        for param, param_spec in params.items():
            accessors = (param_spec.get('accessors')
                         if isinstance(param_spec, dict) else None)
            if not isinstance(accessors, list) or not accessors:
                raise ValueError(
                    f'{class_name}.{param}: must carry a non-empty '
                    f"'accessors' list")
            for accessor in accessors:
                try:
                    parse_accessor(accessor)
                except ValueError as e:
                    raise ValueError(f'{class_name}.{param}: {e}') from e
    return data


def _read_accessor(obj, steps, accessor):
    """Follow `steps` from `obj`; return the value or ``_MISSING``.

    AttributeError/IndexError/KeyError anywhere along the path, or a final
    value of None, mean "this accessor cannot provide the parameter" and
    return ``_MISSING`` so the caller tries the next accessor. Any other
    exception is unexpected and raises :class:`DesignSpecReadError`.
    """
    value = obj
    for name, index in steps:
        try:
            value = getattr(value, name)
            if index is not None:
                value = value[index]
        except (AttributeError, IndexError, KeyError):
            return _MISSING
        except Exception as e:
            raise DesignSpecReadError(
                f'reading accessor {accessor!r} failed at segment '
                f'{name!r}: {e}') from e
    return _MISSING if value is None else value


def resolve_design_input_specs(unit, registry):
    """
    Resolve a unit's design input specs from the registry.

    The unit's class is matched by walking ``type(unit).__mro__`` and taking
    the first class whose ``__name__`` is a registry key; that entry is used
    as-is (no merging across ancestors). For each of the entry's parameters
    the accessors are tried in order and the first non-None value is recorded
    under the parameter name; a parameter whose accessors are all exhausted
    is omitted.

    Parameters
    ----------
    unit : object
        A simulator unit object.
    registry : dict
        A registry mapping as returned by :func:`load_design_spec_registry`.

    Returns
    -------
    dict
        ``{param_name: value}``; ``{}`` when no ancestor is listed.

    Raises
    ------
    DesignSpecReadError
        If an accessor read fails for an unexpected reason.
    """
    entry = None
    for cls in type(unit).__mro__:
        if cls.__name__ in registry:
            entry = registry[cls.__name__]
            break
    if entry is None:
        return {}
    specs = {}
    for param, param_spec in entry['design_input_spec_params'].items():
        for accessor in param_spec['accessors']:
            value = _read_accessor(unit, parse_accessor(accessor), accessor)
            if value is not _MISSING:
                specs[param] = value
                break
    return specs
