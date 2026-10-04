"""The mask cache must never serve one boundary's mask for another."""
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

fails = []


def check(label, cond):
    print(f"[{'ok' if cond else 'FAIL'}]   {label}")
    if not cond:
        fails.append(label)


tmp = tempfile.mkdtemp()
transform = from_origin(0, 20, 1.0, 1.0)
p = os.path.join(tmp, "grid.tif")
with rasterio.open(p, "w", driver="GTiff", height=20, width=20, count=1,
                   dtype="float32", crs="EPSG:4326", transform=transform,
                   nodata=-9999.0) as dst:
    dst.write(np.zeros((20, 20), dtype=np.float32), 1)

src = rasterio.open(p)

a = gpd.GeoDataFrame(geometry=[box(2, 2, 6, 6)], crs="EPSG:4326")
b = gpd.GeoDataFrame(geometry=[box(12, 12, 18, 18)], crs="EPSG:4326")

mask_a = raster_utils.build_mask(src, a).copy()
mask_b = raster_utils.build_mask(src, b).copy()   # same grid, different boundary
mask_a2 = raster_utils.build_mask(src, a).copy()  # back to the first

check("different boundaries give different masks", not np.array_equal(mask_a, mask_b))
check("boundary A mask is stable across calls", np.array_equal(mask_a, mask_a2))
check("A covers ~16 px", 10 <= mask_a.sum() <= 25)
check("B covers ~36 px", 25 <= mask_b.sum() <= 45)
check("A and B do not overlap", not (mask_a & mask_b).any())

# A repeat call with the same object must be served from cache (same array).
first = raster_utils.build_mask(src, b)
second = raster_utils.build_mask(src, b)
check("repeat call with same boundary is cached (identical object)",
      first is second)

src.close()
print()
if fails:
    print(f"{len(fails)} FAILURE(S): " + "; ".join(fails))
    sys.exit(1)
print("mask cache correct")
