# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A per-country ETL pipeline turning WRI Aqueduct Floods v2 global inundation
rasters into Cloud-Optimized GeoTIFFs. Deliberately modelled on the sibling
`../Wind` pipeline — same config-with-env-overrides pattern, numbered steps, a
`run_all.py` that runs the global download once then loops countries, and a
final cleanup step. When changing a convention here, check whether `../Wind`
needs the same change; the boundary-shapefile registry in particular is a
**copy**, not a shared import.

`FloodRiverine/` and `FloodCoastal/` hold the superseded original scripts. They
are kept for reference and are not wired into `run_all.py`. Don't extend them.

**CPU-only.** Nothing here imports CuPy/CUDA/numba; verified with every GPU
hidden. The `geoflood-gpu` conda env and `../env_gpu.yml` carry `cupy-cuda12x`
for the *separate* CSV tools (`../newGPUParser.py`, `../tiffaggregator/*_gpu.py`)
— this pipeline needs none of it, and `requirements.txt` has no GPU dependency.
Bottlenecks are RAM and disk: tune `FLOOD_PROCESS_WORKERS` / `FLOOD_TILE_SIZE`.

There is no linter config and no build step. `tests/` holds five plain scripts
(no pytest) covering the filename grammar, ensemble maths, mask cache, trim
invariants and download retries; they generate their own rasters or stub the
network, so they need no Aqueduct data and no connection.

## Commands

```bash
python run_all.py --dry-run
```

```bash
FLOOD_SKIP_DOWNLOAD=1 python run_all.py --yes
```

```bash
FLOOD_COUNTRIES=all FLOOD_SKIP_DOWNLOAD=1 python run_all.py --yes
```

```bash
for t in tests/test_*.py; do python "$t" || break; done
```

Individual steps are plain scripts (`python 02-riverine-ensemble.py`) and read
the same env vars. On this machine the geospatial deps live in the
`geoflood-gpu` conda env, which is not on `PATH` — and its GDAL DLLs fail to
load if you invoke its `python.exe` directly, so go through conda:

```bash
conda run --no-capture-output -n geoflood-gpu python run_all.py --dry-run
```

`conda run` rejects any argument containing a newline, so `python -c` with a
multi-line snippet fails; write a temp script and run that instead.

## Where the source data comes from

**Working location (verified 2026-10-04):**

```
https://aqueduct.wridata.org/AqueductFloods20/<filename>.tif
```

Confirmed end to end: step 01 downloaded
`inunriver_rcp4p5_00000NorESM1-M_2030_rp00002.tif` and the result is
**MD5-identical** to the copy already in the repo.

The old path — `wri-projects.s3.amazonaws.com/AqueductFloodTool/download/v2/` —
is **dead** (404; the `AqueductFloodTool` prefix is gone from the bucket,
re-checked 2026-10-04). The WRI docs, the community `nismod/aqueduct`
`tiffs.txt`, and the legacy `FloodRiverine`/`FloodCoastal` scripts all still
point there. Don't.

**There is no index page.** The new host serves individual files but returns 403
on the directory, so nothing can be crawled. Step 01 therefore **generates**
filenames from `HAZARDS x SCENARIOS x YEARS x RPS` (x models, or x subsidence x
percentiles) — 486 URLs for the full default config. Precedence:
`FLOOD_SKIP_DOWNLOAD` > `FLOOD_SOURCE_URLS` > `FLOOD_INDEX_URL` > generated.

Two things that bite when generating names:

- **Return-period padding differs per hazard**: riverine `rp00002` (5 digits),
  coastal `rp0002` (4). `inuncoast_..._rp00002_...` 403s. Handled by
  `naming.riverine_source_name()` / `coastal_source_name()`.
- **Model tokens are padded to exactly 14 characters, not uniformly.**
  `0000GFDL-ESM2M`, `0000HadGEM2-ES`, `00IPSL-CM5A-LR`, `MIROC-ESM-CHEM`,
  `00000NorESM1-M`. Each was confirmed by HEAD request. The legacy script's
  `DEFAULT_MODELS` (5 zeros on everything, plus `MIROC5`) produces URLs that do
  not exist. If one 404s, re-probe rather than adjusting padding by eye.

`FLOOD_RIVERINE_MODELS` is the token **list**; the expected count is derived
from it (`FLOOD_RIVERINE_EXPECTED` overrides). These were once the same env var,
which made `int()` fail on a model name.

## Architecture

Data flows one way through files on disk; the only shared state is `RAW_DIR`.

- **`config.py`** — every setting, each overridable by a `FLOOD_*` env var.
  `run_all.py` re-invokes each step as a subprocess with `FLOOD_COUNTRY` set,
  which is how one process handles many countries: each child re-derives its own
  `OUTPUT_ROOT` and `SHAPEFILE` from that one variable.
- **`naming.py`** — the Aqueduct filename grammar. Everything downstream groups
  files by `(scenario, year, rp)`, so all parsing and normalisation lives here.
- **`raster_utils.py`** — the full-grid boundary mask (cached per process; it's
  ~0.9 GB and identical for every group) and the tiled multi-band writer. Both
  hazard steps are thin wrappers over `process_group()`, differing only in their
  `compute_fn` and band metadata.
- **`0X-*.py`** — the steps. Leading digits mean they can't be imported as
  modules; test them via `importlib.util.spec_from_file_location`.

The raw data is global and country-independent, so step 01 runs **once** and
steps 02–05 run **per country**. That split is the reason multi-country runs are
cheap, and it's why `RAW_DIR` and `OUTPUT_ROOT` are separate trees.

## Things that will bite you

- **Filename grammar is load-bearing.** Riverine is
  `inunriver_{scenario}_{model}_{year}_{rp}.tif` — exactly 5 underscore tokens.
  The legacy scripts wrote their output as
  `inunriver_{scenario}_aggregated_{year}_{rp}.tif`, which has the *same* token
  count and parses as a model literally named "aggregated". `naming.is_derived()`
  rejects those. If you loosen the parser, that hole reopens.
- **Return periods are padded differently per hazard** (`rp00002` riverine vs
  `rp0002` coastal). `normalize_rp()` collapses both to 4 digits so the two
  hazards agree; compare normalised tokens, never raw ones.
- **TIFF block dimensions must be multiples of 16.** `block_size()` exists
  because clamping the tile size to a small country window produces an illegal
  value and rasterio refuses to open the file. Windows under 16 px are written
  untiled.
- **Never narrow the output extent to the boundary. This is a hard rule.**
  The mask is rasterized over the full global grid and the output keeps the full
  global extent, exactly as the original FloodRiverine scripts did. Windowing to
  the boundary's bounding box looks like an easy ~10× win and was tried once: a
  bounding box cannot wrap the antimeridian, so for the USA (Alaska's Aleutians
  sit at both −179.1° and +179.8°) it either spans ~360° or, once "fixed" with a
  clip box, silently deletes the western Aleutians, Guam, the Northern Marianas
  and American Samoa from the national boundary. Quietly shrinking a country's
  territory is not an acceptable tradeoff. `config.py` carries a note where such
  a setting would go, and `tests/test_ensemble.py` has an antimeridian
  regression test that fails if extent-narrowing returns. Don't reintroduce it,
  and don't add "just an opt-in flag" for it either.
- **The shapefile is used whole.** No clipping, filtering, or feature dropping
  anywhere. `load_boundary()` reprojects and returns; that's it.
- **`FLOOD_TRIM` is the one sanctioned way to shrink an output, and it is off by
  default.** It drops only rows/columns in which every pixel is already NoData,
  and it is derived by *measuring the rasterized mask* (`data_extent()`), never
  from a bounding box. `trimmed_window()` re-counts the masked pixels and
  refuses to trim on any mismatch, falling back to the full extent. That
  measure-don't-predict distinction is what makes it safe where the clip-box
  was not — the antimeridian resolves itself, because data in both the first
  and last columns leaves nothing to trim horizontally. If you touch this code,
  keep `tests/test_trim.py` passing; it asserts equal pixel counts, equal band
  totals and identical coordinates between trimmed and untrimmed runs.
- **Band order is positional.** A coastal group missing any of its four variants
  is skipped, not written short. Riverine band 5 (`model_count`) is what makes
  bands 1–4 interpretable and is 0, never NaN, where nothing has data.
- **Source nodata is `-9999`, output nodata is NaN.** Reads use
  `masked=True` and the mask is combined with the country mask before anything
  is computed, so `-9999` must never reach a statistic. There's a regression
  check for this.
- **The source host sheds TLS connections under concurrency.** A 16-worker run
  dropped 9 of 594 files with `SSL: UNEXPECTED_EOF_WHILE_READING`. `download_one`
  retries with exponential backoff (`FLOOD_DOWNLOAD_RETRIES`, default 4) and
  treats a truncated read as the same transient fault; 403/404 are permanent and
  are never retried. Don't raise `FLOOD_DOWNLOAD_WORKERS` without keeping the
  retries.
- **Idempotency everywhere.** Every step skips work already done, and writes
  through a `.part` file it renames on success. These runs are long; don't break
  resumability.
- **Cleanup is per-file.** Step 05 deletes a pre-COG GeoTIFF only when *its own*
  COG exists — not merely when some COG does.
- **Step 04 needs ~30 GB of scratch in the output folder**, per file, even
  though the finished COGs are under 100 MB. `rio-cogeo` builds each COG through
  an uncompressed intermediate created *next to the destination*, so `TMPDIR`
  and `CPL_TMPDIR` do not move it — only relocating `FLOOD_DATA_ROOT` does.
  (18.66 GB uncompressed; ~25.4 GB peak observed in a single sampled run;
  `SCRATCH_FACTOR = 1.6` in `04-cog.py` adds margin over that one data point.)
  GDAL's `CHECK_DISK_FREE_SPACE` guard correctly predicts this; leave it on.
  Disabling it was tried and just converts a clear up-front error into a
  "No space left on device" several minutes in.
