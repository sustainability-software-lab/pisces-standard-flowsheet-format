"""
_unit_type_map.py
─────────────────
Maps SuperPro Designer unit operation type strings to SFF unit_type strings.

SuperPro uses its own internal type names (shown in the equipment icon/dialog).
SFF accepts free-form strings but the Project Pisces frontend uses known values.

This mapping should be expanded iteratively as real .spf files are processed.
Any unmapped type passes through with a logged warning so conversions never
fail due to an unknown unit type.
"""

# Maps SuperPro type string (case-insensitive) → SFF unit_type string
UNIT_TYPE_MAP: dict[str, str] = {
    # Bioreactors & fermentation
    "fermentor":                     "Fermentation",
    "fermentation":                  "Fermentation",
    "bioreactor":                    "Fermentation",
    "stirred tank reactor":          "Fermentation",
    "air lift reactor":              "Fermentation",

    # Distillation & absorption
    "distillation column":           "Distillation",
    "distillation":                  "Distillation",
    "absorption column":             "Absorption",
    "absorber":                      "Absorption",
    "stripper":                      "Stripping",

    # Heat exchangers
    "heat exchanger":                "HeatExchange",
    "heater":                        "HeatExchange",
    "cooler":                        "HeatExchange",
    "condenser":                     "HeatExchange",
    "reboiler":                      "HeatExchange",
    "evaporator":                    "Evaporation",
    "multi-effect evaporator":       "Evaporation",

    # Filtration & membranes
    "filter":                        "Filtration",
    "membrane filtration":           "Filtration",
    "microfiltration":               "Filtration",
    "ultrafiltration":               "Filtration",
    "nanofiltration":                "Filtration",
    "reverse osmosis":               "Filtration",
    "diafiltration":                 "Filtration",

    # Chromatography
    "chromatography":                "Chromatography",
    "ion exchange":                  "ChromatographyIonExchange",

    # Centrifugation & sedimentation
    "centrifuge":                    "Centrifugation",
    "disk centrifuge":               "Centrifugation",
    "tubular centrifuge":            "Centrifugation",
    "sedimentation":                 "Sedimentation",
    "decanter":                      "Decanting",

    # Drying
    "dryer":                         "Drying",
    "drying":                        "Drying",
    "spray dryer":                   "Drying",
    "freeze dryer":                  "Drying",

    # Mixing & agitation
    "mixer":                         "Mixing",
    "mixing":                        "Mixing",
    "blender":                       "Mixing",
    "agitator":                      "Mixing",

    # Reactors (non-biological)
    "reactor":                       "Reaction",
    "cstr":                          "Reaction",
    "pfr":                           "Reaction",
    "batch reactor":                 "Reaction",
    "continuous reactor":            "Reaction",

    # Crystallization
    "crystallizer":                  "Crystallization",
    "crystallization":               "Crystallization",

    # Storage & tanks
    "storage tank":                  "Storage",
    "tank":                          "Storage",
    "buffer tank":                   "Storage",
    "silo":                          "Storage",

    # Size reduction
    "grinder":                       "SizeReduction",
    "mill":                          "SizeReduction",
    "crusher":                       "SizeReduction",

    # Solid-liquid separation
    "press":                         "Pressing",
    "belt press":                    "Pressing",
    "screw press":                   "Pressing",

    # Utilities & power
    "boiler":                        "Boiler",
    "turbine":                       "Turbine",
    "turbogenerator":                "Turbogenerator",
    "compressor":                    "Compression",
    "pump":                          "Pumping",

    # Feed/product placeholders
    "feed":                          "FeedInput",
    "product":                       "ProductOutput",
    "waste":                         "WasteOutput",

    # Screening & classification
    "screen":                        "Screening",
    "magnetic separator":            "MagneticSeparation",
    "cyclone":                       "Cyclone",

    # Other
    "electrodialysis":               "Electrodialysis",
    "adsorption":                    "Adsorption",
    "extraction":                    "LiquidExtraction",
    "liquid-liquid extraction":      "LiquidExtraction",

    # SuperPro v14 operation type strings (VarID 12361, GetUPVarVal)
    "chlorination":                  "Reaction",
    "chlorination, salt formation":  "Reaction",
    "salt formation":                "Reaction",
    "oxidation":                     "Reaction",
    "reduction":                     "Reaction",
    "hydrolysis":                    "Reaction",
    "esterification":                "Reaction",
    "saponification":                "Reaction",
    "neutralization":                "Reaction",
    "sulfonation":                   "Reaction",
    "nitration":                     "Reaction",
    "polymerization":                "Reaction",
    "fermentation, aerobic":         "Fermentation",
    "fermentation, anaerobic":       "Fermentation",
    "cell growth":                   "Fermentation",
    "cell disruption":               "CellDisruption",
    "homogenization":                "Mixing",
    "precipitation":                 "Precipitation",
    "flocculation":                  "Flocculation",
    "liquid-liquid extraction, mixer-settler": "LiquidExtraction",
    "solids washing":                "Filtration",
    "cake washing":                  "Filtration",
    "dead-end filtration":           "Filtration",
    "cross-flow filtration":         "Filtration",
    "diafiltration/ultrafiltration": "Filtration",
    "size exclusion chromatography": "Chromatography",
    "affinity chromatography":       "Chromatography",
    "reversed phase chromatography": "Chromatography",
    "normal phase chromatography":   "Chromatography",
    "spray drying":                  "Drying",
    "freeze drying":                 "Drying",
    "rotary vacuum drying":          "Drying",
    "evaporation":                   "Evaporation",
    "flash evaporation":             "Evaporation",
    "steam distillation":            "Distillation",
    "vacuum distillation":           "Distillation",
    "fractional distillation":       "Distillation",
    "liquid-solid extraction":       "Extraction",
    "supercritical extraction":      "Extraction",
    "resin trapping":                "Adsorption",
    "size reduction":                "SizeReduction",
    "generic":                       "Generic",
    "bulk flow":                     "Generic",
    "blending":                      "Mixing",
    "in-place cleaning":             "CIP",
    "sterilization-in-place":        "SIP",
    "batch sterilization":           "Sterilization",
    "continuous sterilization":      "Sterilization",

    # Confirmed from real .spf files (ComEx1.spf test)
    "nutsche filtration":            "Filtration",
    "charcoal treatment":            "Adsorption",
    "condensation":                  "HeatExchange",
    "crystallization, evaporative":  "Crystallization",
    "crystallization, cooling":      "Crystallization",
    "deliquoring":                   "Filtration",
    "decantation":                   "Decanting",
    "solvent extraction":            "LiquidExtraction",
    "washing":                       "Filtration",
    "carbon adsorption":             "Adsorption",
    "ion exchange chromatography":   "ChromatographyIonExchange",
    "diatomaceous earth filtration": "Filtration",
    "membrane filtration, dead-end": "Filtration",
    "membrane filtration, cross-flow": "Filtration",

    # Confirmed from real .spf files (Ethanol_v14.spf)
    "grinding":                      "SizeReduction",
    "sieve":                         "Screening",
    "screw conveying":               "Generic",
    "fluid flow":                    "Pumping",
    "flash":                         "Generic",
    "heat exchanging":               "HeatExchange",
    "etoh dehydration":              "Reaction",
    "corn stover transportation":    "Generic",

    # Confirmed from real .spf files (Penicillin_v14.spf)
    "heat sterilization":            "Sterilization",
    "air filtration":                "Filtration",
    "biomass removal":               "Filtration",
    "cooling":                       "HeatExchange",
    "acidification":                 "Reaction",
    "basket centrifugation":         "Centrifugation",
    "storage":                       "Storage",
    "shake flask":                   "Fermentation",
    "flow splitting":                "Generic",
    "flow distribution":             "Generic",
    "solids storage":                "Storage",
    "air supply":                    "Generic",
    "glucose supply":                "FeedInput",

    # Confirmed from real .spf files (AlgalOil_v14.spf)
    "phosphate hopper":              "Storage",
    "citric acid addition":          "Reaction",
    "sonicator":                     "CellDisruption",
    "clarification":                 "Filtration",
    "algae growth":                  "Fermentation",
}


# ── Keyword rules (Tier 2) ───────────────────────────────────────────────────
#
# Evaluated after exact match, before substring match.
# Each entry is (keywords, sff_unit_type). Ordered most-specific first.
# A rule fires when ANY of its keywords is a substring of the normalised type.
#
KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["chromatograph", "hplc", "size exclusion", "affinity chrom",
      "reversed phase", "normal phase"],
     "Chromatography"),
    (["ion exchange chrom"], "ChromatographyIonExchange"),
    (["filtrat", "diafilt", "microfilt", "ultrafilt", "nanofilt",
      "reverse osmosis", "dead-end", "cross-flow", "membrane filtrat", "clarif"],
     "Filtration"),
    (["distill", "rectif"], "Distillation"),
    (["evaporat"], "Evaporation"),
    (["heat exchang", "heat transfer", "heat exchanging", "cooling", "chilling",
      "refrigerat", "heating", "condensat"],
     "HeatExchange"),
    (["drying", "dryer", "lyophil", "freeze-dry", "spray-dry",
      "freeze dry", "spray dry"],
     "Drying"),
    (["ferment", "bioreact", "cell culture", "cell growth",
      "microbial", "anaerobic digest", "algae growth", "algal growth"],
     "Fermentation"),
    (["react", "hydrolysi", "oxidat", "reduct", "chlorinat", "dehydrat",
      "nitrat", "polymer", "saponif", "esterif", "neutral", "sulfonat",
      "acidif", "pyrolysis", "gasif", "combustion"],
     "Reaction"),
    (["centrifug"], "Centrifugation"),
    (["crystalliz"], "Crystallization"),
    (["precipitat"], "Precipitation"),
    (["flocculat"], "Flocculation"),
    (["cell disrupt", "lysis", "bead mill", "sonicator", "sonication"], "CellDisruption"),
    (["adsorb", "carbon treat", "resin trap", "charcoal treat"], "Adsorption"),
    (["solid-liquid", "liquid-solid", "supercritical extract", "supercritical",
      "leach"],
     "Extraction"),
    (["liquid-liquid", "solvent extract", "liquid extract", "mixer-settler"],
     "LiquidExtraction"),
    (["steriliz", "autoclave", "pasteuriz"], "Sterilization"),
    (["clean-in-place", "in-place clean", "cip"], "CIP"),
    (["sterilization-in-place", "sip"], "SIP"),
    (["grind", "mill", "crush", "size reduc", "comminut", "pulveriz"],
     "SizeReduction"),
    (["sieve", "sieving", "screen", "classif"], "Screening"),
    (["mixing", "blending", "agitat", "homogen"], "Mixing"),
    (["stripping", "strip"], "Stripping"),
    (["absorpt", "absorb", "scrubbing", "scrub"], "Absorption"),
    (["decant", "sediment"], "Decanting"),
    (["pumping", "fluid flow", "liquid flow", "pressuri"], "Pumping"),
    (["compress", "blower"], "Compression"),
    (["storage", "storag", "silo", "hold tank", "warehouse", "hopper"], "Storage"),
    (["electrodialys", "electrod"], "Electrodialysis"),
    (["magnet"], "MagneticSeparation"),
    (["cyclone", "hydrocyclon"], "Cyclone"),
    (["transport", "convey", "trucking", "shipping", "haulage"], "Generic"),
    (["flash"], "Generic"),
    (["flow split", "flow distribut", "bulk flow"], "Generic"),
]


def map_unit_type(superpro_type: str) -> tuple[str, bool]:
    """
    Map a SuperPro unit type string to an SFF unit_type string.

    Returns:
        (sff_unit_type, is_known) where is_known=False means the type was
        not in the map and the raw SuperPro type was passed through.

    Matching tiers (first match wins):
      1. Exact match against UNIT_TYPE_MAP
      2. Keyword rules (KEYWORD_RULES) — ordered, most-specific first
      3. Substring match against UNIT_TYPE_MAP keys (legacy fallback)
    """
    normalized = superpro_type.strip().lower()

    if normalized in UNIT_TYPE_MAP:
        return UNIT_TYPE_MAP[normalized], True

    for keywords, sff_type in KEYWORD_RULES:
        if any(kw in normalized for kw in keywords):
            return sff_type, True

    for key, value in UNIT_TYPE_MAP.items():
        if key in normalized:
            return value, True

    return superpro_type, False
