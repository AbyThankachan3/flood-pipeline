# Flood Exposure Pipeline

Turns WRI Aqueduct Floods v2 inundation rasters into per-country, web-servable
flood-hazard layers (Cloud-Optimized GeoTIFFs), for 2030/2050/2080 under two
emissions scenarios and nine return periods.

Same shape as the wind pipeline in `../Wind`: one config file, numbered steps, a
`run_all.py` that downloads the global source data **once** and then loops the
countries, and a cleanup step that deletes the regenerable intermediates.

| Step | Script | Does |
|---|---|---|
| 00 | `download_shapefile.py` | Fetch the country boundary shapefile (official source) |
| 01 | `01-download-flood-data.py` | Download the global Aqueduct rasters — **once**, shared by all countries |
| 02 | `02-riverine-ensemble.py` | Riverine: clip to country + collapse the 5-GCM ensemble |
| 03 | `03-coastal-stack.py` | Coastal: clip to country + stack the 4 subsidence/percentile variants |
| 04 | `04-cog.py` | Convert to Cloud-Optimized GeoTIFFs (final output) |
| 05 | `05-cleanup.py` | Delete the pre-COG intermediates (keeps raw + COGs) |

---

## Getting the source data

**Working location (verified 2026-10-04):**

```
https://aqueduct.wridata.org/AqueductFloods20/<filename>.tif
```

Verified end to end — a file downloaded by step 01 is MD5-identical to a
previously obtained copy.

The long-standing WRI path, which the official docs and the community
`nismod/aqueduct` list still advertise, is **dead**:

```
https://wri-projects.s3.amazonaws.com/AqueductFloodTool/download/v2/   # 404
```

The `AqueductFloodTool` prefix has been removed from that bucket entirely. The
superseded `FloodRiverine/` and `FloodCoastal/` scripts point at it, so they
cannot download.

### No index to crawl

The new host serves individual files but returns **403 on the directory**, so
there is no listing. Step 01 generates the filenames instead, from your
configured dimensions — 486 files for the full default config (270 riverine +
216 coastal). Just run it:

```bash
FLOOD_DATA_ROOT=D:/FloodData python run_all.py --yes
```

Other ways to supply the list, in precedence order:

```bash
FLOOD_SKIP_DOWNLOAD=1 python run_all.py --yes        # already have the rasters
```

```bash
FLOOD_SOURCE_URLS=./tiffs.txt python run_all.py --yes  # explicit URL list
```

```bash
FLOOD_INDEX_URL=https://example.org/index.html python run_all.py --yes  # crawl a mirror
```

### Filename quirks the generator handles

- **Return periods are padded differently per hazard**: riverine `rp00002`
  (5 digits), coastal `rp0002` (4). Not interchangeable — the wrong width 403s.
- **Model tokens are padded to exactly 14 characters**, not uniformly:

  | Model token | Zeros |
  |---|---|
  | `0000GFDL-ESM2M` | 4 |
  | `0000HadGEM2-ES` | 4 |
  | `00IPSL-CM5A-LR` | 2 |
  | `MIROC-ESM-CHEM` | 0 |
  | `00000NorESM1-M` | 5 |

  Each was confirmed by request against the live host. The legacy script's
  hardcoded list used five zeros on everything and named `MIROC5`; those URLs
  don't exist.

Aqueduct Floods v2 is also on Google Earth Engine as
`WRI/Aqueduct_Flood_Hazard_Maps/V2`, which needs a different export path.

---

## No GPU required

This pipeline is **CPU-only**. Nothing in `Flood/` imports CuPy, CUDA, numba or
any GPU library — the work is `rasterio` + `numpy` + `geopandas` + `rio-cogeo`,
all of which are CPU. Verified by running the full pipeline and the test suite
with `CUDA_VISIBLE_DEVICES=""` (every GPU hidden): both pass.

Don't build the environment from `../env_gpu.yml` — that one pins
`cupy-cuda12x`, which needs a CUDA GPU and is only used by the *separate* CSV
aggregation tools (`../newGPUParser.py`, `../tiffaggregator/*_gpu.py`), not by
this pipeline. Use `requirements.txt`, which has no GPU dependency:

```bash
conda create -n flood -c conda-forge python=3.11 rasterio geopandas shapely rio-cogeo pandas numpy requests beautifulsoup4 lxml tqdm
```

```bash
pip install -r requirements.txt
```

What the pipeline *is* sensitive to is **RAM and time**, not GPU. Each worker
holds one boundary mask covering the full global grid (one bool per pixel,
~0.9 GB for the Aqueduct grid) plus its tile buffers, so budget roughly 1.5 GB
per worker: 16 GB RAM → 2, 32 GB → 4, 64 GB → 8. The default is 1.

---

## Configuration

All settings live in `config.py` and can be overridden by environment variables.

| Env var | Default | Meaning |
|---|---|---|
| `FLOOD_COUNTRY` | `USA` | single country to process |
| `FLOOD_COUNTRIES` | *(unset)* | several at once: a list (`USA,Canada`) or `all`. Takes precedence |
| `FLOOD_HAZARDS` | `riverine,coastal` | which hazards to build |
| `FLOOD_DATA_ROOT` | next to `config.py` | base folder for all data (Docker: `/data`) |
| `FLOOD_GIS_DIR` | `../GIS Files` | base folder the boundary shapefiles live in |
| `FLOOD_YEARS` | `2030,2050,2080` | comma-separated years |
| `FLOOD_SCENARIOS` | `rcp4p5,rcp8p5` | comma-separated scenarios |
| `FLOOD_RPS` | `rp0002…rp1000` | return periods, canonical 4-digit form |
| `FLOOD_TRIM` | *(unset)* | `1` drops entirely-blank rows/columns from the output — see below. Values never change |
| `FLOOD_PROCESS_WORKERS` | `1` | step 02/03 CPU workers — budget ~1.5 GB each (16 GB RAM → 2, 32 → 4, 64 → 8) |
| `FLOOD_DOWNLOAD_WORKERS` | `6` | step 01 download threads |
| `FLOOD_TILE_SIZE` | `1024` | tile size for the windowed read/write |
| `FLOOD_KEEP_INTERMEDIATE` | *(unset)* | `1` keeps the pre-COG GeoTIFFs |
| `FLOOD_SKIP_DOWNLOAD` | *(unset)* | `1` uses whatever is already in `RAW_DIR` |
| `FLOOD_SOURCE_URLS` / `FLOOD_INDEX_URL` / `FLOOD_BASE_URL` | *see above* | where step 01 gets its file list |

You do **not** type a shapefile path: step 00 downloads it and its location is
derived from the country name. Adding a country = register its download in
`download_shapefile.py` and its resulting filename in `config.py`
(`SHAPEFILE_BY_COUNTRY`). That registry is a copy of the wind pipeline's — when
you add a country to one, add it to the other.

### Multiple countries in one run

The Aqueduct rasters are **global**, so they're downloaded **once** into a shared
`raw_tif/` folder and clipped to each country. Processing many countries does
**not** re-download.

```bash
FLOOD_COUNTRIES=all python run_all.py --yes
```

Preview the plan and every resolved path without touching anything:

```bash
python run_all.py --dry-run
```

---

## How the clipping works

Exactly as the original `FloodRiverine`/`FloodCoastal` scripts did it, and
deliberately so:

1. The country mask is rasterized over the **full global grid** (43200 × 21600).
2. The output keeps the **full global extent** — a world-sized raster with the
   country's data in it and NaN everywhere else.
3. The read/compute/write loop is tiled, so memory stays bounded by
   `n_inputs × tile × tile` even though the extent is global.

**No bounding box is computed anywhere, and the shapefile is never clipped,
filtered or reduced.** Every feature you give it is used in full.

### Why not window to the country's bounding box?

Because it is not safe. A bounding box is four numbers and cannot wrap the
antimeridian. For any country with territory on both sides of the dateline it
either degenerates to ~360° wide (saving nothing) or, once "fixed" with a clip
box, **silently deletes real national territory**.

The USA is exactly that case — Alaska's Aleutians sit at both −179.1° and
+179.8°. A clip box that makes the window small drops the western Aleutians,
Guam, the Northern Marianas and American Samoa. Quietly shrinking a country's
boundary to save compute is not a tradeoff worth making, so the pipeline does
not offer it. `config.py` says so at the point where such a setting would go,
and `tests/test_ensemble.py` has an antimeridian regression test that fails
loudly if extent-narrowing is ever reintroduced.

Verified on the real USA shapefile (all 56 features, 15,898,731 masked pixels):

| Territory | Masked px |
|---|---|
| CONUS (Kansas sample) | 302,400 |
| Alaska mainland | 1,678,022 |
| Aleutians **west** of dateline | 2,024 |
| Aleutians **east** of dateline | 1,793 |
| Hawaii | 20,609 |
| Puerto Rico | 9,509 |
| US Virgin Islands | 269 |
| Guam | 681 |
| Northern Marianas | 569 |
| American Samoa | 246 |

The price is speed and scratch disk: every input raster is read in full, so a
group costs ~933M pixels per model rather than a country-sized subset. That is
the same cost the original scripts paid, and it buys a boundary you can defend.

### Optional: trim the blank border (`FLOOD_TRIM=1`)

**Off by default**, so outputs match the original scripts exactly.

When on, the output still covers the country the same way — the difference is
that rows and columns in which *every* pixel is already NoData are not stored.
No data value changes; only the extent does.

```bash
FLOOD_TRIM=1 FLOOD_DATA_ROOT=D:/FloodData python run_all.py --yes
```

This is safe in a way the old clip-box was not, because it **measures the
rasterized mask** rather than predicting from the shapefile's bounding box:

- a row or column is dropped only when it holds no data at all;
- the masked-pixel count is compared before and after, and any mismatch
  abandons the trim and writes the full extent instead;
- the antimeridian needs no special case. If the first *and* last columns both
  hold data — as they do for the USA, via Alaska's Aleutians — there is nothing
  to trim horizontally, so the full width is kept automatically. No country
  list, no hardcoded box.

For the USA the latitude border goes but the width stays: 43200 × 21600 →
~43190 × 10310, about **2.1× fewer pixels**, roughly halving step 04's scratch
requirement. Countries that do not straddle the dateline (Canada, UK, EU,
Australia) trim in both directions and save considerably more.

Nothing moves: every pixel keeps its exact real-world coordinates and the file
records where it sits on Earth, so QGIS/ArcGIS/web maps place it identically.
`tests/test_trim.py` asserts equal data-pixel counts, equal band totals and
identical coordinates between a trimmed and untrimmed run.

### Disk space — read this before running

The finished COGs are small (a USA riverine layer is well under 100 MB, because
the rasters are overwhelmingly NoData). **The conversion to get there is not.**

`rio-cogeo` builds each COG through an **uncompressed intermediate**, and it
creates that intermediate **in the destination folder** — not the system temp,
so `TMPDIR`/`CPL_TMPDIR` will not move it. The requirement scales with the raw
pixel count, not with the finished file.

For a global-extent 43200 × 21600 × 5-band float32 output:

| | |
|---|---|
| Uncompressed size | 18.66 GB |
| GDAL's own stated requirement | 19.16 GB |
| Peak scratch actually observed | ~25.4 GB |
| What step 04's pre-flight demands | ~29.9 GB (1.6× uncompressed) |

The observed peak comes from **one** conversion with free space polled every
12–20 s, so treat it as a floor rather than a precise figure — the pre-flight
factor is deliberately set above it. Plan on the drive holding
`FLOOD_DATA_ROOT` having **~30 GB free**, regardless of how small the results
are. Step 04 checks up front and refuses in seconds with an explicit figure,
rather than dying minutes in:

```bash
FLOOD_DATA_ROOT=D:/FloodData python run_all.py --yes
```

Files are converted one at a time, so the requirement is per-file, not
cumulative — but raising `FLOOD_PROCESS_WORKERS` does not parallelise step 04.

---

## Output

```
<data>/FloodData/<Country>/results/cog/<hazard>_<scenario>_<year>_<rp>.tif
```

e.g. `riverine_rcp45_2030_rp0002.tif`. Scenarios are normalised `rcp4p5` →
`rcp45`, and return periods to a canonical 4 digits, so riverine `rp00002` and
coastal `rp0002` both become `rp0002` and the two hazards agree.

**Riverine — 5 bands, float32, NaN outside the country/flood extent:**

| Band | Layer | Units |
|---|---|---|
| 1 | mean_flood_depth | m |
| 2 | flood_depth_stddev | m |
| 3 | median_flood_depth | m |
| 4 | peak_flood_depth | m |
| 5 | model_count | models |

Band 5 is what makes the others interpretable: a deep mean backed by 1 of 5
models is not the same claim as one backed by 5. Where no model has data, bands
1–4 are NaN and band 5 is 0.

**Coastal — 4 bands.** These are four distinct scenarios, not samples of one
quantity, so they are stacked rather than averaged:

| Band | Layer | Units |
|---|---|---|
| 1 | depth_nosub_p05 | m |
| 2 | depth_nosub_p50 | m |
| 3 | depth_wtsub_p05 | m |
| 4 | depth_wtsub_p50 | m |

Band descriptions, unit tags and dataset tags (hazard, scenario, year, return
period, contributing models) survive the COG conversion and are readable by the
frontend.

---

## Heads-up before the first run

- **Large download.** The Aqueduct rasters are global; clipping happens
  afterwards. Expect tens of GB — when the source is reachable again.
- **Resumable.** Completed files are skipped; an interrupted download leaves a
  `.part` that is restarted cleanly rather than mistaken for complete. Steps
  02–04 skip groups whose output already exists.
- **Auto-cleanup (step 05).** After a successful run the pre-COG GeoTIFFs are
  deleted, keeping the raw downloads, the shapefile and the final COGs. A
  pre-COG file is deleted only when **its own** COG exists, so a partly converted
  run never loses inputs it still needs. `FLOOD_KEEP_INTERMEDIATE=1` keeps
  everything.
- **Groups are skipped, not fudged.** A coastal group missing any of its four
  variants is skipped rather than written with a band silently absent — band
  order is positional and has to stay trustworthy. A riverine group with a model
  count other than 5 still processes, but warns.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Step 01 `Could not get a file list` | The upstream source is down — see the warning at the top |
| `No riverine rasters found under …` | `RAW_DIR` is empty; populate it and use `FLOOD_SKIP_DOWNLOAD=1` |
| Step 02/03 killed / OOM | Lower `FLOOD_PROCESS_WORKERS` to 1; the full-grid mask is ~0.9 GB per worker |
| Step 04 `Not enough free space` / `No space left on device` | Needs ~30 GB scratch **in the output folder**; move `FLOOD_DATA_ROOT` to a bigger drive. `TMPDIR` will not help |
| Outputs are global-extent and mostly NaN | Expected — see *How the clipping works*. Overviews and LZW keep the COGs small |
| `Could not determine the shapefile path for '<country>'` | Not registered yet — see *Configuration* |
| `Raster grid mismatch` | Inputs are on different grids; they must share one global grid |

## Repo layout

```
config.py                  # single source of settings (+ env overrides)
naming.py                  # Aqueduct filename grammar + normalisation
raster_utils.py            # country window, mask, tiled multi-band writer
run_all.py                 # orchestrator (global download once, then per country)
download_shapefile.py      # step 00: boundary shapefile registry + fetch
0X-*.py                    # pipeline stages
tests/                     # plain scripts (no pytest), need no data or network
requirements.txt           # dependencies
FloodRiverine/             # superseded — see below
FloodCoastal/              # superseded — see below
```

### Relationship to `FloodRiverine/` and `FloodCoastal/`

Those folders hold the original scripts this pipeline replaces. They are kept for
reference and are **not** wired into `run_all.py`. Differences that matter:

- They were hardcoded to a single US state shapefile and to lab-machine Linux
  paths; there was no country loop.
- Riverine wrote its ensemble *into the folder it scanned*, so re-runs depended on
  an `_aggregated_` filename filter to avoid eating their own output. This
  pipeline writes to a separate `results/` tree, and `naming.py` additionally
  rejects `_aggregated_` files so a stale one in `RAW_DIR` can never be ingested
  as if it were a GCM.
- COG conversion shelled out to `gdal_translate`; this uses `rio-cogeo`, so no
  GDAL command-line tool needs to be on `PATH`, and every output is validated.
- Nothing deleted intermediates.

The **boundary and masking behaviour is intentionally identical** to theirs —
full-grid rasterization, full-extent output, whole shapefile used. Outputs from
the old scripts and this pipeline agree pixel-for-pixel on the same inputs; what
changed is the country loop, the naming, COG validation and cleanup.
