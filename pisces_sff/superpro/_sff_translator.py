"""
_sff_translator.py
──────────────────
Translates the raw extraction dict (output of _data_extraction.extract_all)
into a valid SFF JSON document.

SFF schema reference:
  https://sustainability-software-lab.github.io/pisces-standard-flowsheet-format/

The translator is purposefully lenient — missing or None values are either
omitted from the output (for optional fields) or given sensible defaults, so
that a partial extraction still yields a usable SFF document.
"""

import logging
from typing import Any

from ._unit_type_map import map_unit_type

log = logging.getLogger(__name__)

DEFAULT_SFF_VERSION = "0.0.2"


# ── Public entry point ────────────────────────────────────────────────────────

def translate(
    raw: dict,
    schema_version: str = DEFAULT_SFF_VERSION,
) -> tuple[dict, list[dict]]:
    """
    Translate the raw extraction dict into an SFF document.

    Args:
        raw: Output of _data_extraction.extract_all()
        schema_version: Target SFF schema version string (e.g. ``"0.0.2"`` or
            ``"0.0.3"``). This value is embedded in ``metadata.sff_version``
            of the output document and should match the schema used to
            validate it. Defaults to ``"0.0.2"``.

    Returns:
        (sff_document, warnings) where warnings is a list of
        {"field": ..., "message": ...} dicts for non-fatal issues.
    """
    warnings: list[dict] = []

    metadata  = _build_metadata(raw.get("metadata", {}), warnings, schema_version)
    units     = [_translate_unit(u, warnings) for u in raw.get("units", [])]
    streams   = [_translate_stream(s, warnings) for s in raw.get("streams", [])]
    utilities = _translate_utilities(raw.get("utilities", {}), warnings)
    chemicals = _build_chemicals(raw.get("streams", []))

    sff_doc = {
        "metadata":  metadata,
        "units":     units,
        "streams":   streams,
        "utilities": utilities,
        "chemicals": chemicals,
    }

    return sff_doc, warnings


# ── Metadata ──────────────────────────────────────────────────────────────────

_CURRENCY_NAME_TO_CODE: dict[str, str] = {
    "us dollar":         "USD",
    "euro":              "EUR",
    "british pound":     "GBP",
    "pound sterling":    "GBP",
    "japanese yen":      "JPY",
    "chinese yuan":      "CNY",
    "renminbi":          "CNY",
    "canadian dollar":   "CAD",
    "australian dollar": "AUD",
    "swiss franc":       "CHF",
    "korean won":        "KRW",
    "indian rupee":      "INR",
    "brazilian real":    "BRL",
    "mexican peso":      "MXN",
}


def _build_metadata(meta: dict, warnings: list, schema_version: str) -> dict:
    tea_year_raw = meta.get("TEA_year_raw")
    try:
        tea_year = int(tea_year_raw) if tea_year_raw is not None else None
    except (TypeError, ValueError):
        tea_year = None

    out: dict[str, Any] = {
        "sff_version": schema_version,
        "TEA_year":    tea_year or meta.get("TEA_year") or _current_year(),
    }

    currency_name_full = meta.get("currency_name_full")
    direct_currency    = meta.get("TEA_currency")
    if currency_name_full:
        code = _CURRENCY_NAME_TO_CODE.get(str(currency_name_full).lower().strip())
        if code:
            out["TEA_currency"] = code
        else:
            if direct_currency:
                out["TEA_currency"] = str(direct_currency)
            else:
                out["TEA_currency"] = str(currency_name_full)
                warnings.append({
                    "field":   "metadata.TEA_currency",
                    "message": f"Unrecognised currency name '{currency_name_full}'; stored verbatim.",
                })
    elif direct_currency:
        out["TEA_currency"] = str(direct_currency)

    # flowsheet_designers: compose from designer name + company (VarIDs 30129, 30131)
    designer_1 = meta.get("flowsheet_designer_1")
    company    = meta.get("flowsheet_company")
    if designer_1 or company:
        if designer_1 and company:
            composed_designers = f"{designer_1} ({company})"
        else:
            composed_designers = designer_1 or company
        if not meta.get("flowsheet_designers"):
            out["flowsheet_designers"] = composed_designers

    main_product = meta.get("main_product_stream_name")

    for key in ("product_name", "organism", "process_title", "flowsheet_designers", "source_doi"):
        val = meta.get(key)
        if val:
            out[key] = val
        elif key == "product_name" and main_product and not out.get("product_name"):
            out["product_name"] = str(main_product)

    if out.get("TEA_currency") is None:
        out["TEA_currency"] = "USD"

    proc_desc = meta.get("process_description")
    if proc_desc:
        out["process_description"] = str(proc_desc)

    _EXTENDED_META_FIELDS = (
        "annual_operating_cost_usd",
        "annual_electricity_cost_usd",
        "annual_labor_cost_usd",
        "annual_waste_treatment_usd",
        "gross_profit_usd",
        "net_profit_usd",
        "gross_margin_pct",
        "payback_period_years",
        "IRR_before_taxes_pct",
        "IRR_after_taxes_pct",
        "NPV_usd",
        "ROI_pct",
        "annual_throughput_kg_yr",
        "total_purchase_cost_usd",
        "DFC_usd",
        "annual_revenue_usd",
        "annual_operating_time_s",
        "batch_time_s",
        "number_of_batches_per_year",
        "batch_throughput_kg",
        "is_batch_mode",
    )
    for field in _EXTENDED_META_FIELDS:
        val = meta.get(field)
        if val is not None:
            out[field] = val

    return out


def _current_year() -> int:
    from datetime import date
    return date.today().year


# ── Units ─────────────────────────────────────────────────────────────────────

def _translate_unit(raw_unit: dict, warnings: list) -> dict:
    uid = raw_unit.get("id", "UNKNOWN")

    raw_type = raw_unit.get("unit_type", "Unknown")
    sff_type, is_known = map_unit_type(raw_type)
    if not is_known:
        warnings.append({
            "field":   f"units[{uid}].unit_type",
            "message": f"SuperPro unit type '{raw_type}' not in mapping table; using raw value.",
        })
        log.warning("Unknown unit type '%s' for unit %s", raw_type, uid)

    unit: dict[str, Any] = {
        "id":        uid,
        "unit_type": sff_type,
    }

    for key in ("design_input_specs", "design_results", "purchase_costs",
                "installed_costs", "utility_consumption_results", "utility_production_results"):
        val = raw_unit.get(key)
        if val:
            unit[key] = _clean_numeric_dict(val, uid, key, warnings)

    reactions = _translate_reactions(raw_unit.get("reactions", []), uid, warnings)
    if reactions:
        unit["reactions"] = reactions

    return unit


def _clean_numeric_dict(d: dict, uid: str, field: str, warnings: list) -> dict:
    clean = {}
    for k, v in d.items():
        if isinstance(v, (int, float)):
            clean[str(k)] = float(v)
        else:
            warnings.append({
                "field":   f"units[{uid}].{field}.{k}",
                "message": f"Non-numeric value '{v}' skipped.",
            })
    return clean


def _translate_reactions(raw_reactions: list, uid: str, warnings: list) -> list:
    out = []
    for rx in raw_reactions:
        entry: dict[str, Any] = {
            "index":    rx.get("index", len(out)),
            "equation": rx.get("equation", ""),
            "reactant": rx.get("reactant", ""),
        }
        conv = rx.get("conversion")
        if isinstance(conv, (int, float)):
            entry["conversion"] = float(conv)
        else:
            warnings.append({
                "field":   f"units[{uid}].reactions[{entry['index']}].conversion",
                "message": "Conversion value missing or non-numeric; field omitted.",
            })
        out.append(entry)
    return out


# ── Streams ───────────────────────────────────────────────────────────────────

def _translate_stream(raw_stream: dict, warnings: list) -> dict:
    sid = raw_stream.get("id", "UNKNOWN")

    stream: dict[str, Any] = {
        "id":             sid,
        "source_unit_id": raw_stream.get("source_unit_id", "None"),
        "sink_unit_id":   raw_stream.get("sink_unit_id", "None"),
    }

    stream_type = raw_stream.get("stream_type")
    if stream_type in ("feed", "product", "waste", "internal"):
        stream["stream_type"] = stream_type

    desc = raw_stream.get("stream_description")
    if desc:
        stream["stream_description"] = desc

    price = raw_stream.get("price")
    if price and isinstance(price.get("value"), (int, float)):
        stream["price"] = price

    raw_props = raw_stream.get("stream_properties", {})
    props: dict[str, Any] = {}

    for field, default_units in [
        ("total_mass_flow",       "kg/h"),
        ("total_volumetric_flow", "m3/h"),
        ("temperature",           "K"),
        ("pressure",              "Pa"),
    ]:
        qty = raw_props.get(field, {})
        value = qty.get("value")
        if value is None:
            warnings.append({
                "field":   f"streams[{sid}].stream_properties.{field}",
                "message": f"Required field '{field}' has null value.",
            })
            value = 0.0
        props[field] = {"value": float(value), "units": qty.get("units", default_units)}

    molar = raw_props.get("total_molar_flow")
    if molar and isinstance(molar.get("value"), (int, float)):
        props["total_molar_flow"] = {"value": float(molar["value"]), "units": molar.get("units", "kmol/h")}

    composition = _translate_composition(raw_props.get("composition", []), sid, warnings)
    if composition:
        props["composition"] = composition

    stream["stream_properties"] = props
    return stream


def _translate_composition(raw_comp: list, sid: str, warnings: list) -> list:
    out = []
    for comp in raw_comp:
        name     = comp.get("component_name")
        mol_frac = comp.get("mol_fraction", 0.0)
        phase    = comp.get("phase", "l")

        if not name:
            continue

        mol_frac = float(mol_frac)
        if not (0.0 <= mol_frac <= 1.0):
            warnings.append({
                "field":   f"streams[{sid}].composition.{name}.mol_fraction",
                "message": f"mol_fraction {mol_frac} out of [0,1] range; clamped.",
            })
            mol_frac = max(0.0, min(1.0, mol_frac))

        phase_str = str(phase).lower().strip()
        if phase_str not in ("l", "g", "s"):
            phase_map = {"liquid": "l", "gas": "g", "solid": "s", "vapor": "g"}
            phase_str = phase_map.get(phase_str, "l")

        out.append({
            "component_name": str(name),
            "mol_fraction":   mol_frac,
            "phase":          phase_str,
        })

    return out


# ── Chemicals ─────────────────────────────────────────────────────────────────

def _build_chemicals(raw_streams: list) -> list[dict]:
    """
    Derive a deduplicated chemicals list from all stream compositions.

    SFF requires each chemical to have an 'id' matching component_name and
    a 'registry_id'. Since SuperPro doesn't expose CAS numbers via COM,
    we store the component name as the registry_id placeholder.
    """
    seen: set[str] = set()
    chemicals: list[dict] = []

    for stream in raw_streams:
        props = stream.get("stream_properties", {})
        for comp in props.get("composition", []):
            name = comp.get("component_name")
            if name and name not in seen:
                seen.add(name)
                chemicals.append({
                    "id":          name,
                    "registry_id": name,
                })

    return sorted(chemicals, key=lambda c: c["id"])


# ── Utilities ─────────────────────────────────────────────────────────────────

def _translate_utilities(raw_utils: dict, warnings: list) -> dict:
    heat_utils  = [_translate_heat_utility(u, warnings) for u in raw_utils.get("heat_utilities", [])]
    power_utils = [_translate_power_utility(u, warnings) for u in raw_utils.get("power_utilities", [])]
    other_utils = [_translate_other_utility(u, warnings) for u in raw_utils.get("other_utilities", [])]

    return {
        "heat_utilities":  heat_utils,
        "power_utilities": power_utils,
        "other_utilities": other_utils,
    }


def _translate_heat_utility(raw: dict, warnings: list) -> dict:
    uid = raw.get("id", "Unknown")
    out: dict[str, Any] = {
        "id":                      uid,
        "temperature":             _ensure_quantity(raw.get("temperature"), "K", uid, "temperature", warnings),
        "pressure":                _ensure_quantity(raw.get("pressure"), "Pa", uid, "pressure", warnings),
        "composition":             raw.get("composition", []),
        "units_for_utility_results": raw.get("units_for_utility_results", "kJ/h"),
    }
    for opt_field in ("regeneration_price", "heat_transfer_price", "temperature_limit"):
        val = raw.get(opt_field)
        if val:
            out[opt_field] = val
    eff = raw.get("heat_transfer_efficiency")
    if isinstance(eff, (int, float)):
        out["heat_transfer_efficiency"] = float(eff)
    return out


def _translate_power_utility(raw: dict, warnings: list) -> dict:
    out: dict[str, Any] = {
        "id":                      raw.get("id", "Unknown"),
        "units_for_utility_results": raw.get("units_for_utility_results", "kW"),
    }
    price = raw.get("price")
    if price and isinstance(price.get("value"), (int, float)):
        out["price"] = price
    return out


def _translate_other_utility(raw: dict, warnings: list) -> dict:
    uid = raw.get("id", "Unknown")
    out: dict[str, Any] = {
        "id":                      uid,
        "temperature":             _ensure_quantity(raw.get("temperature"), "K", uid, "temperature", warnings),
        "pressure":                _ensure_quantity(raw.get("pressure"), "Pa", uid, "pressure", warnings),
        "composition":             raw.get("composition", []),
        "units_for_utility_results": raw.get("units_for_utility_results", "kg/h"),
    }
    price = raw.get("price")
    if price and isinstance(price.get("value"), (int, float)):
        out["price"] = price
    return out


def _ensure_quantity(qty: dict | None, default_units: str, uid: str, field: str, warnings: list) -> dict:
    if qty is None or not isinstance(qty.get("value"), (int, float)):
        warnings.append({
            "field":   f"utilities[{uid}].{field}",
            "message": "Missing or non-numeric value; defaulting to 0.",
        })
        return {"value": 0.0, "units": default_units}
    return {"value": float(qty["value"]), "units": qty.get("units", default_units)}
