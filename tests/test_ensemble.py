"""
Synthetic multi-model test of the ensemble math and the country clip.

Uses only generated rasters, so it needs no Aqueduct data and no network.
Run: python tests/test_ensemble.py
"""
import importlib.util
import os
import sys
import tempfile

import numpy as np
import rasterio
from rasterio.transform import from_origin
import geopandas as gpd
from shapely.geometry import box

FLOOD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FLOOD)
import raster_utils


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), os.path.join(FLOOD, name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


river = load_script("02-riverine-ensemble.py")
coastal = load_script("03-coastal-stack.py")

fails = []


def check(label, cond):
    if cond:
        print(f"[ok]   {label}")
    else:
        fails.append(label)
        print(f"[FAIL] {label}")


# =====================================================================
# 1) ensemble_stats on a hand-computed stack
# =====================================================================
NAN = np.nan
stack = np.array([
    [[1.0, 2.0, NAN, NAN]],
    [[3.0, 4.0, 5.0, NAN]],
    [[5.0, 9.0, NAN, NAN]],
], dtype=np.float32)

out = river.ensemble_stats(stack)

check("mean  col0 = 3.0", np.isclose(out[0][0, 0], 3.0))
check("mean  col1 = 5.0", np.isclose(out[1 - 1][0, 1], 5.0))
check("std   col0 = 1.633 (population)", np.isclose(out[1][0, 0], np.std([1, 3, 5]), atol=1e-5))
check("median col0 = 3.0", np.isclose(out[2][0, 0], 3.0))
check("max   col1 = 9.0", np.isclose(out[3][0, 1], 9.0))
check("count col0 = 3", out[4][0, 0] == 3)
check("count col2 = 1 (one model only)", out[4][0, 2] == 1)
check("mean  col2 = 5.0 (single contributing model)", np.isclose(out[0][0, 2], 5.0))
check("std   col2 = 0.0", np.isclose(out[1][0, 2], 0.0))
check("count col3 = 0 (no data at all)", out[4][0, 3] == 0)
check("mean  col3 is NaN", np.isnan(out[0][0, 3]))
check("std   col3 is NaN", np.isnan(out[1][0, 3]))
check("max   col3 is NaN", np.isnan(out[3][0, 3]))
check("model_count is never NaN", not np.isnan(out[4]).any())

# =====================================================================
# 2) end-to-end process_group with 3 synthetic models + a polygon
# =====================================================================
tmp = tempfile.mkdtemp()

# 20x20 grid at 1 degree, origin (0, 20) -> covers lon 0..20, lat 0..20
transform = from_origin(0, 20, 1.0, 1.0)
profile = dict(driver="GTiff", height=20, width=20, count=1,
               dtype="float32", crs="EPSG:4326", transform=transform,
               nodata=-9999.0)

model_paths = []
for i, value in enumerate([2.0, 4.0, 6.0], start=1):
    arr = np.full((20, 20), value, dtype=np.float32)
    arr[0:5, :] = -9999.0          # a nodata band across the top
    p = os.path.join(tmp, f"m{i}.tif")
    with rasterio.open(p, "w", **profile) as dst:
        dst.write(arr, 1)
    model_paths.append(p)

# Boundary: lon 5..10, lat 5..10 (well inside the raster, away from nodata)
gdf = gpd.GeoDataFrame(geometry=[box(5, 5, 10, 10)], crs="EPSG:4326")

out_path = os.path.join(tmp, "out.tif")
written = raster_utils.process_group(
    src_paths=model_paths,
    out_path=out_path,
    gdf=gdf,
    compute_fn=river.ensemble_stats,
    n_out_bands=5,
    band_descriptions=river.BAND_DESCRIPTIONS,
    band_units=river.BAND_UNITS,
    dataset_tags={"hazard": "riverine", "test": "yes"},
    tile_size=4,          # force multiple tiles so the tiling path is exercised
    crs="EPSG:4326",
)
check("process_group returned a path", written == out_path)

d = rasterio.open(out_path)
print(f"\n   output size {d.width}x{d.height} (source 20x20), bounds {d.bounds}")
# The boundary must NEVER shrink the output extent: a bounding box cannot wrap
# the antimeridian, so any extent-narrowing risks dropping real territory.
check("output keeps the FULL source extent", d.width == 20 and d.height == 20)
check("output transform matches the source", d.transform.almost_equals(transform))
check("5 bands written", d.count == 5)
check("nodata is NaN", np.isnan(d.nodata))

mean = d.read(1)
std = d.read(2)
mx = d.read(4)
cnt = d.read(5)
valid = ~np.isnan(mean)

check("some pixels are valid", valid.sum() > 0)
check("mean of 2,4,6 = 4", np.allclose(mean[valid], 4.0))
check("std  of 2,4,6 = 1.633", np.allclose(std[valid], np.std([2, 4, 6]), atol=1e-5))
check("max  of 2,4,6 = 6", np.allclose(mx[valid], 6.0))
check("count = 3 inside polygon", np.all(cnt[valid] == 3))
check("-9999 never appears as a value", np.nanmin(mean) > -1000)
check("band descriptions set", d.descriptions[0] == "mean_flood_depth")
check("dataset tag set", d.tags().get("hazard") == "riverine")

# Pixels outside the polygon but inside the window must be NaN.
check("outside-polygon pixels are NaN", np.isnan(mean).sum() > 0)
d.close()

# =====================================================================
# 3) tile_size must not change the result
# =====================================================================
out2 = os.path.join(tmp, "out2.tif")
raster_utils.process_group(
    src_paths=model_paths, out_path=out2, gdf=gdf,
    compute_fn=river.ensemble_stats, n_out_bands=5,
    band_descriptions=river.BAND_DESCRIPTIONS, band_units=river.BAND_UNITS,
    dataset_tags={}, tile_size=1024, crs="EPSG:4326",
)
a = rasterio.open(out_path).read()
b = rasterio.open(out2).read()
check("tiled and untiled results are identical", np.allclose(a, b, equal_nan=True))

# =====================================================================
# 4) no overlap between raster and boundary -> None, no file
# =====================================================================
far = gpd.GeoDataFrame(geometry=[box(100, 100, 110, 110)], crs="EPSG:4326")
out3 = os.path.join(tmp, "out3.tif")
res = raster_utils.process_group(
    src_paths=model_paths, out_path=out3, gdf=far,
    compute_fn=river.ensemble_stats, n_out_bands=5,
    band_descriptions=river.BAND_DESCRIPTIONS, band_units=river.BAND_UNITS,
    dataset_tags={}, tile_size=1024, crs="EPSG:4326",
)
check("non-overlapping boundary returns None", res is None)

# =====================================================================
# 4b) ANTIMERIDIAN REGRESSION — the important one.
#
# A country with territory on both sides of the dateline (the USA, via
# Alaska's Aleutians) must keep BOTH sides. Any attempt to narrow the output
# to the boundary's bounding box breaks this, because a bounding box cannot
# wrap: it would either span the whole globe or, if clipped, silently delete
# one side's territory. This test fails loudly if that is ever reintroduced.
# =====================================================================
world_transform = from_origin(-180, 90, 1.0, 1.0)      # 360x180 at 1 degree
world_profile = dict(driver="GTiff", height=180, width=360, count=1,
                     dtype="float32", crs="EPSG:4326",
                     transform=world_transform, nodata=-9999.0)
world_path = os.path.join(tmp, "world.tif")
with rasterio.open(world_path, "w", **world_profile) as dst:
    dst.write(np.full((180, 360), 7.0, dtype=np.float32), 1)

# Two landmasses either side of the dateline, as Alaska really is.
straddle = gpd.GeoDataFrame(
    geometry=[box(-179, 50, -176, 53),     # west of the dateline
              box(176, 50, 179, 53)],      # east of the dateline
    crs="EPSG:4326")

out4 = os.path.join(tmp, "straddle.tif")
raster_utils.process_group(
    src_paths=[world_path], out_path=out4, gdf=straddle,
    compute_fn=river.ensemble_stats, n_out_bands=5,
    band_descriptions=river.BAND_DESCRIPTIONS, band_units=river.BAND_UNITS,
    dataset_tags={}, tile_size=64, crs="EPSG:4326",
)
ds = rasterio.open(out4)
check("antimeridian: output spans the full globe", ds.width == 360)
sm = ds.read(1)
# Column index of each landmass on a -180..180 grid at 1 degree.
west = sm[:, 0:5]          # lon -180..-175
east = sm[:, 355:360]      # lon  175..180
check("antimeridian: WEST-of-dateline territory kept",
      np.sum(~np.isnan(west)) > 0)
check("antimeridian: EAST-of-dateline territory kept",
      np.sum(~np.isnan(east)) > 0)
check("antimeridian: both sides have equal area",
      np.sum(~np.isnan(west)) == np.sum(~np.isnan(east)))
check("antimeridian: mid-Pacific between them stays empty",
      np.all(np.isnan(sm[:, 100:250])))
ds.close()

# =====================================================================
# 5) misaligned inputs must raise, not silently misalign bands
# =====================================================================
bad_profile = dict(profile)
bad_profile.update(width=10, height=10)
bad = os.path.join(tmp, "bad.tif")
with rasterio.open(bad, "w", **bad_profile) as dst:
    dst.write(np.zeros((10, 10), dtype=np.float32), 1)

try:
    raster_utils.process_group(
        src_paths=[model_paths[0], bad], out_path=os.path.join(tmp, "x.tif"),
        gdf=gdf, compute_fn=river.ensemble_stats, n_out_bands=5,
        band_descriptions=river.BAND_DESCRIPTIONS, band_units=river.BAND_UNITS,
        dataset_tags={}, tile_size=1024, crs="EPSG:4326",
    )
    check("grid mismatch raises", False)
except ValueError:
    check("grid mismatch raises", True)

# =====================================================================
# 6) coastal band labels / ordering
# =====================================================================
check("coastal variant order",
      coastal.variant_order() == [("nosub", "0_perc_05"), ("nosub", "0_perc_50"),
                                  ("wtsub", "0_perc_05"), ("wtsub", "0_perc_50")])
check("coastal band label", coastal.band_label("wtsub", "0_perc_50") == "depth_wtsub_p50")
check("coastal identity passthrough",
      np.allclose(coastal.identity(stack), stack, equal_nan=True))

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("all ensemble tests passed")
