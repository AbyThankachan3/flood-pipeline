"""
Step 02 — Riverine: clip to the country and collapse the GCM ensemble.

Aqueduct publishes riverine inundation depth for five CMIP5 models. For each
(scenario, year, return period) this step reads all the models it finds, clips
them to the country boundary, and reduces them across models into one 5-band
raster.

Input  : {RAW_DIR}/inunriver/*.tif            (global, shared between countries)
Output : {OUTPUT_ROOT}/results/riverine_{scenario}_{year}_{rp}.tif

Bands (float32, NaN outside the country / outside the flood extent):

    1  mean_flood_depth    m       across-model mean
    2  flood_depth_stddev  m       across-model standard deviation
    3  median_flood_depth  m       across-model median
    4  peak_flood_depth    m       across-model maximum
    5  model_count         models  how many models had data for that pixel

Band 5 is what makes the others interpretable: a deep mean backed by 1 of 5
models is not the same claim as one backed by 5. Where no model has data all
five bands are NaN except model_count, which is 0.

Skips groups whose output already exists, so re-running after an interruption
only does the remaining work.
"""

import os
import sys
import glob
import traceback
import warnings
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

import config
import naming
import raster_utils

BAND_DESCRIPTIONS = [
    "mean_flood_depth",
    "flood_depth_stddev",
    "median_flood_depth",
    "peak_flood_depth",
    "model_count",
]
BAND_UNITS = ["m", "m", "m", "m", "models"]

_WORKER_GDF = None


def _init_worker(shapefile, crs):
    """Pool initializer: load and reproject the boundary once per worker."""
    global _WORKER_GDF
    _WORKER_GDF = raster_utils.load_boundary(shapefile, crs)


def ensemble_stats(stack):
    """(n_models, h, w) -> (5, h, w): mean, std, median, max, valid count."""
    n_valid = np.count_nonzero(~np.isnan(stack), axis=0).astype(np.float32)
    empty = n_valid == 0

    out = np.empty((5, stack.shape[1], stack.shape[2]), dtype=np.float32)
    with warnings.catch_warnings():
        # Pixels where no model has data are normal (sea, outside the flood
        # extent); the nan-reductions warn about them and return NaN, which is
        # exactly what band 5 is there to disambiguate.
        warnings.simplefilter("ignore", RuntimeWarning)
        out[0] = np.nanmean(stack, axis=0)
        out[1] = np.nanstd(stack, axis=0)
        out[2] = np.nanmedian(stack, axis=0)
        out[3] = np.nanmax(stack, axis=0)
    out[4] = n_valid

    # nan-reductions over an all-NaN column return NaN already, but make the
    # intent explicit rather than relying on that.
    out[0:4, empty] = np.nan
    return out


def discover_groups():
    """{(scenario, year, rp): [model tif paths]} from the shared raw folder."""
    raw_dir = os.path.join(config.RAW_DIR, naming.RIVERINE_PREFIX)
    groups = defaultdict(list)

    for path in sorted(glob.glob(os.path.join(raw_dir, "*.tif"))):
        meta = naming.parse_riverine(path)
        if meta is None:
            continue
        if meta["scenario"] not in {naming.normalize_scenario(s) for s in config.SCENARIOS}:
            continue
        if meta["year"] not in config.YEARS:
            continue
        if meta["rp"] not in {naming.normalize_rp(r) for r in config.RPS}:
            continue
        groups[naming.group_key(meta)].append(path)

    return {k: sorted(v) for k, v in sorted(groups.items())}


def process_one(key, paths, results_dir):
    scenario, year, rp = key
    out_name = naming.output_name("riverine", scenario, year, rp)
    out_path = os.path.join(results_dir, out_name)

    try:
        if os.path.exists(out_path):
            return {"status": "skipped", "name": out_name, "models": len(paths)}

        note = ""
        if len(paths) != config.RIVERINE_EXPECTED_MODELS:
            note = (f"expected {config.RIVERINE_EXPECTED_MODELS} models, "
                    f"found {len(paths)}")

        tmp_path = out_path + ".part"
        written = raster_utils.process_group(
            src_paths=paths,
            out_path=tmp_path,
            gdf=_WORKER_GDF,
            compute_fn=ensemble_stats,
            n_out_bands=5,
            band_descriptions=BAND_DESCRIPTIONS,
            band_units=BAND_UNITS,
            dataset_tags={
                "hazard": "riverine",
                "scenario": scenario,
                "year": year,
                "return_period": rp,
                "n_models": str(len(paths)),
                "models": ",".join(
                    naming.parse_riverine(p)["model"] for p in paths
                ),
                "source": "WRI Aqueduct Floods v2 (inunriver)",
                "bands": ",".join(BAND_DESCRIPTIONS),
            },
            tile_size=config.TILE_SIZE,
            crs=config.CRS,
            trim=config.TRIM_BLANK_BORDER,
            log=lambda m: print(f"          {out_name}: {m}"),
        )

        if written is None:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return {"status": "no_overlap", "name": out_name, "models": len(paths)}

        os.replace(tmp_path, out_path)
        return {"status": "ok", "name": out_name, "models": len(paths), "note": note}

    except Exception as e:
        traceback.print_exc()
        for leftover in (out_path + ".part",):
            if os.path.exists(leftover):
                os.remove(leftover)
        return {"status": "failed", "name": out_name, "error": str(e)}


def main():
    print("=" * 62)
    print(f"STEP 02 — Riverine ensemble for: {config.COUNTRY}")
    print("=" * 62)

    if "riverine" not in config.HAZARDS:
        print("riverine not in FLOOD_HAZARDS — nothing to do.")
        return

    if not config.SHAPEFILE or not os.path.exists(config.SHAPEFILE):
        print(f"[ERROR] Boundary shapefile not found: {config.SHAPEFILE or '(unset)'}")
        print("Run download_shapefile.py first.")
        sys.exit(1)

    groups = discover_groups()
    if not groups:
        print(f"[ERROR] No riverine rasters found under "
              f"{os.path.join(config.RAW_DIR, naming.RIVERINE_PREFIX)}")
        print("Run 01-download-flood-data.py first.")
        sys.exit(1)

    results_dir = os.path.join(config.OUTPUT_ROOT, "results")
    os.makedirs(results_dir, exist_ok=True)

    print(f"{len(groups)} scenario/year/return-period group(s) to build.")
    print(f"Workers: {config.PROCESS_WORKERS}\n")

    outcomes = []
    with ProcessPoolExecutor(
        max_workers=config.PROCESS_WORKERS,
        initializer=_init_worker,
        initargs=(config.SHAPEFILE, config.CRS),
    ) as pool:
        futures = {
            pool.submit(process_one, key, paths, results_dir): key
            for key, paths in groups.items()
        }
        for fut in as_completed(futures):
            r = fut.result()
            outcomes.append(r)
            if r["status"] == "ok":
                extra = f"  [warn] {r['note']}" if r.get("note") else ""
                print(f"[ok]      {r['name']}  ({r['models']} models){extra}")
            elif r["status"] == "skipped":
                print(f"[skip]    {r['name']}  (already built)")
            elif r["status"] == "no_overlap":
                print(f"[skip]    {r['name']}  (country does not intersect)")
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
