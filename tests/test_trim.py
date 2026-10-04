"""
Tests for the optional blank-border trim (FLOOD_TRIM).

The contract being tested: trimming may change the output's SIZE, but must
never change a single data VALUE, and must never drop a masked pixel. Anything
it cannot prove lossless must fall back to the full extent.

Uses only generated rasters — no Aqueduct data, no network.
Run: python tests/test_trim.py
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

fails = []


def check(label, cond):
    print(f"[{'ok' if cond else 'FAIL'}]   {label}")
    if not cond:
        fails.append(label)


tmp = tempfile.mkdtemp()


def make_raster(path, width, height, transform, value=5.0):
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=transform, nodata=-9999.0) as dst:
        dst.write(np.full((height, width), value, dtype=np.float32), 1)
    return path


def run(src, gdf, out, trim, tile=64):
    return raster_utils.process_group(
        src_paths=[src], out_path=out, gdf=gdf,
        compute_fn=river.ensemble_stats, n_out_bands=5,
        band_descriptions=river.BAND_DESCRIPTIONS,
        band_units=river.BAND_UNITS, dataset_tags={}, tile_size=tile,
        crs="EPSG:4326", trim=trim,
    )


# =====================================================================
# 1) data_extent finds the outermost rows/cols holding data
# =====================================================================
m = np.zeros((7, 8), dtype=bool)
m[2, 2] = m[2, 4] = m[3, 2] = m[3, 4] = m[4, 3] = True
check("data_extent finds the tight box", raster_utils.data_extent(m) == (2, 5, 2, 5))
check("empty mask -> None", raster_utils.data_extent(np.zeros((4, 4), bool)) is None)

full = np.ones((5, 5), dtype=bool)
check("full mask -> whole grid", raster_utils.data_extent(full) == (0, 5, 0, 5))

# A blank row INSIDE the data must be kept (output stays a rectangle).
m2 = np.zeros((5, 5), dtype=bool)
m2[1, 1] = m2[3, 3] = True
check("blank row between data is kept inside the box",
      raster_utils.data_extent(m2) == (1, 4, 1, 4))

# trimmed_window declines when there is nothing to gain
w, why = raster_utils.trimmed_window(full, 5, 5)
check("no-op trim is declined", w is None and "nothing to trim" in why)
w, why = raster_utils.trimmed_window(np.zeros((5, 5), bool), 5, 5)
check("empty mask trim is declined", w is None and "empty" in why)

# =====================================================================
# 2) Trimming must not change any value — only the extent
# =====================================================================
transform = from_origin(0, 20, 1.0, 1.0)          # 20x20 grid, lon 0..20
src = make_raster(os.path.join(tmp, "src.tif"), 20, 20, transform)
gdf = gpd.GeoDataFrame(geometry=[box(5, 5, 10, 10)], crs="EPSG:4326")

untrimmed = os.path.join(tmp, "untrimmed.tif")
trimmed = os.path.join(tmp, "trimmed.tif")
run(src, gdf, untrimmed, trim=False)
run(src, gdf, trimmed, trim=True)

du = rasterio.open(untrimmed)
dt = rasterio.open(trimmed)
print(f"\n   untrimmed {du.width}x{du.height}   trimmed {dt.width}x{dt.height}")
check("untrimmed keeps the full 20x20", (du.width, du.height) == (20, 20))
check("trimmed is smaller", dt.width < du.width and dt.height < du.height)

au = du.read(1)
at = dt.read(1)
check("same number of data pixels",
      np.sum(~np.isnan(au)) == np.sum(~np.isnan(at)))
check("same total depth (no value changed)",
      np.isclose(np.nansum(au), np.nansum(at)))
check("same model_count total",
      np.isclose(np.nansum(du.read(5)), np.nansum(dt.read(5))))

# The trimmed pixels must sit at the SAME real-world coordinates.
rows, cols = np.where(~np.isnan(at))
xs_t, ys_t = rasterio.transform.xy(dt.transform, rows, cols)
rows_u, cols_u = np.where(~np.isnan(au))
xs_u, ys_u = rasterio.transform.xy(du.transform, rows_u, cols_u)
check("data lands at identical real-world coordinates",
      np.allclose(sorted(xs_t), sorted(xs_u)) and
      np.allclose(sorted(ys_t), sorted(ys_u)))
check("trimmed bounds sit inside the untrimmed bounds",
      dt.bounds.left >= du.bounds.left and dt.bounds.right <= du.bounds.right and
      dt.bounds.bottom >= du.bounds.bottom and dt.bounds.top <= du.bounds.top)
du.close()
dt.close()

# =====================================================================
# 3) ANTIMERIDIAN — trimming must NOT narrow a dateline-straddling country
# =====================================================================
wt = from_origin(-180, 90, 1.0, 1.0)              # 360x180 world
world = make_raster(os.path.join(tmp, "world.tif"), 360, 180, wt, value=3.0)
straddle = gpd.GeoDataFrame(
    geometry=[box(-180, 50, -177, 53),            # touches the WEST edge
              box(177, 50, 180, 53)],             # touches the EAST edge
    crs="EPSG:4326")

out = os.path.join(tmp, "straddle_trim.tif")
run(world, straddle, out, trim=True)
d = rasterio.open(out)
print(f"\n   dateline-straddling country, trim ON -> {d.width}x{d.height} (world is 360x180)")
check("antimeridian: FULL WIDTH kept even with trim on", d.width == 360)
check("antimeridian: height still trimmed (latitude never wraps)", d.height < 180)
a = d.read(1)
check("antimeridian: west-edge data survives", np.sum(~np.isnan(a[:, 0:4])) > 0)
check("antimeridian: east-edge data survives", np.sum(~np.isnan(a[:, 356:360])) > 0)
check("antimeridian: both sides equal area",
      np.sum(~np.isnan(a[:, 0:4])) == np.sum(~np.isnan(a[:, 356:360])))

# Compare against the untrimmed run: not one pixel may be lost.
out_full = os.path.join(tmp, "straddle_full.tif")
run(world, straddle, out_full, trim=False)
df = rasterio.open(out_full)
check("antimeridian: trim loses no data pixels",
      np.sum(~np.isnan(a)) == np.sum(~np.isnan(df.read(1))))
d.close()
df.close()

# =====================================================================
# 3b) The REAL USA shape: dateline-straddling but NOT touching +/-180
#     exactly. A thin blank sliver at each side IS trimmed, and both
#     extremes still survive. This is the case the test above misses,
#     because its geometry sits flush against the edges.
# =====================================================================
near = gpd.GeoDataFrame(
    geometry=[box(-179, 50, -176, 53),            # 1 deg short of the west edge
              box(176, 50, 179, 53)],             # 1 deg short of the east edge
    crs="EPSG:4326")

out_near = os.path.join(tmp, "near_trim.tif")
out_near_full = os.path.join(tmp, "near_full.tif")
run(world, near, out_near, trim=True)
run(world, near, out_near_full, trim=False)

dn = rasterio.open(out_near)
dnf = rasterio.open(out_near_full)
print(f"\n   near-edge straddler, trim ON -> {dn.width}x{dn.height} "
      f"(bounds {dn.bounds.left:.0f}..{dn.bounds.right:.0f})")
check("near-edge: a sliver IS trimmed from the sides", dn.width < 360)
check("near-edge: but almost all width is kept", dn.width >= 356)
check("near-edge: west extreme retained", dn.bounds.left <= -179)
check("near-edge: east extreme retained", dn.bounds.right >= 179)
an = dn.read(1)
check("near-edge: first column holds data (trim stopped at real data)",
      np.sum(~np.isnan(an[:, 0])) > 0)
check("near-edge: last column holds data",
      np.sum(~np.isnan(an[:, -1])) > 0)
check("near-edge: no data pixel lost vs untrimmed",
      np.sum(~np.isnan(an)) == np.sum(~np.isnan(dnf.read(1))))
dn.close()
dnf.close()

# =====================================================================
# 4) Data touching every edge -> nothing to trim, full extent kept
# =====================================================================
everywhere = gpd.GeoDataFrame(geometry=[box(-180, -90, 180, 90)], crs="EPSG:4326")
out = os.path.join(tmp, "everywhere.tif")
run(world, everywhere, out, trim=True)
d = rasterio.open(out)
check("data at every edge -> full extent kept", (d.width, d.height) == (360, 180))
d.close()

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("all trim tests passed")
