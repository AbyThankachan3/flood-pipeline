#!/usr/bin/env python3

import os
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import pandas as pd
import argparse
from bs4 import BeautifulSoup


BASE_URL = "https://wri-projects.s3.amazonaws.com/AqueductFloodTool/download/v2/"

SCENARIOS = ["rcp4p5", "rcp8p5"]
SUBSIDENCE = ["nosub", "wtsub"]
YEARS = ["2030", "2050", "2080"]

RPS = [
    "rp0002","rp0005","rp0010","rp0025",
    "rp0050","rp0100","rp0250","rp0500","rp1000"
]

PROJECTIONS = ["0","0_perc_05","0_perc_50"]

CHUNK = 1024 * 1024


INDEX_URL = "https://wri-projects.s3.amazonaws.com/AqueductFloodTool/download/v2/index.html"

def get_inuncoast_files():

    print("Fetching Aqueduct index...")

    r = requests.get(INDEX_URL)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    valid_files = []

    for a in soup.find_all("a", href=True):

        href = a["href"]

        if not href.endswith(".tif"):
            continue

        name = os.path.basename(href)

        if not name.startswith("inuncoast"):
            continue

        parts = name.replace(".tif","").split("_")

        # expected structure
        # inuncoast scenario subsidence year rp projection

        if len(parts) < 6:
            continue

        flood, scen, sub, year, rp = parts[:5]

        projection = "_".join(parts[5:])

        if scen not in SCENARIOS:
            continue

        if sub not in SUBSIDENCE:
            continue

        if year not in YEARS:
            continue

        if rp not in RPS:
            continue

        if projection not in PROJECTIONS:
            continue

        valid_files.append(name)

    print(f"Valid coastal files found: {len(valid_files)}")

    return sorted(valid_files)
def build_filename(scen, sub, year, rp, proj):

    return f"inuncoast_{scen}_{sub}_{year}_{rp}_{proj}.tif"


def build_url(filename):

    return BASE_URL + filename


def build_target(base_dir, filename):

    parts = filename.replace(".tif","").split("_")

    flood = parts[0]
    scenario = parts[1]
    subsidence = parts[2]
    year = parts[3]
    rp = parts[4]

    return Path(base_dir) / flood / year / scenario / subsidence / rp / filename


def generate_all_urls():

    urls = []

    for scen in SCENARIOS:
        for sub in SUBSIDENCE:
            for year in YEARS:
                for rp in RPS:
                    for proj in PROJECTIONS:

                        fname = build_filename(scen, sub, year, rp, proj)
                        urls.append(fname)

    return urls


def download_file(filename, base_dir, timeout=60):

    url = build_url(filename)

    target = build_target(base_dir, filename)
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        return ("skipped", filename, str(target))

    try:

        r = requests.get(url, stream=True, timeout=timeout)

        if r.status_code != 200:
            return (f"http_{r.status_code}", filename, str(target))

        with open(target, "wb") as f:
            for chunk in r.iter_content(CHUNK):
                if chunk:
                    f.write(chunk)

        return ("downloaded", filename, str(target))

    except Exception as e:
        return (f"error:{e}", filename, str(target))


def main(args):

    files = get_inuncoast_files()

    results = []

    print(f"\nTotal candidate files: {len(files)}")
    print(f"Using {args.threads} download threads\n")

    with ThreadPoolExecutor(max_workers=args.threads) as executor:

        futures = [
            executor.submit(download_file, fname, args.output_dir)
            for fname in files
        ]

        for f in tqdm(as_completed(futures), total=len(futures)):
            results.append(f.result())

    df = pd.DataFrame(results, columns=["status","filename","path"])

    manifest = Path(args.output_dir) / "manifest_inuncoast.csv"
    df.to_csv(manifest, index=False)

    print("\nDownload complete")
    print("Manifest:", manifest)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Parallel downloader for Aqueduct coastal flood maps"
    )

    parser.add_argument(
        "--output_dir",
        required=True,
        help="Base folder to save files"
    )

    parser.add_argument(
        "--threads",
        type=int,
        default=8,
        help="Number of parallel download threads"
    )

    args = parser.parse_args()

    main(args)