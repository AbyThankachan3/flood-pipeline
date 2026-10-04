"""
Step 01 — Download the raw Aqueduct flood rasters.

These rasters are GLOBAL, so this step is country-independent: it runs ONCE and
its output is shared by every country in the run. Adding countries does not
re-download anything.

Output layout (flat per hazard — grouping is done by parsing filenames later):

    {RAW_DIR}/inunriver/inunriver_rcp4p5_<model>_2030_rp00002.tif
    {RAW_DIR}/inuncoast/inuncoast_rcp4p5_nosub_2030_rp0002_0_perc_05.tif
    {RAW_DIR}/manifests/download_manifest.csv

Only files matching the configured hazards / scenarios / years / return periods
are fetched. Historical baselines are skipped — this pipeline builds projected
layers only.

Resume-safe: a completed file is skipped, and an interrupted one leaves a
`.part` that is restarted cleanly rather than being mistaken for complete.
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

import config
import naming

CHUNK = 1024 * 1024

RAW_SUBDIR = {
    "riverine": naming.RIVERINE_PREFIX,   # inunriver
    "coastal": naming.COASTAL_PREFIX,     # inuncoast
}


# =========================================================
# SELECTION
# =========================================================

def wanted(meta):
    """Does this parsed file match the configured run?"""
    if meta is None:
        return False
    if meta["hazard"] not in config.HAZARDS:
        return False
    if meta["scenario"] not in {naming.normalize_scenario(s) for s in config.SCENARIOS}:
        return False
    if meta["year"] not in config.YEARS:
        return False
    if meta["rp"] not in {naming.normalize_rp(r) for r in config.RPS}:
        return False
    if meta["hazard"] == "coastal":
        if meta["subsidence"] not in config.COASTAL_SUBSIDENCE:
            return False
        if meta["projection"] not in config.COASTAL_PERCENTILES:
            return False
    return True


def crawl_index(index_url, timeout=60):
    """Every .tif link on the Aqueduct index page, as absolute URLs."""
    print(f"Fetching Aqueduct index: {index_url}")
    r = requests.get(index_url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        full = urljoin(index_url, href)
        if os.path.splitext(urlparse(full).path)[1].lower() in (".tif", ".tiff"):
            urls.add(full)
    return sorted(urls)


def read_url_list(path):
    """
    Read a text file of .tif URLs, one per line ('#' comments and blanks ok).

    A bare filename is joined onto config.AQUEDUCT_BASE_URL, so the same file
    works whether it lists full URLs (like nismod/aqueduct's tiffs.txt) or just
    names.
    """
    print(f"Reading source list: {path}")
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line if "://" in line else
                        urljoin(config.AQUEDUCT_BASE_URL, line))
    return sorted(set(urls))


def generate_urls():
    """
    Every URL this run wants, built from the configured dimensions.

    This is the default source, because the host serves individual files but
    returns 403 on the directory itself -- there is no index page to crawl.

    The two hazards pad the return period differently (riverine rp00002 vs
    coastal rp0002), which naming.*_source_name() handles.
    """
    urls = []

    if "riverine" in config.HAZARDS:
        for scen in config.SCENARIOS:
            for year in config.YEARS:
                for rp in config.RPS:
                    for model in config.RIVERINE_MODELS:
                        urls.append(urljoin(
                            config.AQUEDUCT_BASE_URL,
                            naming.riverine_source_name(scen, model, year, rp)))

    if "coastal" in config.HAZARDS:
        for scen in config.SCENARIOS:
            for sub in config.COASTAL_SUBSIDENCE:
                for year in config.YEARS:
                    for rp in config.RPS:
                        for proj in config.COASTAL_PERCENTILES:
                            urls.append(urljoin(
                                config.AQUEDUCT_BASE_URL,
                                naming.coastal_source_name(
                                    scen, sub, year, rp, proj)))

    return sorted(set(urls))


def already_present():
    """Raw .tif files already sitting in RAW_DIR, per hazard."""
    counts = {}
    for hazard, sub in RAW_SUBDIR.items():
        d = os.path.join(config.RAW_DIR, sub)
        if os.path.isdir(d):
            n = len([f for f in os.listdir(d) if f.lower().endswith((".tif", ".tiff"))])
            if n:
                counts[hazard] = n
    return counts


def explain_dead_source(err):
    present = already_present()
    print(f"\n[ERROR] Could not get a file list from the configured source.")
    print(f"        {err}")
    print()
    print(f"Base URL in use: {config.AQUEDUCT_BASE_URL}")
    print()
    print("Note: the ORIGINAL WRI path")
    print("  https://wri-projects.s3.amazonaws.com/AqueductFloodTool/download/v2/")
    print("is dead (404, prefix removed from the bucket); the working location as")
    print("of 2026-10-04 is https://aqueduct.wridata.org/AqueductFloods20/ .")
    print("That host serves individual files but 403s on the directory, so there")
    print("is no index to crawl -- file names are generated from config instead.")
    print()
    print("Options:")
    print("  * If you already have the rasters, put them in")
    print(f"      {os.path.join(config.RAW_DIR, 'inunriver')}")
    print(f"      {os.path.join(config.RAW_DIR, 'inuncoast')}")
    print("    and re-run with FLOOD_SKIP_DOWNLOAD=1 — steps 02-05 need no network.")
    print("  * If you have a working mirror, point FLOOD_SOURCE_URLS at a text")
    print("    file of .tif URLs (one per line), or set FLOOD_BASE_URL/")
    print("    FLOOD_INDEX_URL to the new location.")
    print("  * Aqueduct Floods v2 is also published on Google Earth Engine as")
    print("    WRI/Aqueduct_Flood_Hazard_Maps/V2, which needs a different export")
    print("    path than this pipeline.")
    if present:
        print()
        print("Note: RAW_DIR already contains "
              + ", ".join(f"{n} {h}" for h, n in sorted(present.items()))
              + " raster(s).")
        print("You can proceed with those now using FLOOD_SKIP_DOWNLOAD=1.")


def select_files(urls):
    """(url, hazard, filename) for every link this run wants."""
    selected = []
    for url in urls:
        fname = os.path.basename(urlparse(url).path)
        meta = naming.parse(fname)
        if wanted(meta):
            selected.append((url, meta["hazard"], fname))
    return selected


# =========================================================
# DOWNLOAD
# =========================================================

def download_one(url, hazard, filename, timeout=300):
    dest_dir = os.path.join(config.RAW_DIR, RAW_SUBDIR[hazard])
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    part = dest + ".part"

    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return ("skipped", filename, dest)

    try:
        with requests.get(url, stream=True, timeout=timeout,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status_code != 200:
                return (f"http_{r.status_code}", filename, dest)
            expected = int(r.headers.get("content-length", 0))
            written = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)

        # A connection that drops mid-stream does not always raise, so a short
        # read would otherwise be renamed into place and look complete. On a
        # long unattended run that is how silent corruption gets in.
        if expected and written != expected:
            os.remove(part)
            return (f"truncated_{written}_of_{expected}", filename, dest)

        os.replace(part, dest)
        return ("downloaded", filename, dest)
    except Exception as e:
        if os.path.exists(part):
            os.remove(part)
        return (f"error:{e}", filename, dest)


# =========================================================
# MAIN
# =========================================================

def main():
    print("=" * 62)
    print("STEP 01 — Download raw Aqueduct flood data (shared, global)")
    print("=" * 62)
    for line in config.summary_lines():
        print("  " + line)

    if config.SKIP_DOWNLOAD:
        present = already_present()
        if not present:
            print(f"\n[ERROR] FLOOD_SKIP_DOWNLOAD is set but {config.RAW_DIR} "
                  f"holds no rasters.")
            print("Put the Aqueduct .tif files in inunriver/ and inuncoast/ "
                  "under that folder first.")
            sys.exit(1)
        print("\nFLOOD_SKIP_DOWNLOAD is set — using what is already in RAW_DIR: "
              + ", ".join(f"{n} {h}" for h, n in sorted(present.items())))
        return

    try:
        if config.SOURCE_URLS:
            urls = read_url_list(config.SOURCE_URLS)
        elif config.AQUEDUCT_INDEX_URL:
            urls = crawl_index(config.AQUEDUCT_INDEX_URL)
        else:
            print(f"Generating file list from config "
                  f"(no index to crawl; host does not list directories)")
            print(f"Base URL: {config.AQUEDUCT_BASE_URL}")
            urls = generate_urls()
    except Exception as e:
        explain_dead_source(e)
        sys.exit(1)

    print(f"Source lists {len(urls)} GeoTIFF(s).")

    files = select_files(urls)
    if not files:
        print("\n[ERROR] Nothing in the source list matched this configuration.")
        print("Check FLOOD_HAZARDS / FLOOD_SCENARIOS / FLOOD_YEARS / FLOOD_RPS.")
        sys.exit(1)

    by_hazard = {}
    for _u, hazard, _f in files:
        by_hazard[hazard] = by_hazard.get(hazard, 0) + 1
    print("Matched: " + ", ".join(f"{n} {h}" for h, n in sorted(by_hazard.items())))
    print(f"Downloading with {config.DOWNLOAD_WORKERS} threads into {config.RAW_DIR}\n")

    results = []
    with ThreadPoolExecutor(max_workers=config.DOWNLOAD_WORKERS) as pool:
        futures = [pool.submit(download_one, u, h, f) for u, h, f in files]
        for fut in tqdm(as_completed(futures), total=len(futures), ncols=100):
            results.append(fut.result())

    df = pd.DataFrame(results, columns=["status", "filename", "path"])

    manifest_dir = os.path.join(config.RAW_DIR, "manifests")
    os.makedirs(manifest_dir, exist_ok=True)
    manifest = os.path.join(manifest_dir, "download_manifest.csv")
    df.to_csv(manifest, index=False)

    downloaded = int((df["status"] == "downloaded").sum())
    skipped = int((df["status"] == "skipped").sum())
    failed = len(df) - downloaded - skipped

    print(f"\nDownloaded {downloaded}, already present {skipped}, failed {failed}.")
    print(f"Manifest: {manifest}")

    if failed:
        print("\nFailed entries (re-running is safe and retries only these):")
        for row in df[~df["status"].isin(["downloaded", "skipped"])].itertuples():
            print(f"  {row.status:24s} {row.filename}")
        sys.exit(1)


if __name__ == "__main__":
    main()
