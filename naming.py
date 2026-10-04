"""
Filename grammar for the Aqueduct flood rasters.

Everything downstream groups files by (scenario, year, return period), so all
of the parsing and normalising lives here rather than being re-implemented in
each step.

Source filenames
----------------
riverine : inunriver_{scenario}_{model}_{year}_{rp}.tif
           e.g. inunriver_rcp4p5_00000NorESM1-M_2030_rp00002.tif
coastal  : inuncoast_{scenario}_{subsidence}_{year}_{rp}_{projection}.tif
           e.g. inuncoast_rcp4p5_nosub_2030_rp0002_0_perc_05.tif

Note the two hazards zero-pad the return period differently (rp00002 vs
rp0002). Both are normalised to a canonical 4-digit rp0002 so a riverine and a
coastal output for "the 2-year flood" carry the same token.

Scenarios are normalised rcp4p5 -> rcp45 (the interior "p" is dropped, matching
how the wind pipeline writes ssp245).
"""

import os
import re

RIVERINE_PREFIX = "inunriver"
COASTAL_PREFIX = "inuncoast"

# Historical baselines are a different product (different scenario/year tokens)
# and are not part of the projected outputs this pipeline builds.
HISTORICAL_TOKENS = ("historical", "hist")

# The superseded FloodRiverine/FloodCoastal scripts wrote their ensemble next to
# the models it was built from, as inunriver_<scenario>_aggregated_<year>_<rp>.tif
# -- which has exactly the same token count as a real model file. If a raw folder
# still holds those, they must not be re-ingested as if "aggregated" were a GCM.
DERIVED_TOKENS = ("aggregated", "ensemble", "mean", "median", "stacked")


def normalize_scenario(scenario):
    """rcp4p5 -> rcp45, rcp8p5 -> rcp85. Anything else passes through lowered."""
    s = (scenario or "").strip().lower()
    m = re.fullmatch(r"rcp(\d)p(\d)", s)
    if m:
        return f"rcp{m.group(1)}{m.group(2)}"
    return s


def denormalize_scenario(scenario):
    """rcp45 -> rcp4p5. Inverse of normalize_scenario, for building source URLs."""
    s = (scenario or "").strip().lower()
    m = re.fullmatch(r"rcp(\d)(\d)", s)
    if m:
        return f"rcp{m.group(1)}p{m.group(2)}"
    return s


def normalize_rp(rp, width=4):
    """rp00002 / rp0002 / rp2 -> rp0002. Returns None if it isn't an rp token."""
    m = re.fullmatch(r"rp0*(\d+)", (rp or "").strip().lower())
    if not m:
        return None
    return "rp" + m.group(1).zfill(width)


def is_historical(name):
    low = os.path.basename(name).lower()
    return any(f"_{t}_" in low for t in HISTORICAL_TOKENS)


def is_derived(name):
    """True for a file some earlier run produced, rather than an Aqueduct source."""
    low = os.path.splitext(os.path.basename(name))[0].lower()
    return any(t in low.split("_") for t in DERIVED_TOKENS)


def parse_riverine(filename):
    """
    Parse a riverine source filename into its parts.

    Returns a dict with normalised scenario/rp, or None if the name does not
    match the expected 5-token grammar (which includes historical files and any
    aggregate this pipeline itself wrote).
    """
    name = os.path.basename(filename)
    stem, ext = os.path.splitext(name)
    if ext.lower() not in (".tif", ".tiff"):
        return None
    if not stem.lower().startswith(RIVERINE_PREFIX):
        return None
    if is_historical(stem) or is_derived(stem):
        return None

    parts = stem.split("_")
    if len(parts) != 5:
        return None

    _prefix, scenario, model, year, rp = parts
    rp_norm = normalize_rp(rp)
    if rp_norm is None or not re.fullmatch(r"\d{4}", year):
        return None

    return {
        "hazard": "riverine",
        "scenario": normalize_scenario(scenario),
        "scenario_raw": scenario,
        "model": model,
        "year": year,
        "rp": rp_norm,
        "rp_raw": rp,
        "filename": name,
    }


def parse_coastal(filename):
    """
    Parse a coastal source filename into its parts.

    The projection suffix itself contains underscores ("0_perc_05"), so it is
    rejoined from whatever follows the return period.
    """
    name = os.path.basename(filename)
    stem, ext = os.path.splitext(name)
    if ext.lower() not in (".tif", ".tiff"):
        return None
    if not stem.lower().startswith(COASTAL_PREFIX):
        return None
    if is_historical(stem) or is_derived(stem):
        return None

    parts = stem.split("_")
    if len(parts) < 6:
        return None

    _prefix, scenario, subsidence, year, rp = parts[:5]
    projection = "_".join(parts[5:])

    rp_norm = normalize_rp(rp)
    if rp_norm is None or not re.fullmatch(r"\d{4}", year):
        return None

    return {
        "hazard": "coastal",
        "scenario": normalize_scenario(scenario),
        "scenario_raw": scenario,
        "subsidence": subsidence,
        "year": year,
        "rp": rp_norm,
        "rp_raw": rp,
        "projection": projection,
        "filename": name,
    }


def parse(filename):
    """Parse either hazard, returning None if the name matches neither."""
    return parse_riverine(filename) or parse_coastal(filename)


def group_key(meta):
    """The (scenario, year, rp) tuple that becomes one multi-band output."""
    return (meta["scenario"], meta["year"], meta["rp"])


def output_name(hazard, scenario, year, rp):
    """Flat output filename, e.g. riverine_rcp45_2030_rp0002.tif"""
    return f"{hazard}_{scenario}_{year}_{rp}.tif"


# ---------------------------------------------------------------------------
# Building SOURCE filenames (for the download step)
# ---------------------------------------------------------------------------
#
# The two hazards zero-pad the return period to different widths on the server:
# riverine uses 5 digits (rp00002) and coastal 4 (rp0002). Probed 2026-10-04 --
# inuncoast_..._rp00002_... returns 403, so these widths are not interchangeable.
RIVERINE_RP_WIDTH = 5
COASTAL_RP_WIDTH = 4


def _rp_digits(rp_canonical, width):
    """'rp0002' -> 'rp00002' (width 5) or 'rp0002' (width 4)."""
    norm = normalize_rp(rp_canonical, width=width)
    if norm is None:
        raise ValueError(f"not a return-period token: {rp_canonical!r}")
    return norm


def riverine_source_name(scenario, model, year, rp):
    """
    inunriver_rcp4p5_00000NorESM1-M_2030_rp00002.tif

    `scenario` may be given canonical ('rcp45') or raw ('rcp4p5'); the raw form
    is what appears in the filename.
    """
    return (f"{RIVERINE_PREFIX}_{denormalize_scenario(scenario)}_{model}_"
            f"{year}_{_rp_digits(rp, RIVERINE_RP_WIDTH)}.tif")


def coastal_source_name(scenario, subsidence, year, rp, projection):
    """inuncoast_rcp4p5_nosub_2030_rp0002_0_perc_05.tif"""
    return (f"{COASTAL_PREFIX}_{denormalize_scenario(scenario)}_{subsidence}_"
            f"{year}_{_rp_digits(rp, COASTAL_RP_WIDTH)}_{projection}.tif")
