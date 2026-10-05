"""
============================================================
FLOOD PIPELINE — CONFIGURATION
============================================================

Same shape as the wind pipeline's config.py, so anything you learned there
applies here.

Two ways to configure, in order of precedence:

  1) Environment variables (used by Docker / CI). If a FLOOD_* variable is set,
     it wins. This lets you change country / paths / years WITHOUT editing code.
  2) The defaults in the "EDIT THESE" section below (used for a plain local run).

All relative paths are resolved relative to THIS file, so it does not matter
which folder you launch from.

Environment variables (all optional):
  FLOOD_COUNTRY           e.g. Canada
  FLOOD_COUNTRIES         "Canada,UK" or "all"
  FLOOD_HAZARDS           "riverine,coastal"
  FLOOD_SHAPEFILE         absolute path to the .shp
  FLOOD_DATA_ROOT         base folder for all data (Docker: /data)
  FLOOD_OUTPUT_ROOT       absolute path for this country's output
  FLOOD_YEARS             "2030,2050,2080"
  FLOOD_SCENARIOS         "rcp4p5,rcp8p5"
  FLOOD_RPS               "rp0002,rp0005,..."   (canonical 4-digit form)
  FLOOD_DOWNLOAD_WORKERS  6
  FLOOD_PROCESS_WORKERS   2
  FLOOD_KEEP_INTERMEDIATE 1 to keep the pre-COG GeoTIFFs
"""

import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _resolve(path):
    """Absolute path, relative to this file if not already absolute."""
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(_HERE, path))


def _env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_int(name, default):
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else default


def _env_list_str(name, default):
    v = os.environ.get(name)
    if v in (None, ""):
        return default
    return [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]


def _env_bool(name, default):
    v = os.environ.get(name)
    if v in (None, ""):
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ============================================================
# EDIT THESE  (defaults for a plain local run)
# ============================================================

# 1) Which country to process. FOR MOST RUNS THIS IS THE ONLY THING YOU SET.
#    The boundary shapefile is downloaded automatically (step 00) and its path
#    is derived from this name (see SHAPEFILE below) -- you do not type a path.
COUNTRY = _env("FLOOD_COUNTRY", "USA")

# 1b) Process SEVERAL countries in one run.
#     FLOOD_COUNTRIES="Canada,UK"  -> that list
#     FLOOD_COUNTRIES="all"        -> every registered country
#     unset                        -> just the single COUNTRY above
#     The global Aqueduct rasters are downloaded ONCE and shared across all of
#     them (see RAW_DIR), so more countries does NOT mean re-downloading.
_countries_env = os.environ.get("FLOOD_COUNTRIES")

# 2) Which flood hazards to process.
#      riverine -> inunriver, 5 GCMs collapsed into an ensemble
#      coastal  -> inuncoast, 4 subsidence/percentile variants stacked
HAZARDS = _env_list_str("FLOOD_HAZARDS", ["riverine", "coastal"])

# 3) Which future years to process. Aqueduct publishes exactly these three.
YEARS = _env_list_str("FLOOD_YEARS", ["2030", "2050", "2080"])

# 4) Which emissions scenarios to process.
SCENARIOS = _env_list_str("FLOOD_SCENARIOS", ["rcp4p5", "rcp8p5"])

# 5) Which return periods, in the canonical 4-digit form used by this pipeline.
#    Aqueduct writes riverine as rp00002 and coastal as rp0002; both are
#    normalised to rp0002 here (see naming.py).
RPS = _env_list_str("FLOOD_RPS", [
    "rp0002", "rp0005", "rp0010", "rp0025",
    "rp0050", "rp0100", "rp0250", "rp0500", "rp1000",
])

# 6) Base folder for ALL data (downloads + outputs). Native default: next to
#    this file. Docker sets FLOOD_DATA_ROOT=/data (the mounted volume).
DATA_ROOT = _resolve(_env("FLOOD_DATA_ROOT", "."))

# 7) Shared raw Aqueduct downloads. ONE copy, reused by every country --
#    this is what makes multi-country runs not re-download.
RAW_DIR = _resolve(_env("FLOOD_RAW_DIR", os.path.join(DATA_ROOT, "raw_tif")))

# 8) Per-country outputs. Each country gets its own folder so they never mix.
OUTPUT_ROOT = _resolve(_env(
    "FLOOD_OUTPUT_ROOT",
    os.path.join(DATA_ROOT, "FloodData", COUNTRY),
))


# ============================================================
# HAZARD DETAILS  (safe to leave as-is)
# ============================================================

# How many riverine models a complete group should have. Derived from
# RIVERINE_MODELS further down (the probed token list), so the two can never
# drift apart. A group with a different count still processes, but warns.
# Override with FLOOD_RIVERINE_EXPECTED if you deliberately want a subset
# treated as complete.
#
# NOTE: this used to read FLOOD_RIVERINE_MODELS, which now holds the model
# TOKEN LIST -- setting that to a model name made this int() blow up.

# Coastal variants that become bands 1-4, in order.
COASTAL_SUBSIDENCE = _env_list_str("FLOOD_COASTAL_SUBSIDENCE", ["nosub", "wtsub"])

# Coastal sea-level-rise percentile variants, per the WRI data dictionary:
#     "0"          -> 95th percentile (Aqueduct's DEFAULT, worst case)
#     "0_perc_05"  ->  5th percentile
#     "0_perc_50"  -> 50th percentile
#
# Default is the two the legacy innuncoast_stack_tiffs.py consumed, which keeps
# the 4-band output unchanged. Note the legacy DOWNLOADER fetched all three and
# then ignored "0", so the 95th percentile never reached any output.
#
# To pull everything (recommended for a raw archive -- re-downloading later is
# the expensive part):
#     FLOOD_COASTAL_PERCENTILES=0,0_perc_05,0_perc_50
COASTAL_PERCENTILES = _env_list_str(
    "FLOOD_COASTAL_PERCENTILES", ["0_perc_05", "0_perc_50"])


# ============================================================
# BOUNDARY SHAPEFILE  (normally auto-derived -- you don't set this)
# ============================================================
#
# The filename each country's boundary shapefile ends up as, once step 00
# downloads and unzips it. This is what lets you set only COUNTRY: the path is
# looked up here, not typed by hand. The download source for each country lives
# in download_shapefile.py; the resulting filename lives here.
SHAPEFILE_BY_COUNTRY = {
    "USA":       "GIS Files/State/cb_2018_us_state_500k.shp",
    "Canada":    "Canada/lpr_000b21a_e.shp",
    "EU":        "EU/NUTS_RG_01M_2021_4326_LEVL_0.shp",
    "UK":        "UK/CTRY_DEC_2024_UK_BUC.shp",
    "Australia": "Australia/STE_2021_AUST_GDA2020.shp",
}

# The list of countries a single `run_all.py` will process.
if _countries_env and _countries_env.strip().lower() == "all":
    COUNTRIES = list(SHAPEFILE_BY_COUNTRY.keys())
elif _countries_env:
    COUNTRIES = [c.strip() for c in _countries_env.replace(";", ",").split(",")
                 if c.strip()]
else:
    COUNTRIES = [COUNTRY]

# Trim the entirely-blank border off each output (FLOOD_TRIM=1).
#
# OFF by default, so outputs match the original FloodRiverine/FloodCoastal
# scripts exactly. Turning it on does not change a single data value -- it only
# stops storing rows and columns in which every pixel is already NoData.
#
# This is safe in a way the old clip-box was not, because it MEASURES the
# rasterized mask instead of predicting from the shapefile's bounding box:
#
#   * a row/column is dropped only when it contains no data at all;
#   * the masked-pixel count is compared before and after, and any mismatch
#     abandons the trim and writes the full extent instead;
#   * territory on both sides of the antimeridian is handled with no special
#     case -- if the first and last columns both hold data (the USA, via
#     Alaska's Aleutians) there is nothing to trim horizontally and the full
#     width is kept automatically.
#
# For the USA that means the latitude border is removed but the full width
# stays: 43200x21600 -> ~43190x10310, about 2.1x fewer pixels, which roughly
# halves step 04's scratch requirement. Landlocked-in-longitude countries
# (Canada, UK, EU, Australia) trim in both directions and save far more.
TRIM_BLANK_BORDER = _env_bool("FLOOD_TRIM", False)

# NOTE: there is deliberately no bounding-box / clip-box setting here.
#
# The boundary shapefile is always used in full, and the mask is rasterized
# over the whole raster grid (see raster_utils). An earlier revision computed a
# read window from the boundary's bounding box to go ~10x faster. That is
# unsafe: a bounding box cannot wrap the antimeridian, so for the USA (Alaska's
# Aleutians sit on both sides of the dateline) it either degenerates to ~360
# degrees wide or, once "fixed" with a clip box, silently drops Guam, the
# Northern Marianas, American Samoa and the western Aleutians from the national
# boundary. Quietly shrinking a country's territory is not an acceptable
# tradeoff for speed. Do not reintroduce it.

# Base folder the boundary shapefiles live in (override with FLOOD_GIS_DIR).
GIS_DIR = _resolve(_env("FLOOD_GIS_DIR", os.path.join("..", "GIS Files")))

# Resolve the shapefile path: an explicit FLOOD_SHAPEFILE wins (for a custom or
# not-yet-registered country); otherwise it is derived from COUNTRY.
_shp_override = os.environ.get("FLOOD_SHAPEFILE")
_shp_rel = SHAPEFILE_BY_COUNTRY.get(COUNTRY)
if _shp_override:
    SHAPEFILE = _resolve(_shp_override)
elif _shp_rel:
    SHAPEFILE = os.path.join(GIS_DIR, *_shp_rel.split("/"))
else:
    # Unknown country and no override: leave empty so run_all / step 00 can
    # print a clear message instead of guessing a wrong path.
    SHAPEFILE = ""


# ============================================================
# ADVANCED (safe to leave as-is)
# ============================================================

# Step 01 download threads. The host sheds TLS connections under concurrency:
# a 16-worker run dropped 9 of 594 files with
# "SSL: UNEXPECTED_EOF_WHILE_READING". Retries now absorb that, so 16 is usable
# and roughly 3x faster, but 6-8 is gentler if you would rather not see errors.
DOWNLOAD_WORKERS = _env_int("FLOOD_DOWNLOAD_WORKERS", 6)

# Attempts per file before giving up, with exponential backoff between them.
# 403/404 are treated as permanent and never retried.
DOWNLOAD_RETRIES = _env_int("FLOOD_DOWNLOAD_RETRIES", 4)

# Step 02/03 CPU workers. Each worker holds one boundary mask covering the FULL
# raster grid -- one bool per pixel, so ~0.9 GB for the 43200x21600 Aqueduct
# grid -- plus the tile buffers. Budget roughly 1.5 GB per worker and size this
# to the machine:
#   16 GB RAM -> 2      32 GB RAM -> 4      64 GB RAM -> 8
# The default is 1 because the mask, not the tiles, dominates memory here.
PROCESS_WORKERS = _env_int("FLOOD_PROCESS_WORKERS", 1)

# Tile size for the tiled read/write loop.
TILE_SIZE = _env_int("FLOOD_TILE_SIZE", 1024)

# NOTE on disk space: step 04 builds each COG through an UNCOMPRESSED
# intermediate that rio-cogeo creates IN THE OUTPUT FOLDER. For the
# global-extent 5-band float32 outputs that is ~17 GB of scratch (~24 GB peak)
# per file, even though the finished COG is under 100 MB. There is no TMPDIR
# setting that moves it -- put DATA_ROOT itself on a drive with room.

# After a successful run, step 05 deletes the regenerable pre-COG GeoTIFFs,
# keeping the raw downloads, the shapefile, and the final COGs.
# Set FLOOD_KEEP_INTERMEDIATE=1 to keep everything instead.
KEEP_INTERMEDIATE = _env_bool("FLOOD_KEEP_INTERMEDIATE", False)

CRS = "EPSG:4326"

# ---- Where the raw Aqueduct rasters come from -------------------------------
#
# WORKING LOCATION (verified 2026-10-04):
#
#     https://aqueduct.wridata.org/AqueductFloods20/<filename>.tif
#
# Confirmed by fetching inunriver_rcp4p5_00000NorESM1-M_2030_rp00002.tif and
# getting 200 image/tiff with Content-Length 81419410 -- byte-for-byte the same
# size as the copy already in this repo -- plus matching TIFF magic bytes 49 49 2a 00 (little-endian TIFF).
#
# The OLD location, which the WRI docs, the community nismod/aqueduct tiffs.txt
# and the superseded FloodRiverine/FloodCoastal scripts all still point at:
#
#     https://wri-projects.s3.amazonaws.com/AqueductFloodTool/download/v2/
#
# returns 404, and the "AqueductFloodTool" prefix is gone from the wri-projects
# bucket entirely (its root lists only Aqueduct30/, Aqueduct30Backup/ and
# Aqueduct40/). Re-checked 2026-10-04: still dead. Don't point anything there.
#
# NOTE: the new host returns 403 on the directory itself, so there is NO index
# page to crawl. Filenames must be GENERATED from the dimensions below, which
# is what step 01 does by default.
#
# Step 01 picks its file list in this precedence order:
#
#   1) FLOOD_SKIP_DOWNLOAD=1 — use whatever is already in RAW_DIR, no network.
#   2) FLOOD_SOURCE_URLS     — a text file of .tif URLs or bare filenames, one
#                              per line ('#' comments allowed).
#   3) FLOOD_INDEX_URL       — crawl an index page for .tif links. Unset by
#                              default because no index exists; kept for a
#                              mirror that offers one.
#   4) generated from config — the default. Builds every filename from
#                              HAZARDS x SCENARIOS x YEARS x RPS (x models, or
#                              x subsidence x percentiles) and fetches those.
AQUEDUCT_BASE_URL = _env(
    "FLOOD_BASE_URL",
    "https://aqueduct.wridata.org/AqueductFloods20/",
)

# No default index page: the bucket directory is not listable (403).
AQUEDUCT_INDEX_URL = _env("FLOOD_INDEX_URL", "")

# Optional file listing .tif URLs or filenames, one per line ('#' comments ok).
SOURCE_URLS = _env("FLOOD_SOURCE_URLS", "")

# Set FLOOD_SKIP_DOWNLOAD=1 when RAW_DIR is already populated.
SKIP_DOWNLOAD = _env_bool("FLOOD_SKIP_DOWNLOAD", False)

# The five CMIP5 model tokens used in riverine filenames.
#
# These are PROBED, not guessed. Each was confirmed with a HEAD request against
# the live host on 2026-10-04. The zero-padding is NOT uniform -- it pads each
# model name out to exactly 14 characters, which is why the old
# innunriver_download_data.py DEFAULT_MODELS list ("00000GFDL-ESM2M",
# "00000IPSL-CM5A-LR", "00000MIROC5") produced URLs that do not exist.
#
# If a model 404s, re-probe rather than adjusting the padding by eye:
#   curl -sI -o /dev/null -w "%{http_code}" #     "$FLOOD_BASE_URL/inunriver_rcp4p5_<token>_2030_rp00002.tif"
RIVERINE_MODELS = _env_list_str("FLOOD_RIVERINE_MODELS", [
    "0000GFDL-ESM2M",
    "0000HadGEM2-ES",
    "00IPSL-CM5A-LR",
    "MIROC-ESM-CHEM",
    "00000NorESM1-M",
])

RIVERINE_EXPECTED_MODELS = _env_int("FLOOD_RIVERINE_EXPECTED", len(RIVERINE_MODELS))


# ============================================================
# Internal helpers
# ============================================================

def summary_lines():
    return [
        f"Countries   : {', '.join(COUNTRIES)}",
        f"Hazards     : {', '.join(HAZARDS)}",
        f"Years       : {', '.join(YEARS)}",
        f"Scenarios   : {', '.join(SCENARIOS)}",
        f"Return per. : {', '.join(RPS)}",
        f"Shared raw  : {RAW_DIR}",
        f"Shapefile   : {SHAPEFILE}  (used in full, nothing clipped)",
        f"Trim border : {'on — blank rows/cols dropped' if TRIM_BLANK_BORDER else 'off — full source extent'}",
        f"Output root : {OUTPUT_ROOT}",
    ]
