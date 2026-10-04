"""
Step 00 — Ensure the country boundary shapefile exists before the pipeline runs.

If the shapefile named by config.SHAPEFILE already exists, this does nothing.
Otherwise it looks up config.COUNTRY in the registry below and downloads +
unzips it from the official government / intergovernmental source.

Countries whose data sits behind account registration (some Gulf states) cannot
be automated — for those this prints exactly where to download it and where to
put it, then stops.

The registry mirrors the wind pipeline's (Wind/download_shapefile.py) so the
two can be containerised independently; when you add a country to one, add it
to the other. Source provenance: see Wind/docs/boundary-data-sources.md.

TESTED: none of these have been re-run from this copy — the shapefiles for USA,
Canada, EU, UK and Australia are already present under ../GIS Files, so the
download path is only exercised for a country you add.
"""

import os
import sys
import zipfile
import tempfile

import requests

import config


# =========================================================
# REGISTRY  (keyed by the COUNTRY value in config.py)
# =========================================================
# strategy:
#   "zip"        -> download a .zip, extract everything
#   "nested_zip" -> a .zip that itself contains .shp.zip files; extract the
#                   inner ones listed in "inner"
#   "arcgis"     -> resolve an ArcGIS Hub item id to a download url first
#   "gated"      -> registration required; print manual instructions
#   "manual"     -> no open download found; print manual instructions

SOURCES = {
    # ---- Confirmed direct government sources ----
    "USA": {  # US Census Bureau cartographic boundaries
        "strategy": "zip",
        "url": "https://www2.census.gov/geo/tiger/GENZ2018/shp/"
               "cb_2018_us_state_500k.zip",
        "source": "US Census Bureau, 2018 cartographic boundary (states, 1:500k)",
    },
    "Canada": {  # Statistics Canada
        "strategy": "zip",
        "url": "https://www12.statcan.gc.ca/census-recensement/2021/geo/"
               "sip-pis/boundary-limites/files-fichiers/lpr_000b21a_e.zip",
        "source": "Statistics Canada, 2021 Census (cartographic)",
    },
    "EU": {  # Eurostat GISCO NUTS 2021 (nested zip)
        "strategy": "nested_zip",
        "url": "https://gisco-services.ec.europa.eu/distribution/v2/nuts/"
               "download/ref-nuts-2021-01m.shp.zip",
        "inner": [
            "NUTS_RG_01M_2021_4326_LEVL_0.shp.zip",
            "NUTS_RG_01M_2021_4326_LEVL_1.shp.zip",
            "NUTS_RG_01M_2021_4326_LEVL_2.shp.zip",
            "NUTS_RG_01M_2021_4326_LEVL_3.shp.zip",
        ],
        "source": "Eurostat GISCO, NUTS 2021 1:1M",
    },
    "UK": {  # ONS Open Geography Portal (ArcGIS Hub)
        "strategy": "arcgis",
        "item_id": "06d13e0421784911aa669768f25dcb18",
        "source": "ONS, Countries (Dec 2024) Boundaries UK BUC",
    },
    "Australia": {  # ABS ASGS Edition 3
        "strategy": "zip",
        "url": "https://www.abs.gov.au/statistics/standards/"
               "australian-statistical-geography-standard-asgs/"
               "edition-3-july-2021-june-2026/access-and-downloads/"
               "digital-boundary-files/STE_2021_AUST_SHP_GDA2020.zip",
        "source": "ABS, States/Territories 2021 (GDA2020)",
    },

    # ---- Middle East: government-sourced via UN OCHA COD-AB (untested) ----
    "UAE": {"strategy": "zip", "url": "https://data.humdata.org/dataset/23d41c1f-41ef-4957-a47e-b8c08c984d83/resource/8e3dce55-76e9-4ea1-a4d9-93eccdc4b66d/download/are_admin_boundaries.shp.zip", "source": "UAE Federal Competitiveness and Statistics Centre (via COD-AB)"},
    "Qatar": {"strategy": "zip", "url": "https://data.humdata.org/dataset/6a84f3b8-41cd-4769-a61f-6dbd5f61bd05/resource/2345dd03-d14f-4038-9e96-8c235056d25f/download/qat_adm_psa_20240627_ab_shp.zip", "source": "Qatar Planning and Statistics Authority (via COD-AB)"},
    "Egypt": {"strategy": "zip", "url": "https://data.humdata.org/dataset/b90d81ba-7c7a-4283-9899-827480d80a79/resource/6115d7e5-4ba4-451d-988e-f791f4716e7a/download/egy_admin_boundaries.shp.zip", "source": "Egypt CAPMAS (via COD-AB)"},
    "Iraq": {"strategy": "zip", "url": "https://data.humdata.org/dataset/488bb3cd-3ce9-49d3-862a-3ce7975c63e1/resource/1d1ed1f3-a295-47b6-800d-356dc1036731/download/irq_admin_boundaries.shp.zip", "source": "Iraq Central Statistics Office (via COD-AB)"},
    "Yemen": {"strategy": "zip", "url": "https://data.humdata.org/dataset/6b2656e2-b915-4671-bfed-468d5edcd80a/resource/eb58b807-bea0-450f-a654-0fa49054b4e0/download/yem_admin_boundaries.shp.zip", "source": "Yemen Central Statistical Organization (via COD-AB)"},
    "Lebanon": {"strategy": "zip", "url": "https://data.humdata.org/dataset/569beba7-bad7-4951-a19d-468a035461cd/resource/1d8198d3-8f50-405a-9534-6c131fe3d6d2/download/lbn_admin_boundaries.shp.zip", "source": "Lebanon Council for Development and Reconstruction (via COD-AB)"},
    "Palestine": {"strategy": "zip", "url": "https://data.humdata.org/dataset/2caf8373-816f-458c-9913-71bddb9cab7c/resource/ebf1cc7b-45e0-43bb-bec5-2464cd2d26fc/download/pse_admin_boundaries.shp.zip", "source": "Palestinian Authority Ministry of Planning (via COD-AB)"},

    # ---- Middle East: UN-sourced (not national government) (untested) ----
    "Iran": {"strategy": "zip", "url": "https://data.humdata.org/dataset/247b4026-79ff-4b16-95b9-0f366792d2cc/resource/515839b3-68cd-4308-818d-7d21b43c22d3/download/irn_admin_boundaries.shp.zip", "source": "UNHCR (via COD-AB) — NOT national government"},
    "Syria": {"strategy": "zip", "url": "https://data.humdata.org/dataset/356a63e9-90aa-4b9c-a938-58ef24469c00/resource/2e69313a-b8a8-46bf-8cc8-fbd132b279bd/download/syr_admin_boundaries.shp.zip", "source": "UN Cartographic Section (via COD-AB) — NOT national government"},

    # ---- Covered elsewhere ----
    "Turkey": {"strategy": "manual", "portal": "Use the EU dataset — Turkiye is included in Eurostat NUTS 2021 as a candidate country.", "source": "Eurostat NUTS 2021"},
    "Cyprus": {"strategy": "manual", "portal": "Use the EU dataset — Cyprus is an EU member included in Eurostat NUTS 2021.", "source": "Eurostat NUTS 2021"},

    # ---- Registration-gated: cannot be automated ----
    "Saudi Arabia": {"strategy": "gated", "portal": "https://www.geosa.gov.sa/en/Pages/default.aspx", "source": "GEOSA National Geospatial Platform (account required)"},
    "Bahrain": {"strategy": "gated", "portal": "https://data.gov.bh/", "source": "Bahrain Open Data Portal (account required)"},
    "Oman": {"strategy": "gated", "portal": "https://ea.gov.om/en/open-data/open-data-sets/gis-data/", "source": "Oman Environment Authority (account required)"},
    "Kuwait": {"strategy": "gated", "portal": "https://e.gov.kw/sites/kgoenglish/Pages/OtherTopics/OpenData.aspx", "source": "Kuwait Government Online (account required)"},

    # ---- No open source located ----
    "Israel": {"strategy": "manual", "portal": "https://www.gov.il/en/departments/survey_of_israel", "source": "Survey of Israel — publishes DXF/ARC-INFO, no open shapefile located"},
    "Jordan": {"strategy": "manual", "portal": "Department of Statistics / Royal Jordanian Geographic Centre", "source": "No open government shapefile located"},
}


# =========================================================
# DOWNLOAD / EXTRACT HELPERS
# =========================================================

def download(url, dest_path, retries=6):
    """Stream a URL to disk with simple retries. Returns True on success."""
    headers = {"User-Agent": "Mozilla/5.0"}
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, headers=headers, timeout=120) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                done = 0
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            print(f"\r  {done/1048576:.1f}/{total/1048576:.1f} MB "
                                  f"({100*done/total:.0f}%)", end="")
                print()
            return True
        except Exception as e:
            print(f"\n  [attempt {attempt}/{retries}] download failed: {e}")
    return False


def extract_zip(zip_path, dest_dir, members=None):
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest_dir, members=members)


def find_shapefiles(directory):
    out = []
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if f.lower().endswith(".shp"):
                out.append(os.path.join(root, f))
    return out


# =========================================================
# STRATEGIES
# =========================================================

def do_zip(entry, dest_dir):
    tmp = os.path.join(dest_dir, "_download.zip")
    print(f"Downloading: {entry['url']}")
    if not download(entry["url"], tmp):
        return False
    print("Extracting...")
    extract_zip(tmp, dest_dir)
    os.remove(tmp)
    return True


def do_nested_zip(entry, dest_dir):
    tmp = os.path.join(dest_dir, "_outer.zip")
    print(f"Downloading: {entry['url']}")
    if not download(entry["url"], tmp):
        return False
    with tempfile.TemporaryDirectory() as staging:
        extract_zip(tmp, staging)
        for inner in entry.get("inner", []):
            inner_path = os.path.join(staging, inner)
            if os.path.exists(inner_path):
                print(f"Extracting inner: {inner}")
                extract_zip(inner_path, dest_dir)
            else:
                print(f"  [warn] inner file not found in archive: {inner}")
    os.remove(tmp)
    return True


def do_arcgis(entry, dest_dir):
    item = entry["item_id"]
    api = (f"https://hub.arcgis.com/api/download/v1/items/{item}/shapefile"
           f"?redirect=false&layers=0")
    print("Resolving ArcGIS download url...")
    try:
        result_url = requests.get(api, timeout=60).json().get("resultUrl")
    except Exception as e:
        print(f"  [error] could not resolve ArcGIS item: {e}")
        return False
    if not result_url:
        print("  [error] ArcGIS returned no download url.")
        return False
    tmp = os.path.join(dest_dir, "_download.zip")
    print(f"Downloading: {result_url}")
    if not download(result_url, tmp):
        return False
    print("Extracting...")
    extract_zip(tmp, dest_dir)
    os.remove(tmp)
    return True


def print_manual(country, entry, dest_dir):
    print("\n" + "-" * 60)
    print(f"'{country}' cannot be downloaded automatically.")
    print(f"Source : {entry.get('source', 'n/a')}")
    if entry.get("portal"):
        print(f"Get it : {entry['portal']}")
    print("Then place the .shp (with its .shx/.dbf/.prj) so that this path exists:")
    print(f"         {config.SHAPEFILE}")
    print(f"(i.e. inside: {dest_dir})")
    print("-" * 60)


# =========================================================
# MAIN
# =========================================================

def main():
    country = config.COUNTRY
    target = config.SHAPEFILE
    dest_dir = os.path.dirname(target) if target else ""

    print("=" * 62)
    print(f"STEP 00 — Boundary shapefile for: {country}")
    print("=" * 62)

    if not target:
        print(f"[ERROR] Could not determine the shapefile path for '{country}'.")
        print("Either set COUNTRY to a registered country, or set FLOOD_SHAPEFILE")
        print("(or SHAPEFILE_BY_COUNTRY in config.py) to point at its .shp file.")
        sys.exit(1)

    if os.path.exists(target):
        print(f"Already present, skipping download:\n  {target}")
        return

    entry = SOURCES.get(country)
    if entry is None:
        print(f"[ERROR] No shapefile source registered for '{country}'.")
        print("Add it to download_shapefile.py, or download it manually and")
        print("set SHAPEFILE_BY_COUNTRY in config.py to point at it.")
        sys.exit(1)

    print(f"Source: {entry.get('source', 'n/a')}")
    os.makedirs(dest_dir, exist_ok=True)

    strategy = entry["strategy"]
    if strategy in ("gated", "manual"):
        print_manual(country, entry, dest_dir)
        sys.exit(1)
    elif strategy == "zip":
        ok = do_zip(entry, dest_dir)
    elif strategy == "nested_zip":
        ok = do_nested_zip(entry, dest_dir)
    elif strategy == "arcgis":
        ok = do_arcgis(entry, dest_dir)
    else:
        print(f"[ERROR] Unknown strategy '{strategy}'.")
        sys.exit(1)

    if not ok:
        print("[ERROR] Download failed after retries.")
        sys.exit(1)

    if os.path.exists(target):
        print(f"\n[ok] Shapefile ready:\n  {target}")
        return

    found = find_shapefiles(dest_dir)
    print("\n[WARN] Download succeeded but the expected file was not found at:")
    print(f"  {target}")
    if found:
        print("Shapefiles that WERE extracted:")
        for f in found:
            print(f"  {f}")
        print("\nUpdate SHAPEFILE_BY_COUNTRY in config.py to point at the correct one.")
    else:
        print("No .shp files were found in the archive.")
    sys.exit(1)


if __name__ == "__main__":
    main()
