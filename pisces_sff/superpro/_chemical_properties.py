"""
chemical_properties.py
──────────────────────
Molecular weight lookup for chemical components extracted from SuperPro.

Two-tier approach:
  1. Fast local dictionary: 200+ common bioprocess chemicals, no network needed.
  2. PubChem REST batch query: fallback for anything not in the local dict,
     results cached to mw_cache.json in the platform user-cache directory.

Public entry point
──────────────────
  enrich_molar_flows(raw: dict) -> dict

Given the raw dict from data_extraction.extract_all(), adds a
'total_molar_flow' key to each stream's stream_properties when the
stream's mass flow and composition allow it to be computed.

MW units: g/mol.  Molar flow units: kmol/h.
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)


def _default_cache_path() -> str:
    """Return a user-writable path for the PubChem MW cache.

    Uses the platform-appropriate user cache directory:
    - Windows: ``%LOCALAPPDATA%\\pisces_sff``
    - macOS/Linux: ``~/.cache/pisces_sff``

    Falls back to a ``mw_cache.json`` file next to this module if the
    user-cache directory cannot be determined or created.
    """
    try:
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        else:
            base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
                os.path.expanduser("~"), ".cache"
            )
        cache_dir = os.path.join(base, "pisces_sff")
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, "mw_cache.json")
    except Exception:
        return os.path.join(os.path.dirname(__file__), "mw_cache.json")


# Cache file in a user-writable location (not next to the installed module)
_CACHE_PATH = _default_cache_path()

# PubChem PUG-REST batch endpoint (POST, returns JSON)
_PUBCHEM_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/property/MolecularWeight/JSON"
_PUBCHEM_TIMEOUT_S = 8
_PUBCHEM_MAX_BATCH = 50  # max names per single POST

# ── Local MW dictionary ────────────────────────────────────────────────────────
# Covers chemicals common in SuperPro Designer bioprocess models.
# Values are molecular weights in g/mol (from NIST / PubChem).
# Keys are lowercase, stripped of common SuperPro suffixes.
# "Composite" materials (seawater, biomass, etc.) are included with
# representative average values so the wizard can skip those streams.

_LOCAL_MW: dict[str, float] = {
    # ── Water / utility streams ──────────────────────────────────────────────
    "water":                18.015,
    "h2o":                  18.015,
    "distilled water":      18.015,
    "deionized water":      18.015,
    "process water":        18.015,
    "wash water":           18.015,
    "pure water":           18.015,
    "groundwater":          18.015,
    "seawater":             18.08,   # ≈ water; small salt effect (< 5%)
    "sea water":            18.08,
    "salt water":           18.08,
    "river water":          18.02,
    "tap water":            18.02,
    "cooling water":        18.015,
    "boiler water":         18.015,
    "return condensate":    18.015,
    "condensate":           18.015,
    "steam condensate":     18.015,
    "steam":                18.015,
    "low pressure steam":   18.015,
    "medium pressure steam":18.015,
    "high pressure steam":  18.015,
    # ── Air / gas streams ────────────────────────────────────────────────────
    "air":                  28.966,
    "dry air":              28.966,
    "compressed air":       28.966,
    "flue gas":             29.5,    # typical: ~75% N2 + ~15% CO2 + ~8% H2O + ~2% O2
    "fuel gas":             28.0,    # typical lean methane-rich flue
    "plant gas":            28.0,
    "biogas":               30.0,    # ~60% CH4 + 40% CO2
    "natural gas":          17.5,    # mostly methane
    "syngas":               22.0,    # CO + H2 mixture approx
    # ── C1 / small gases ─────────────────────────────────────────────────────
    "oxygen":               32.000,
    "o2":                   32.000,
    "nitrogen":             28.013,
    "n2":                   28.013,
    "hydrogen":              2.016,
    "h2":                    2.016,
    "carbon dioxide":       44.010,
    "co2":                  44.010,
    "carbon monoxide":      28.010,
    "co":                   28.010,
    "methane":              16.043,
    "ch4":                  16.043,
    "ammonia":              17.031,
    "nh3":                  17.031,
    "hydrogen sulfide":     34.082,
    "h2s":                  34.082,
    "sulfur dioxide":       64.066,
    "so2":                  64.066,
    "nitrous oxide":        44.013,
    "n2o":                  44.013,
    "ozone":                48.000,
    # ── Sugars ───────────────────────────────────────────────────────────────
    "glucose":             180.156,
    "dextrose":            180.156,
    "fructose":            180.156,
    "galactose":           180.156,
    "xylose":              150.130,
    "arabinose":           150.130,
    "sucrose":             342.297,
    "lactose":             342.297,
    "maltose":             342.297,
    "starch":              162.14,   # per anhydroglucose unit
    "cellulose":           162.14,
    "hemicellulose":       150.0,
    "lignin":              184.0,    # representative monolignol equivalent
    "sorbitol":            182.17,
    "xylitol":             152.15,
    # ── Alcohols ─────────────────────────────────────────────────────────────
    "methanol":             32.042,
    "ethanol":              46.069,
    "propanol":             60.096,
    "isopropanol":          60.096,
    "butanol":              74.122,
    "isobutanol":           74.122,
    "pentanol":             88.148,
    "glycerol":             92.094,
    "glycerin":             92.094,
    "ethylene glycol":      62.068,
    "propylene glycol":     76.094,
    "1,4-butanediol":       90.121,
    "1,3-propanediol":      76.095,
    # ── Organic acids ────────────────────────────────────────────────────────
    "acetic acid":          60.052,
    "formic acid":          46.026,
    "propionic acid":       74.079,
    "butyric acid":         88.106,
    "lactic acid":          90.079,
    "citric acid":         192.124,
    "succinic acid":       118.088,
    "fumaric acid":        116.073,
    "malic acid":          134.088,
    "oxalic acid":          90.035,
    "gluconic acid":       196.155,
    "glutaric acid":       132.115,
    "adipic acid":         146.141,
    "itaconic acid":       130.099,
    "levulinic acid":      116.116,
    "3-hydroxypropionic acid": 90.079,
    # ── Amino acids ──────────────────────────────────────────────────────────
    "alanine":              89.094,
    "arginine":            174.201,
    "asparagine":          132.119,
    "aspartic acid":       133.104,
    "cysteine":            121.158,
    "glutamine":           146.146,
    "glutamic acid":       147.130,
    "glycine":              75.032,
    "histidine":           155.156,
    "isoleucine":          131.174,
    "leucine":             131.174,
    "lysine":              146.188,
    "methionine":          149.208,
    "phenylalanine":       165.191,
    "proline":             115.132,
    "serine":              105.093,
    "threonine":           119.120,
    "tryptophan":          204.228,
    "tyrosine":            181.191,
    "valine":              117.147,
    # ── Inorganic chemicals ───────────────────────────────────────────────────
    "sulfuric acid":        98.079,
    "h2so4":                98.079,
    "hydrochloric acid":    36.461,
    "hcl":                  36.461,
    "nitric acid":          63.013,
    "phosphoric acid":      97.994,
    "sodium hydroxide":     39.997,
    "naoh":                 39.997,
    "caustic soda":         39.997,
    "potassium hydroxide":  56.106,
    "koh":                  56.106,
    "calcium hydroxide":    74.093,
    "ammonium sulfate":    132.141,
    "ammonium hydroxide":   35.046,
    "ammonium phosphate":  115.026,
    "diammonium phosphate":132.057,
    "dap":                 132.057,
    "phosphate (dap)":     132.057,
    "urea":                 60.055,
    "sodium chloride":      58.440,
    "nacl":                 58.440,
    "sodium sulfate":      142.042,
    "potassium chloride":   74.551,
    "kcl":                  74.551,
    "potassium nitrate":   101.103,
    "kno3":                101.103,
    "nitrate":             101.103,   # typically KNO3 in SuperPro models
    "calcium chloride":    110.983,
    "cacl2":               110.983,
    "magnesium sulfate":   120.366,
    "mgso4":               120.366,
    "ferrous sulfate":     151.908,
    "feso4":               151.908,
    "ferric chloride":     162.204,
    "fecl3":               162.204,
    "lime":                 56.077,   # CaO
    "calcium oxide":        56.077,
    "quicklime":            56.077,
    "slaked lime":          74.093,
    "gypsum":              172.172,   # CaSO4·2H2O
    "chalk":               100.087,   # CaCO3
    "sodium bicarbonate":   84.007,
    "sodium carbonate":    105.989,
    "soda ash":            105.989,
    "potassium carbonate": 138.206,
    # ── Fertilizers / agrochemicals ───────────────────────────────────────────
    "anhydrous ammonia":    17.031,
    "map":                 115.026,   # monoammonium phosphate
    "monoammonium phosphate": 115.026,
    "triple superphosphate": 152.060,
    "ammonium nitrate":     80.043,
    "calcium nitrate":     164.088,
    # ── Solvents ─────────────────────────────────────────────────────────────
    "hexane":               86.177,
    "n-hexane":             86.177,
    "make-up hexane":       86.177,
    "heptane":             100.203,
    "octane":              114.230,
    "benzene":              78.113,
    "toluene":              92.140,
    "xylene":              106.167,
    "acetone":              58.080,
    "methyl ethyl ketone":  72.107,
    "diethyl ether":        74.123,
    "chloroform":          119.378,
    "dichloromethane":      84.933,
    "ethyl acetate":        88.106,
    "dmso":                 78.133,
    "dmf":                  73.095,
    # ── Lipids / biodiesel ────────────────────────────────────────────────────
    "fatty acids":         280.0,    # representative (C18)
    "palmitic acid":       256.424,
    "stearic acid":        284.477,
    "oleic acid":          282.461,
    "linoleic acid":       280.445,
    "triglyceride":        885.0,    # representative glyceryl trioleate
    "biodiesel":           296.5,    # FAME, representative methyl oleate
    "fame":                296.5,
    "glycerides":          885.0,
    "algal oil":           860.0,    # approximate
    "vegetable oil":       875.0,
    "crude oil":           350.0,    # representative
    "hexadecane":          226.441,  # cetane
    "decane":              142.282,
    # ── Polymers / flocculants ────────────────────────────────────────────────
    # Polymers don't have a single MW; use the repeat-unit MW as a proxy
    "polyacrylamide":       71.08,   # repeat unit MW
    "flocculant":           71.08,   # polyacrylamide, common in SuperPro
    "chitosan":            161.16,   # repeat unit MW
    "polylactic acid":      72.063,  # repeat unit MW (PLA)
    "pla":                  72.063,
    "polyhydroxybutyrate":  86.09,   # PHB repeat unit MW
    "phb":                  86.09,
    "polyethylene":         28.053,  # repeat unit MW (PE)
    "polypropylene":        42.080,  # repeat unit MW (PP)
    "cellulose acetate":   263.25,   # repeat unit MW
    # ── Biomass components ────────────────────────────────────────────────────
    "biomass":             180.0,    # dry cell weight surrogate (approx)
    "dry biomass":         180.0,
    "cell mass":           180.0,
    "algae":               180.0,
    "microalgae":          180.0,
    "yeast":               180.0,
    "bacteria":            180.0,
    "protein":             110.0,    # representative amino acid average
    "lipid":               860.0,    # representative triglyceride
    "ash":                  40.0,    # inorganic, representative
    "carbohydrate":        162.14,   # approximate glucose polymer unit
    # Lignocellulosic biomass polymer components (NREL Ethanol model naming)
    "glucan":              162.14,   # cellulose repeat unit (anhydroglucose)
    "xylan":               132.12,   # hemicellulose xylose repeat unit
    "arabinan":            132.12,   # arabinose polymer repeat unit
    "galactan":            162.14,   # galactose polymer repeat unit
    "mannan":              162.14,   # mannose polymer repeat unit
    "acetate":              60.052,  # acetic acid / acetyl groups in hemicellulose
    "extractives":         190.0,    # lipids + waxes mixture proxy
    "cellulose":           162.14,   # same as glucan
    "corn stover":         160.0,    # lignocellulosic mixture proxy
    "corn fiber":          162.14,   # glucan-rich corn processing residue
    "corn steep liquor":   180.0,    # nutrient medium, glucose proxy
    "thin stillage":        18.02,   # mostly water after distillation
    "whole stillage":       18.02,   # mostly water + suspended solids
    "wet distillers grain": 180.0,   # biomass residue proxy
    "distillers grain":    180.0,
    "enzyme":              50000.0,  # cellulase enzyme complex proxy
    "cellulase enzyme":    50000.0,
    "slaked lime":          74.093,  # calcium hydroxide (Ca(OH)2)
    "calcium hydroxide":    74.093,
    "syrup":               180.16,   # glucose/fructose mixture proxy
    "condensate":           18.015,  # mostly water
    "boiler feed water":    18.015,
    "make-up water":        18.015,
    "process water":        18.015,
    "cooling water":        18.015,
    "wastewater":           18.015,
    "effluent":             18.015,
    "wwt effluent":         18.015,
    "flue gas":             29.5,    # already present; alt name
    "biogas":               30.0,    # already present; alt name
    "lignin residue":      180.16,
    "corn stover feed":    160.0,
    # ── Process intermediates ─────────────────────────────────────────────────
    "ethylene":             28.053,
    "propylene":            42.081,
    "acetaldehyde":         44.053,
    "acrolein":             56.063,
    "formaldehyde":         30.026,
    "acetonitrile":         41.052,
    "hydrogen peroxide":    34.015,
    "h2o2":                 34.015,
    "chlorine":             70.906,
    "sodium hypochlorite":  74.442,
    "ozone":                48.000,
    # ── SuperPro generic names ────────────────────────────────────────────────
    "solvent":              86.18,   # default: hexane proxy
    "acid":                 60.052,  # default: acetic acid proxy
    "base":                 39.997,  # default: NaOH proxy
    "salt":                 58.440,  # default: NaCl proxy
    "nutrient":             60.055,  # default: urea proxy
    "chemical":             100.0,   # unknown — generic approximation
    "unknown":              100.0,

    # ── Pharmaceuticals & APIs ────────────────────────────────────────────────
    "penicillin g":             334.39,
    "penicillin v":             350.39,
    "ampicillin":               349.41,
    "amoxicillin":              365.41,
    "erythromycin":             733.93,
    "tetracycline":             444.44,
    "doxycycline":              444.44,
    "streptomycin":             581.57,
    "vancomycin":              1449.26,
    "aspirin":                  180.16,
    "acetylsalicylic acid":     180.16,
    "ibuprofen":                206.28,
    "paracetamol":              151.16,
    "acetaminophen":            151.16,
    "caffeine":                 194.19,
    "metformin":                129.16,
    "simvastatin":              418.57,
    "atorvastatin":             558.64,
    "vitamin c":                176.12,
    "ascorbic acid":            176.12,
    "l-ascorbic acid":          176.12,
    "folic acid":               441.40,
    "niacin":                   123.11,
    "riboflavin":               376.36,
    "thiamine":                 265.36,
    "pyridoxine":               169.18,
    "pyridoxal phosphate":      247.14,
    "biotin":                   244.31,
    "pantothenic acid":         219.23,
    # Large biologics — use molecular weight; molar flows will be very small
    "insulin":                 5808.0,
    "monoclonal antibody":    145000.0,
    "mab":                    145000.0,
    "immunoglobulin g":       150000.0,
    "igg":                    150000.0,
    "albumin":                 66430.0,
    "human serum albumin":     66430.0,
    "hsa":                     66430.0,
    "tpa":                     59047.0,  # tissue plasminogen activator
    "erythropoietin":          30400.0,
    "epo":                     30400.0,
    "interferon alpha":        19000.0,
    "interferon beta":         23000.0,
    "granulocyte colony stimulating factor":  18800.0,
    "g-csf":                   18800.0,
    "semaglutide":              4114.0,
    "glucagon like peptide":    3298.0,
    "glp-1":                    3298.0,
    # Nucleotides / nucleic acids
    "mrna":                   330000.0,  # rough proxy for average mRNA
    "dna":                    330000.0,  # rough proxy
    "plasmid dna":           3000000.0,  # typical plasmid ~9 kbp
    "atp":                      507.18,
    "adp":                      427.20,
    "amp":                      347.22,
    "nad":                      663.43,
    "nadh":                     665.44,

    # ── Enzymes (repeat-unit or characteristic MW proxies) ────────────────────
    "cellulase":               50000.0,
    "hemicellulase":           35000.0,
    "amylase":                 55000.0,
    "alpha-amylase":           55000.0,
    "glucoamylase":            70000.0,
    "protease":                25000.0,
    "lipase":                  38000.0,
    "laccase":                 64000.0,
    "xylanase":                22000.0,
    "pectinase":               35000.0,
    "invertase":               60000.0,
    "lactase":                135000.0,
    "lysozyme":                14307.0,
    "peroxidase":              44000.0,
    "catalase":               240000.0,
    "glucose oxidase":         80000.0,

    # ── Food / dairy ──────────────────────────────────────────────────────────
    "whey protein":            15000.0,
    "whey protein concentrate": 15000.0,
    "whey protein isolate":    15000.0,
    "casein":                  23600.0,
    "beta-lactoglobulin":      18300.0,
    "alpha-lactalbumin":       14200.0,
    "lactalbumin":             14200.0,
    "gelatin":                 80000.0,
    "collagen":               300000.0,
    "pectin":                  50000.0,
    "inulin":                   5100.0,
    "guar gum":               220000.0,
    "xanthan gum":           1000000.0,
    "carrageenan":            400000.0,
    "agar":                   120000.0,
    "starch (polymer)":       500000.0,  # repeat-unit ladder; practical proxy
    "amylose":                500000.0,
    "amylopectin":           1000000.0,
    "dextrin":                 50000.0,
    "dextrose":                  180.16,  # same as glucose
    "lactose":                   342.30,
    "trehalose":                 342.30,
    "raffinose":                 504.44,
    "maltose":                   342.30,
    "maltodextrin":             1000.0,   # typical 6-7 DP unit
    "fructooligosaccharide":     504.44,

    # ── Nutraceuticals & vitamins ─────────────────────────────────────────────
    "vitamin b12":             1355.37,
    "cyanocobalamin":          1355.37,
    "vitamin d3":               384.64,
    "cholecalciferol":          384.64,
    "vitamin e":                430.71,
    "alpha-tocopherol":         430.71,
    "tocopherol":               430.71,
    "vitamin k1":               450.70,
    "phylloquinone":            450.70,
    "beta-carotene":            536.87,
    "lycopene":                 536.87,
    "lutein":                   568.87,
    "zeaxanthin":               568.87,
    "astaxanthin":              596.84,
    "dha":                      328.49,  # docosahexaenoic acid
    "epa":                      302.45,  # eicosapentaenoic acid
    "omega-3":                  328.49,  # approximate DHA proxy
    "fish oil":                 860.0,   # triglyceride proxy
    "algal oil":                860.0,   # triglyceride proxy (already listed; kept for synonyms)
    "coenzyme q10":             863.34,
    "coq10":                    863.34,

    # ── Metals / inorganic compounds ──────────────────────────────────────────
    "lithium carbonate":         73.89,
    "li2co3":                    73.89,
    "lithium hydroxide":         23.95,
    "lioh":                      23.95,
    "lithium chloride":          42.39,
    "licl":                      42.39,
    "lithium sulfate":          109.94,
    "cobalt sulfate":           155.00,
    "cobalt chloride":          129.84,
    "nickel sulfate":           154.75,
    "nickel chloride":          129.60,
    "manganese sulfate":        151.00,
    "manganese dioxide":         86.94,
    "copper sulfate":           159.61,
    "zinc sulfate":             161.47,
    "zinc oxide":                81.38,
    "aluminum sulfate":         342.15,
    "aluminum hydroxide":        78.00,
    "aluminum oxide":           101.96,
    "alumina":                  101.96,
    "iron sulfate":             151.91,
    "ferrous sulfate":          151.91,
    "ferric chloride":          162.20,
    "ferric sulfate":           399.88,
    "titanium dioxide":          79.87,
    "sodium carbonate":         105.99,
    "na2co3":                   105.99,
    "sodium bicarbonate":        84.01,
    "nahco3":                    84.01,
    "sodium sulfate":           142.04,
    "na2so4":                   142.04,
    "sodium sulfite":           126.04,
    "sodium thiosulfate":       158.11,
    "potassium carbonate":      138.21,
    "k2co3":                    138.21,
    "potassium chloride":        74.55,
    "kcl":                       74.55,
    "potassium sulfate":        174.26,
    "potassium hydroxide":       56.11,
    "koh":                       56.11,
    "calcium carbonate":        100.09,
    "caco3":                    100.09,
    "calcium chloride":         110.98,
    "cacl2":                    110.98,
    "calcium sulfate":          136.14,
    "gypsum":                   172.17,
    "magnesium sulfate":        120.37,
    "mgso4":                    120.37,
    "magnesium hydroxide":       58.32,
    "magnesium carbonate":       84.31,
    "ammonium sulfate":         132.14,
    "nh4so4":                   132.14,
    "ammonium chloride":         53.49,
    "nh4cl":                     53.49,
    "ammonium nitrate":          80.04,
    "urea":                      60.055,
    "diammonium phosphate":     132.06,
    "dap":                      132.06,
    "monoammonium phosphate":   115.03,
    "map":                      115.03,
    # Rare earth oxides
    "lanthanum oxide":          325.82,
    "la2o3":                    325.82,
    "cerium oxide":             328.24,
    "ceo2":                     172.12,
    "neodymium oxide":          336.48,
    "nd2o3":                    336.48,
    "praseodymium oxide":       329.82,
    "dysprosium oxide":         373.00,
    "yttrium oxide":            225.81,
    "y2o3":                     225.81,

    # ── Additional solvents & reagents ────────────────────────────────────────
    "tetrahydrofuran":           72.11,
    "thf":                       72.11,
    "n-methyl-2-pyrrolidone":    99.13,
    "nmp":                       99.13,
    "diethylene glycol":        106.12,
    "deg":                      106.12,
    "triethylene glycol":       150.17,
    "teg":                      150.17,
    "1,4-dioxane":               88.11,
    "dioxane":                   88.11,
    "dimethyl sulfoxide":        78.13,
    "dmso":                      78.13,
    "dimethylformamide":         73.09,
    "dmf":                       73.09,
    "n,n-dimethylformamide":     73.09,
    "chloroform":               119.38,
    "dichloromethane":           84.93,
    "methylene chloride":        84.93,
    "diethyl ether":             74.12,
    "isopropyl ether":          102.18,
    "n-butyl acetate":          116.16,
    "ethyl lactate":            118.13,
    "dimethyl carbonate":        90.08,
    "methyl tert-butyl ether":   88.15,
    "mtbe":                      88.15,
    "carbon disulfide":          76.13,
    "cs2":                       76.13,
    "1-butanol":                 74.12,
    "2-butanol":                 74.12,
    "isobutanol":                74.12,
    "n-pentanol":                88.15,
    "2-propanol":                60.10,
    "cyclohexane":               84.16,
    "cyclohexanol":             100.16,
    "toluene":                   92.14,
    "xylene":                   106.17,
    "benzene":                   78.11,
    "styrene":                  104.15,
    "anisole":                  108.14,

    # ── Biofuel intermediates ─────────────────────────────────────────────────
    "biodiesel":                297.48,   # methyl oleate proxy
    "fame":                     297.48,   # fatty acid methyl ester proxy
    "fatty acid":               256.42,   # palmitic acid proxy
    "palmitic acid":            256.42,
    "oleic acid":               282.46,
    "linoleic acid":            280.45,
    "stearic acid":             284.48,
    "methyl oleate":            296.49,
    "methyl palmitate":         270.45,
    "glycerol":                  92.094,
    "glycerin":                  92.094,
    "levulinic acid":           116.12,
    "hydroxymethylfurfural":    126.11,
    "hmf":                      126.11,
    "furfural":                  96.08,
    "furfuryl alcohol":          98.10,
    "sorbitol":                 182.17,
    "xylitol":                  152.15,
    "butanol":                   74.12,
    "1-butanol":                 74.12,
    "2,3-butanediol":            90.12,
    "bdo":                       90.12,
    "1,4-butanediol":            90.12,
    "1,3-propanediol":           76.09,
    "propanediol":               76.09,
    "succinic acid":            118.09,
    "itaconic acid":            130.10,
    "muconic acid":             142.11,
    "vanillin":                 152.15,
    "ferulic acid":             194.18,
    "syringaldehyde":           182.17,
    "lignin":                   180.16,   # monolignol proxy (coniferyl alcohol)
    "lignosulfonate":           534.53,   # typical salt MW proxy
    "hemicellulose":            150.13,   # xylobiose proxy
    "xylose":                   150.13,
    "arabinose":                150.13,
    "rhamnose":                 164.16,
    "galactose":                180.16,
    "mannose":                  180.16,

    # ── Gases & light compounds ───────────────────────────────────────────────
    "syngas":                    20.0,    # proxy (mix of H2/CO)
    "flue gas":                  29.5,    # already present; kept for alt names
    "natural gas":               16.04,
    "biogas":                    30.0,    # already present
    "landfill gas":              30.0,
    "producer gas":              22.0,    # proxy
    "synthesis gas":             20.0,
    "hydrogen sulfide":          34.08,
    "h2s":                       34.08,
    "sulfur dioxide":            64.06,
    "so2":                       64.06,
    "sulfur trioxide":           80.06,
    "so3":                       80.06,
    "nitrogen oxide":            30.01,
    "nitric oxide":              30.01,
    "nitrogen dioxide":          46.01,
    "no2":                       46.01,
    "nitrous oxide":             44.01,
    "n2o":                       44.01,
    "carbon monoxide":           28.01,
    "co":                        28.01,
    "chlorine gas":              70.91,
    "cl2":                       70.91,
    "hydrochloric acid":         36.46,
    "hcl":                       36.46,
    "sulfuric acid":             98.08,
    "h2so4":                     98.08,
    "nitric acid":               63.01,
    "hno3":                      63.01,
    "phosphoric acid":           97.99,
    "h3po4":                     97.99,

    # ── SuperPro abbreviated / display names ──────────────────────────────────
    # These are truncated names as displayed in SuperPro Designer UI that
    # differ from the canonical chemical names above.
    "amm. sulfate":             132.14,   # ammonium sulfate
    "amm. nitrate":              80.04,   # ammonium nitrate
    "amm. phosphate":           132.06,   # diammonium phosphate proxy
    "carb. dioxide":             44.01,   # carbon dioxide
    "ethyl alcohol":             46.07,   # ethanol
    "isopropyl alcohol":         60.10,   # 2-propanol
    "ro water":                  18.015,  # reverse osmosis water
    "di water":                  18.015,  # deionized water
    "hydrolase":              50000.0,    # generic enzyme proxy
    "protease":               50000.0,
    "lipase":                 50000.0,
    "other solids":             180.0,    # organic solids proxy (glucose-like)
    "inert solids":              60.0,    # inorganic solids proxy
    "non-process solids":       180.0,
}


# ── Suffix stripping (mirrors molar-mass-db.ts logic) ────────────────────────

import re as _re

_SUFFIX_RE = _re.compile(
    r'\s*[\(\[](aq|l|g|s|liq|gas|sol|solid|vapor|vap|liquid)[\)\]]\s*$',
    _re.IGNORECASE,
)
_TRAILING_RE = _re.compile(r'[-_\s]+(L|G|S|liq|sol|gas)$', _re.IGNORECASE)


def _normalise(name: str) -> str:
    """Lowercase, strip SuperPro phase suffixes and extra whitespace."""
    n = name.strip()
    n = _SUFFIX_RE.sub("", n)
    n = _TRAILING_RE.sub("", n)
    return n.lower().strip()


# ── Cache I/O ─────────────────────────────────────────────────────────────────

def _load_cache() -> dict[str, Optional[float]]:
    if os.path.exists(_CACHE_PATH):
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(cache: dict[str, Optional[float]]) -> None:
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as exc:
        log.warning("Could not save MW cache: %s", exc)


# ── PubChem lookup ────────────────────────────────────────────────────────────

def _pubchem_batch(names: list[str]) -> dict[str, float]:
    """
    Query PubChem for molecular weights of a list of chemical names.
    Returns {name_lower: mw_g_mol} for resolved names only.
    """
    if not names:
        return {}
    results: dict[str, float] = {}

    # Per-name single queries (slower but reliable mapping)
    for name in names:
        encoded = urllib.parse.quote(name, safe="")
        url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{encoded}/property/MolecularWeight/JSON"
        )
        try:
            with urllib.request.urlopen(url, timeout=_PUBCHEM_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            props = data.get("PropertyTable", {}).get("Properties", [])
            if props:
                mw = float(props[0]["MolecularWeight"])
                results[name.lower()] = mw
                log.debug("PubChem: %s → %.3f g/mol", name, mw)
        except Exception as exc:
            log.debug("PubChem lookup failed for '%s': %s", name, exc)

    return results


# ── Public MW resolution ───────────────────────────────────────────────────────

def get_component_mws(names: list[str]) -> dict[str, Optional[float]]:
    """
    Return {name: mw_g_mol | None} for each requested name.
    Order: local dict → persistent cache → PubChem.
    """
    result: dict[str, Optional[float]] = {}
    need_pubchem: list[str] = []

    cache = _load_cache()

    for name in names:
        key = _normalise(name)

        # 1. Local dict (fastest, no I/O)
        if key in _LOCAL_MW:
            result[name] = _LOCAL_MW[key]
            continue

        # 2. Persistent cache
        if key in cache:
            result[name] = cache[key]  # may be None (previously not found)
            continue

        need_pubchem.append(name)

    # 3. PubChem for unknowns
    if need_pubchem:
        log.info("Querying PubChem for %d unknown chemicals: %s",
                 len(need_pubchem), need_pubchem)
        pubchem_hits = _pubchem_batch(need_pubchem)
        for name in need_pubchem:
            key = _normalise(name)
            mw = pubchem_hits.get(key) or pubchem_hits.get(name.lower())
            result[name] = mw
            cache[key] = mw  # cache None too (avoid repeat failed lookups)
        _save_cache(cache)

    return result


# ── Molar flow enrichment ──────────────────────────────────────────────────────

def enrich_molar_flows(raw: dict) -> dict:
    """
    Compute total_molar_flow (kmol/h) for each stream where possible and
    inject it into raw["streams"][i]["stream_properties"]["total_molar_flow"].

    Algorithm:
      1. Collect all unique component names across all streams.
      2. Batch-resolve their molecular weights (local dict + PubChem).
      3. For each stream with known mass flow and composition:
           avg_MW = 1 / Σ(mass_frac_i / MW_i)   [harmonic mean by mass]
           molar_flow = total_mass_flow_kg_h / avg_MW / 1000  [→ kmol/h]
      4. If any components are unresolved, skip that stream (don't guess).

    Streams that already have a non-zero total_molar_flow (from COM
    extraction via a future VarID) are not overwritten.
    """
    streams = raw.get("streams", [])

    # Collect unique component names
    all_comp_names: set[str] = set()
    for s in streams:
        for comp in s.get("stream_properties", {}).get("composition", []):
            name = comp.get("component_name")
            if name:
                all_comp_names.add(name)

    if not all_comp_names:
        return raw

    # Batch MW lookup
    mw_map = get_component_mws(list(all_comp_names))

    enriched_count = 0
    unresolved_names: set[str] = set()
    for s in streams:
        props = s.setdefault("stream_properties", {})

        # Don't overwrite a non-zero molar flow already set (e.g. from COM)
        existing = props.get("total_molar_flow", {})
        if existing and isinstance(existing.get("value"), (int, float)) and existing["value"] > 0:
            continue

        mass_flow_kg_h = (props.get("total_mass_flow") or {}).get("value")
        if not isinstance(mass_flow_kg_h, (int, float)) or mass_flow_kg_h <= 0:
            continue

        composition = props.get("composition", [])
        if not composition:
            continue

        # Build mass-fraction weighted sum
        # avg_MW = 1 / Σ(w_i / MW_i)  where w_i = mass fraction
        # If mole fractions provided instead, convert: need mass fractions
        # We'll use mole fractions as approximation when mass fracs unavailable.
        # Also sum of fracs may not be 1; normalise.

        frac_sum = sum(c.get("mol_fraction", 0.0) for c in composition)
        if frac_sum <= 0:
            continue

        weighted_sum = 0.0  # Σ (frac_i / MW_i)
        fully_resolved = True
        for comp in composition:
            cname = comp.get("component_name", "")
            frac  = comp.get("mol_fraction", 0.0) / frac_sum  # normalised mole frac

            mw = mw_map.get(cname)
            if mw is None or mw <= 0:
                fully_resolved = False
                unresolved_names.add(cname)
                break
            weighted_sum += frac / mw

        if not fully_resolved or weighted_sum <= 0:
            continue

        # avg_MW from mole fractions: Σ (x_i * MW_i)
        avg_mw_mole = sum(
            (c.get("mol_fraction", 0) / frac_sum) * mw_map[c["component_name"]]
            for c in composition
        )
        molar_flow_kmol_h = mass_flow_kg_h / avg_mw_mole

        props["total_molar_flow"] = {
            "value": round(molar_flow_kmol_h, 4),
            "units": "kmol/h",
        }
        enriched_count += 1
        log.debug(
            "Stream %s: avg_MW=%.2f g/mol  molar_flow=%.4f kmol/h",
            s.get("id", "?"), avg_mw_mole, molar_flow_kmol_h,
        )

    if unresolved_names:
        log.info(
            "Molar flow enrichment: unresolved components blocking %d streams: %s",
            len(streams) - enriched_count,
            sorted(unresolved_names),
        )
    log.info(
        "Molar flow enrichment: %d/%d streams computed",
        enriched_count, len(streams),
    )
    return raw
