"""
Step 03 — Coastal: clip to the country and stack the subsidence/percentile
variants into one raster.

Aqueduct publishes coastal inundation depth as separate files per subsidence
assumption (nosub / wtsub) and per sea-level-rise percentile (05th / 50th).
Unlike riverine these are not samples of one quantity to average — they are
four distinct scenarios — so they are stacked as bands, not reduced.

Input  : {RAW_DIR}/inuncoast/*.tif           (global, shared between countries)
Output : {OUTPUT_ROOT}/results/coastal_{scenario}_{year}_{rp}.tif

Bands (float32, NaN outside the country / outside the flood extent), in
config.COASTAL_SUBSIDENCE x config.COASTAL_PERCENTILES order:

    1  depth_nosub_p05   m   no subsidence,   5th percentile SLR
    2  depth_nosub_p50   m   no subsidence,  50th percentile SLR
    3  depth_wtsub_p05   m   with subsidence, 5th percentile SLR
    4  depth_wtsub_p50   m   with subsidence, 50th percentile SLR

A group missing any of its four variants is skipped rather than written with a
band silently absent — band order is positional and must stay trustworthy.
"""

import os
import sys
import glob
import traceback
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

import config
import naming
import raster_utils

_WORKER_GDF = None


def _init_worker(shapefile, crs):
    global _WORKER_GDF
    _WORKER_GDF = raster_utils.load_boundary(shapefile, crs)


def variant_order():
    """The (subsidence, projection) pairs that become bands 1..N, in order."""
    return [(sub, proj)
            for sub in config.COASTAL_SUBSIDENCE
            for proj in config.COASTAL_PERCENTILES]


def band_label(subsidence, projection):
    """('nosub', '0_perc_05') -> 'depth_nosub_p05'"""
    pct = projection.replace("0_perc_", "p")
    return f"depth_{subsidence}_{pct}"


def identity(stack):
    """Coastal variants pass through unchanged — one input raster per band."""
    return stack


def discover_groups():
    """{(scenario, year, rp): {(sub, proj): path}} from the shared raw folder."""
    raw_dir = os.path.join(config.RAW_DIR, naming.COASTAL_PREFIX)
    groups = defaultdict(dict)

    for path in sorted(glob.glob(os.path.join(raw_dir, "*.tif"))):
        meta = naming.parse_coastal(path)
        if meta is None:
            continue
        if meta["scenario"] not in {naming.normalize_scenario(s) for s in config.SCENARIOS}:
            continue
        if meta["year"] not in config.YEARS:
            continue
        if meta["rp"] not in {naming.normalize_rp(r) for r in config.RPS}:
            continue
        if meta["subsidence"] not in config.COASTAL_SUBSIDENCE:
            continue
        if meta["projection"] not in config.COASTAL_PERCENTILES:
            continue
        groups[naming.group_key(meta)][(meta["subsidence"], meta["projection"])] = path

    return dict(sorted(groups.items()))


def process_one(key, variants, results_dir):
    scenario, year, rp = key
    out_name = naming.output_name("coastal", scenario, year, rp)
    out_path = os.path.join(results_dir, out_name)

    order = variant_order()

    try:
        if os.path.exists(out_path):
            return {"status": "skipped", "name": out_name}

        missing = [f"{s}/{p}" for s, p in order if (s, p) not in variants]
        if missing:
            return {"status": "incomplete", "name": out_name,
                    "error": "missing " + ", ".join(missing)}

        paths = [variants[(s, p)] for s, p in order]
        descriptions = [band_label(s, p) for s, p in order]

        tmp_path = out_path + ".part"
        written = raster_utils.process_group(
            src_paths=paths,
            out_path=tmp_path,
            gdf=_WORKER_GDF,
            compute_fn=identity,
            n_out_bands=len(order),
            band_descriptions=descriptions,
            band_units=["m"] * len(order),
            dataset_tags={
                "hazard": "coastal",
                "scenario": scenario,
                "year": year,
                "return_period": rp,
                "source": "WRI Aqueduct Floods v2 (inuncoast)",
                "bands": ",".join(descriptions),
            },
            tile_size=config.TILE_SIZE,
            crs=config.CRS,
            trim=config.TRIM_BLANK_BORDER,
            log=lambda m: print(f"          {out_name}: {m}"),
        )

        if written is None:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return {"status": "no_overlap", "name": out_name}

        os.replace(tmp_path, out_path)
        return {"status": "ok", "name": out_name, "bands": len(order)}

    except Exception as e:
        traceback.print_exc()
        if os.path.exists(out_path + ".part"):
            os.remove(out_path + ".part")
        return {"status": "failed", "name": out_name, "error": str(e)}


def main():
    print("=" * 62)
    print(f"STEP 03 — Coastal stack for: {config.COUNTRY}")
    print("=" * 62)

    if "coastal" not in config.HAZARDS:
        print("coastal not in FLOOD_HAZARDS — nothing to do.")
        return

    if not config.SHAPEFILE or not os.path.exists(config.SHAPEFILE):
        print(f"[ERROR] Boundary shapefile not found: {config.SHAPEFILE or '(unset)'}")
        print("Run download_shapefile.py first.")
        sys.exit(1)

    groups = discover_groups()
    if not groups:
        print(f"[ERROR] No coastal rasters found under "
              f"{os.path.join(config.RAW_DIR, naming.COASTAL_PREFIX)}")
        print("Run 01-download-flood-data.py first.")
        sys.exit(1)

    results_dir = os.path.join(config.OUTPUT_ROOT, "results")
    os.makedirs(results_dir, exist_ok=True)

    print(f"{len(groups)} scenario/year/return-period group(s) to build.")
    print(f"Bands per output: {len(variant_order())}")
    print(f"Workers: {config.PROCESS_WORKERS}\n")

    outcomes = []
    with ProcessPoolExecutor(
        max_workers=config.PROCESS_WORKERS,
        initializer=_init_worker,
        initargs=(config.SHAPEFILE, config.CRS),
    ) as pool:
        futures = {
            pool.submit(process_one, key, variants, results_dir): key
            for key, variants in groups.items()
        }
        for fut in as_completed(futures):
            r = fut.result()
            outcomes.append(r)
            if r["status"] == "ok":
                print(f"[ok]      {r['name']}  ({r['bands']} bands)")
            elif r["status"] == "skipped":
                print(f"[skip]    {r['name']}  (already built)")
            elif r["status"] == "no_overlap":
                print(f"[skip]    {r['name']}  (country does not intersect)")
            elif r["status"] == "incomplete":
                print(f"[skip]    {r['name']}  ({r['error']})")
            else:
                print(f"[FAILED]  {r['name']}: {r.get('error')}")

    built = sum(1 for r in outcomes if r["status"] == "ok")
    failed = sum(1 for r in outcomes if r["status"] == "failed")
    print(f"\nBuilt {built}, skipped {len(outcomes) - built - failed}, failed {failed}.")
    print(f"Output: {results_dir}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
