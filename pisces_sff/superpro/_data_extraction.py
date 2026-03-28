"""
_data_extraction.py
───────────────────
Enumerate units, streams, and chemicals from a SuperPro Designer document
COM object using SuperPro v14's function-based COM API.

Key pattern: all COM methods with [out] VARIANT* parameters must be called
via _oleobj_.InvokeTypes (not Python dispatch). pywin32 returns byref output
values as a result tuple: (retval, out1, out2, ...).

Confirmed dispids (from Designer.tlb makepy analysis):
  DoMEBalances           58   [out] VARIANT*
  DoEconomicCalculations 59   (no args)
  GetFlowsheetVarVal      5   (VarID:i4, [out]val:VARIANT*)
  GetUPVarVal            28   (procName:str, VarID:i4, [out]val:VARIANT*)
  GetEquipVarVal         41   (equipName:str, VarID:i4, [out]val:VARIANT*)
  GetStreamVarVal        19   (streamName:str, VarID:i4, [out]val:VARIANT*, compName:str)
  StartEnumeration       67   ([out]pos:VARIANT*, listID:i4, containerID:i4, name:str)
  GetNextItemName        68   ([in/out]pos:VARIANT*, [out]name:VARIANT*, listID:i4, containerID:i4, name:str)

Extended VarIDs (numeric values confirmed by running scripts/discover_varids.py on VM
  against superpro-examples/Bio-Fuels/Ethanol/Ethanol_v14.spf on 2026-03-12).
  Source: Designer.tlb TypeLib introspection (1122 VarID enum constants extracted).
"""

import logging
from typing import Any

log = logging.getLogger(__name__)

# ── COM constants ──────────────────────────────────────────────────────────────

_LCID = 0

_DISPID_DO_ME_BALANCES          = 58
_DISPID_DO_ECONOMIC_CALCS       = 59
_DISPID_GET_FLOWSHEET_VAR       = 5
_DISPID_GET_UP_VAR              = 28
_DISPID_GET_EQUIP_VAR           = 41
_DISPID_GET_STREAM_VAR          = 19
_DISPID_START_ENUMERATION       = 67
_DISPID_GET_NEXT_ITEM_NAME      = 68

# ListType IDs
_LID_UNIT_PROC  = 256
_LID_STREAM     = 1024
_LID_PURE_COMP  = 4099

# ContainerType IDs
_CID_FLOWSHEET  = 1
_CID_STREAM     = 5

# VarIDs — streams (GetStreamVarVal)
_VID_MASS_FLOW       = 24577   # kg/h
_VID_VOL_FLOW        = 24579   # m3/h
_VID_TEMPERATURE     = 24580   # K
_VID_PRESSURE        = 24581   # Pa
_VID_SOURCE_PROC     = 24593   # str: source unit procedure name
_VID_DEST_PROC       = 24594   # str: destination unit procedure name
_VID_COMP_MASS_FRAC  = 24832   # float: component mass fraction
_VID_COMP_MOLE_FRAC  = 24833   # float: component mole fraction

# VarIDs — unit procedures (GetUPVarVal)
_VID_UP_EQUIP_NAME   = 12360   # str: equipment name within procedure (e.g. 'R-101')
_VID_UP_OPER_TYPE    = 12361   # str: operation type (e.g. 'Chlorination, Salt Formation')

# VarIDs — equipment (GetEquipVarVal)
_VID_EQUIP_VOLUME    = 1       # m3
_VID_EQUIP_DIAMETER  = 14      # m
_VID_NO_UNITS        = 12308   # count of parallel units
_VID_EQUIP_TYPE      = 12358   # str: equipment type (e.g. 'Stirred Reactor')
_VID_EQUIP_PC        = 16128   # $: equipment purchase cost

# VarIDs — flowsheet (GetFlowsheetVarVal)
_VID_ANNUAL_THROUGHPUT = 29956  # kg/year
_VID_PURCHASE_COST     = 30464  # $: total equipment purchase cost
_VID_DFC               = 30466  # $: direct fixed capital
_VID_REVENUE           = 36096  # $/year

# ── Extended flowsheet VarIDs ─────────────────────────────────────────────────
# Confirmed via Designer.tlb TypeLib introspection (2026-03-12).

_VID_FS_AOT_AVAILABLE        = 30213  # seconds: Annual Operating Time Available
_VID_FS_BATCH_TIME           = 30208  # seconds: Batch Duration (= cycle time for continuous)
_VID_FS_NUM_BATCHES_PER_YEAR = 30212  # count: Number of Batches Per Year
_VID_FS_BATCH_THROUGHPUT     = 29957  # kg/batch (or kg/h for continuous): Batch/Target Throughput
_VID_FS_IS_BATCH_MODE        = 29953  # bool: batch (True) vs continuous (False)
_VID_FS_ANNUAL_OPER_COST     = 30482  # $/year: Annual Operating Cost (OPEX)
_VID_FS_ANNUAL_ELEC_COST     = 30489  # $/year: Annual Electricity Cost
_VID_FS_LABOR_COST           = 30471  # $/year: Annual Labor Cost
_VID_FS_WASTE_TREAT_COST     = 30477  # $/year: Waste Treatment & Disposal Cost
_VID_FS_GROSS_PROFIT         = 36103  # $/year: Gross Profit
_VID_FS_NET_PROFIT           = 36104  # $/year: Net Profit
_VID_FS_GROSS_MARGIN         = 36098  # %: Gross Margin
_VID_FS_PAYBACK_TIME         = 36105  # years: Payback Period
_VID_FS_IRR_BEFORE_TAXES     = 36100  # %: IRR Before Taxes
_VID_FS_IRR_AFTER_TAXES      = 36101  # %: IRR After Taxes
_VID_FS_NPV                  = 36109  # $: Net Present Value (low interest scenario)
_VID_FS_ROI                  = 36099  # %: Return on Investment

# VarIDs for authorship / document metadata (confirmed via discover_varids.py, 2026-03-12)
_VID_FS_TEA_YEAR             = 36114  # int: TEA analysis base year (e.g. 2013)
_VID_FS_DESIGNER_NAME_1      = 30129  # str: primary designer/team name (e.g. "Intelligen Team")
_VID_FS_DESIGNER_NAME_2      = 30131  # str: company/org name (e.g. "Intelligen Inc.")
_VID_FS_PROCESS_DESCRIPTION  = 30135  # str: full process description text
_VID_FS_MAIN_PRODUCT_STREAM  = 30491  # str: name of main product stream (e.g. "Product")
_VID_FS_CURRENCY_NAME        = 36130  # str: currency full name (e.g. "US Dollar")

# Map of extended flowsheet VarID → (field_name, units_label)
_EXTENDED_FLOWSHEET_VIDS: dict[int | None, tuple[str, str]] = {
    _VID_FS_AOT_AVAILABLE:        ("annual_operating_time_s",      "s"),
    _VID_FS_BATCH_TIME:           ("batch_time_s",                 "s"),
    _VID_FS_NUM_BATCHES_PER_YEAR: ("number_of_batches_per_year",   ""),
    _VID_FS_BATCH_THROUGHPUT:     ("batch_throughput_kg",          "kg/batch"),
    _VID_FS_IS_BATCH_MODE:        ("is_batch_mode",                "bool"),
    _VID_FS_ANNUAL_OPER_COST:     ("annual_operating_cost_usd",    "$/year"),
    _VID_FS_ANNUAL_ELEC_COST:     ("annual_electricity_cost_usd",  "$/year"),
    _VID_FS_LABOR_COST:           ("annual_labor_cost_usd",        "$/year"),
    _VID_FS_WASTE_TREAT_COST:     ("annual_waste_treatment_usd",   "$/year"),
    _VID_FS_GROSS_PROFIT:         ("gross_profit_usd",             "$/year"),
    _VID_FS_NET_PROFIT:           ("net_profit_usd",               "$/year"),
    _VID_FS_GROSS_MARGIN:         ("gross_margin_pct",             "%"),
    _VID_FS_PAYBACK_TIME:         ("payback_period_years",         "years"),
    _VID_FS_IRR_BEFORE_TAXES:     ("IRR_before_taxes_pct",         "%"),
    _VID_FS_IRR_AFTER_TAXES:      ("IRR_after_taxes_pct",          "%"),
    _VID_FS_NPV:                  ("NPV_usd",                      "$"),
    _VID_FS_ROI:                  ("ROI_pct",                      "%"),
    # Authorship / document metadata
    _VID_FS_TEA_YEAR:             ("TEA_year_raw",                 "year"),
    _VID_FS_DESIGNER_NAME_1:      ("flowsheet_designer_1",         ""),
    _VID_FS_DESIGNER_NAME_2:      ("flowsheet_company",            ""),
    _VID_FS_PROCESS_DESCRIPTION:  ("process_description",          ""),
    _VID_FS_MAIN_PRODUCT_STREAM:  ("main_product_stream_name",     ""),
    _VID_FS_CURRENCY_NAME:        ("currency_name_full",           ""),
}

# ── Extended stream VarIDs ────────────────────────────────────────────────────

_VID_STREAM_PRICE            = 25088  # $/kg (or $/unit): stream unit price
_VID_STREAM_PURCHASING_PRICE = None   # purchasing price shares dispid 25088 with streamPrice in v14
_VID_STREAM_IS_INPUT         = 25096  # bool: feed/input stream flag
_VID_STREAM_IS_OUTPUT        = 25097  # bool: product/output stream flag
_VID_STREAM_IS_RAW_MATERIAL  = 25099  # bool: raw material flag
_VID_STREAM_IS_WASTE         = 25098  # bool: waste stream flag

# Arg type tuples for InvokeTypes
_ARGS_I4_BYREF   = ((3, 0), (16396, 0))
_ARGS_STR_I4_BYREF_STR = ((8, 0), (3, 0), (16396, 0), (8, 0))
_ARGS_STR_I4_BYREF     = ((8, 0), (3, 0), (16396, 0))
_ARGS_ENUM_START   = ((16396, 0), (3, 0), (3, 0), (8, 0))
_ARGS_ENUM_NEXT    = ((16396, 0), (16396, 0), (3, 0), (3, 0), (8, 0))


# ── Low-level COM helpers ──────────────────────────────────────────────────────

def _get_flowsheet_var(doc, var_id: int) -> Any:
    """GetFlowsheetVarVal(VarID, [out]val) → val or None."""
    try:
        r = doc._oleobj_.InvokeTypes(
            _DISPID_GET_FLOWSHEET_VAR, _LCID, 1, (11, 0),
            _ARGS_I4_BYREF,
            var_id, None,
        )
        return r[1] if isinstance(r, tuple) else None
    except Exception:
        return None


def _get_up_var(doc, proc_name: str, var_id: int) -> Any:
    """GetUPVarVal(procName, VarID, [out]val) → val or None."""
    try:
        r = doc._oleobj_.InvokeTypes(
            _DISPID_GET_UP_VAR, _LCID, 1, (11, 0),
            _ARGS_STR_I4_BYREF,
            proc_name, var_id, None,
        )
        return r[1] if isinstance(r, tuple) else None
    except Exception:
        return None


def _get_equip_var(doc, equip_name: str, var_id: int) -> Any:
    """GetEquipVarVal(equipName, VarID, [out]val) → val or None."""
    try:
        r = doc._oleobj_.InvokeTypes(
            _DISPID_GET_EQUIP_VAR, _LCID, 1, (11, 0),
            _ARGS_STR_I4_BYREF,
            equip_name, var_id, None,
        )
        return r[1] if isinstance(r, tuple) else None
    except Exception:
        return None


def _get_stream_var(doc, stream_name: str, var_id: int, comp_name: str = "") -> Any:
    """GetStreamVarVal(streamName, VarID, [out]val, compName) → val or None."""
    try:
        r = doc._oleobj_.InvokeTypes(
            _DISPID_GET_STREAM_VAR, _LCID, 1, (11, 0),
            _ARGS_STR_I4_BYREF_STR,
            stream_name, var_id, None, comp_name,
        )
        return r[1] if isinstance(r, tuple) else None
    except Exception:
        return None


def _enumerate_items(doc, list_id: int, container_id: int, container_name: str = "") -> list[str]:
    """
    Enumerate all items in a list via StartEnumeration + GetNextItemName.

    Returns the list of item name strings.
    """
    items: list[str] = []
    try:
        r = doc._oleobj_.InvokeTypes(
            _DISPID_START_ENUMERATION, _LCID, 1, (11, 0),
            _ARGS_ENUM_START,
            None, list_id, container_id, container_name,
        )
        if not isinstance(r, tuple) or not r[0]:
            return items
        pos = r[1]
        while True:
            r2 = doc._oleobj_.InvokeTypes(
                _DISPID_GET_NEXT_ITEM_NAME, _LCID, 1, (11, 0),
                _ARGS_ENUM_NEXT,
                pos, None, list_id, container_id, container_name,
            )
            if not isinstance(r2, tuple):
                break
            ok, pos, name = r2[0], r2[1], r2[2]
            if name:
                items.append(name)
            if not ok:
                break
    except Exception as exc:
        log.warning("Enumeration failed (lid=%d, cid=%d, name=%r): %s",
                    list_id, container_id, container_name, exc)
    return items


def _qty(value, units: str) -> dict:
    return {"value": float(value) if isinstance(value, (int, float)) else None, "units": units}


# ── Metadata ───────────────────────────────────────────────────────────────────

def extract_metadata(doc) -> dict:
    meta: dict[str, Any] = {}

    throughput = _get_flowsheet_var(doc, _VID_ANNUAL_THROUGHPUT)
    if throughput is not None:
        meta["annual_throughput_kg_yr"] = throughput

    pc = _get_flowsheet_var(doc, _VID_PURCHASE_COST)
    if pc is not None:
        meta["total_purchase_cost_usd"] = pc

    dfc = _get_flowsheet_var(doc, _VID_DFC)
    if dfc is not None:
        meta["DFC_usd"] = dfc

    revenue = _get_flowsheet_var(doc, _VID_REVENUE)
    if revenue is not None:
        meta["annual_revenue_usd"] = revenue

    for vid, (field_name, units) in _EXTENDED_FLOWSHEET_VIDS.items():
        if vid is None:
            continue
        val = _get_flowsheet_var(doc, vid)
        if val is not None:
            meta[field_name] = val
            log.debug("Extended flowsheet %s (VarID %d) = %s %s", field_name, vid, val, units)

    return meta


# ── Unit operations ────────────────────────────────────────────────────────────

def extract_units(doc) -> list[dict]:
    proc_names = _enumerate_items(doc, _LID_UNIT_PROC, _CID_FLOWSHEET)
    log.info("Found %d unit procedures: %s", len(proc_names), proc_names)

    raw_units: list[dict] = []
    for proc_name in proc_names:
        equip_name = _get_up_var(doc, proc_name, _VID_UP_EQUIP_NAME) or proc_name
        oper_type  = _get_up_var(doc, proc_name, _VID_UP_OPER_TYPE)  or "Unknown"

        purchase_cost = _get_equip_var(doc, equip_name, _VID_EQUIP_PC)
        equip_type    = _get_equip_var(doc, equip_name, _VID_EQUIP_TYPE)
        volume        = _get_equip_var(doc, equip_name, _VID_EQUIP_VOLUME)
        diameter      = _get_equip_var(doc, equip_name, _VID_EQUIP_DIAMETER)
        no_units      = _get_equip_var(doc, equip_name, _VID_NO_UNITS)

        log.debug("Unit %s: equip=%s type=%s pc=$%s", proc_name, equip_name, oper_type, purchase_cost)

        unit_dict: dict[str, Any] = {
            "id":             proc_name,
            "unit_type":      oper_type,
            "equipment_name": equip_name,
            "equipment_type": equip_type,
        }

        if isinstance(purchase_cost, (int, float)):
            unit_dict["purchase_costs"] = {"equipment_purchase_cost": float(purchase_cost)}

        design = {}
        if isinstance(volume, (int, float)):
            design["volume_m3"] = float(volume)
        if isinstance(diameter, (int, float)):
            design["diameter_m"] = float(diameter)
        if isinstance(no_units, (int, float)):
            design["number_of_units"] = float(no_units)
        if design:
            unit_dict["design_results"] = design

        raw_units.append(unit_dict)

    return raw_units


# ── Streams ────────────────────────────────────────────────────────────────────

def extract_streams(doc) -> list[dict]:
    stream_names = _enumerate_items(doc, _LID_STREAM, _CID_FLOWSHEET)
    log.info("Found %d streams", len(stream_names))

    raw_streams: list[dict] = []
    for stream_name in stream_names:
        source = _get_stream_var(doc, stream_name, _VID_SOURCE_PROC) or "None"
        dest   = _get_stream_var(doc, stream_name, _VID_DEST_PROC)   or "None"

        mass_flow = _get_stream_var(doc, stream_name, _VID_MASS_FLOW)
        vol_flow  = _get_stream_var(doc, stream_name, _VID_VOL_FLOW)
        temp      = _get_stream_var(doc, stream_name, _VID_TEMPERATURE)
        pressure  = _get_stream_var(doc, stream_name, _VID_PRESSURE)

        comp_names  = _enumerate_items(doc, _LID_PURE_COMP, _CID_STREAM, stream_name)
        composition = []
        for comp_name in comp_names:
            mol_frac  = _get_stream_var(doc, stream_name, _VID_COMP_MOLE_FRAC, comp_name)
            mass_frac = _get_stream_var(doc, stream_name, _VID_COMP_MASS_FRAC, comp_name)

            # Only treat COM mole fraction as a true mol fraction; do not silently
            # substitute mass fraction into the "mol_fraction" field.
            if isinstance(mol_frac, (int, float)):
                mol_fraction = float(mol_frac)
            else:
                mol_fraction = 0.0
                if isinstance(mass_frac, (int, float)):
                    # Preserve available mass-fraction information without
                    # mislabeling it as a mol fraction.
                    log.warning(
                        "Stream %s component %s has mass fraction %r but no mole "
                        "fraction; exposing mass fraction separately and setting "
                        "mol_fraction=0.0.",
                        stream_name,
                        comp_name,
                        mass_frac,
                    )

            comp_entry = {
                "component_name": comp_name,
                "mol_fraction":   mol_fraction,
                "phase":          "l",
            }

            if isinstance(mass_frac, (int, float)):
                comp_entry["mass_fraction"] = float(mass_frac)

            composition.append(comp_entry)
        is_input  = _get_stream_var(doc, stream_name, _VID_STREAM_IS_INPUT)  \
                    if _VID_STREAM_IS_INPUT  is not None else None
        is_output = _get_stream_var(doc, stream_name, _VID_STREAM_IS_OUTPUT) \
                    if _VID_STREAM_IS_OUTPUT is not None else None
        is_raw    = _get_stream_var(doc, stream_name, _VID_STREAM_IS_RAW_MATERIAL) \
                    if _VID_STREAM_IS_RAW_MATERIAL is not None else None
        is_waste  = _get_stream_var(doc, stream_name, _VID_STREAM_IS_WASTE)  \
                    if _VID_STREAM_IS_WASTE  is not None else None

        if is_waste:
            stream_type = "waste"
        elif is_input or is_raw:
            stream_type = "feed"
        elif is_output:
            stream_type = "product"
        elif str(source) == "None":
            stream_type = "feed"
        elif str(dest) == "None":
            stream_type = "product"
        else:
            stream_type = "internal"

        price_val: float | None = None
        if stream_type in ("feed", "waste", "internal"):
            for price_vid in (_VID_STREAM_PRICE, _VID_STREAM_PURCHASING_PRICE):
                if price_vid is None:
                    continue
                pv = _get_stream_var(doc, stream_name, price_vid)
                if isinstance(pv, (int, float)) and pv > 0:
                    price_val = float(pv)
                    break

        stream_dict: dict[str, Any] = {
            "id":               stream_name,
            "source_unit_id":   str(source),
            "sink_unit_id":     str(dest),
            "stream_type":      stream_type,
            "stream_description": "",
            "stream_properties": {
                "total_mass_flow":       _qty(mass_flow, "kg/h"),
                "total_volumetric_flow": _qty(vol_flow,  "m3/h"),
                "temperature":           _qty(temp,      "K"),
                "pressure":              _qty(pressure,  "Pa"),
                "composition":           composition,
            },
        }
        if price_val is not None:
            stream_dict["price"] = {"value": price_val, "units": "USD/kg"}

        raw_streams.append(stream_dict)

        log.debug("Stream %s: mass_flow=%s source=%s dest=%s comps=%d",
                  stream_name, mass_flow, source, dest, len(composition))

    return raw_streams


# ── Utilities ──────────────────────────────────────────────────────────────────

def extract_utilities(doc) -> dict:
    """SuperPro COM v14 doesn't expose utility definitions via a simple enumeration."""
    return {"heat_utilities": [], "power_utilities": [], "other_utilities": []}


# ── Top-level entry point ──────────────────────────────────────────────────────

def extract_all(doc) -> dict:
    """
    Run all extractors against the open SuperPro document COM object.
    Returns a raw dict passed directly to _sff_translator.translate().
    """
    log.info("Extracting metadata...")
    metadata = extract_metadata(doc)

    log.info("Extracting unit operations...")
    units = extract_units(doc)

    log.info("Extracting streams...")
    streams = extract_streams(doc)

    log.info("Extracting utilities...")
    utilities = extract_utilities(doc)

    log.info("Extraction complete: %d units, %d streams", len(units), len(streams))

    return {
        "metadata": metadata,
        "units":    units,
        "streams":  streams,
        "utilities": utilities,
    }
