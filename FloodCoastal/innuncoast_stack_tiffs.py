import os
import math
import rasterio
import numpy as np
import geopandas as gpd
from rasterio import features
from rasterio.windows import Window

# -----------------------
# CONFIG
# -----------------------
SOURCE_ROOT = "/home/labadmin/flood/inuncoast_usa/inuncoast"
OUTPUT_ROOT = "/home/labadmin/flood/inuncoast"

USA_SHP = "/home/labadmin/flood/GIS Files/State/cb_2018_us_state_500k.shp"

TILE_SIZE = 1024


# -----------------------
# LOAD SHAPEFILE
# -----------------------
print("Loading USA shapefile...")
gdf = gpd.read_file(USA_SHP)


# -----------------------
# HELPERS
# -----------------------
def find_file(folder, keyword):
    for f in os.listdir(folder):
        if keyword in f.lower() and f.endswith(".tif"):
            return os.path.join(folder, f)
    return None


def build_output_name(year, scenario, rp):
    return f"inuncoast_{scenario}_{year}_{rp}.tif"


def build_mask(ref_ds):
    print("Building USA mask...")

    gdf_proj = gdf.to_crs(ref_ds.crs)

    shapes = [(geom, 1) for geom in gdf_proj.geometry]

    mask = features.rasterize(
        shapes=shapes,
        out_shape=(ref_ds.height, ref_ds.width),
        transform=ref_ds.transform,
        fill=0,
        dtype="uint8"
    )

    return mask.astype(bool)


# -----------------------
# CORE FUNCTION
# -----------------------
def stack_and_clip(files, output_path):

    dsets = [rasterio.open(p) for p in files]
    ref = dsets[0]

    H, W = ref.height, ref.width

    # build USA mask
    usa_mask = build_mask(ref)

    # output profile
    profile = ref.profile.copy()
    profile.update(
        count=4,
        dtype="float32",
        nodata=np.nan,
        compress="deflate",
        tiled=True,
        blockxsize=min(TILE_SIZE, W),
        blockysize=min(TILE_SIZE, H),
    )

    with rasterio.open(output_path, "w", **profile) as dst:

        tiles_x = math.ceil(W / TILE_SIZE)
        tiles_y = math.ceil(H / TILE_SIZE)

        for ty in range(tiles_y):
            for tx in range(tiles_x):

                xoff = tx * TILE_SIZE
                yoff = ty * TILE_SIZE

                w = min(TILE_SIZE, W - xoff)
                h = min(TILE_SIZE, H - yoff)

                window = Window(xoff, yoff, w, h)

                mask_win = usa_mask[yoff:yoff+h, xoff:xoff+w]

                for band_idx, ds in enumerate(dsets):

                    arr = ds.read(1, window=window, masked=True)
                    data = arr.data.astype(np.float32)

                    # apply mask
                    full_mask = arr.mask | (~mask_win)
                    data[full_mask] = np.nan

                    dst.write(data, band_idx + 1, window=window)

    for ds in dsets:
        ds.close()


# -----------------------
# MAIN
# -----------------------
def main():

    total = 0

    for year in os.listdir(SOURCE_ROOT):

        year_path = os.path.join(SOURCE_ROOT, year)
        if not os.path.isdir(year_path):
            continue

        for scenario in os.listdir(year_path):

            scenario_path = os.path.join(year_path, scenario)

            nosub_root = os.path.join(scenario_path, "nosub")
            wtsub_root = os.path.join(scenario_path, "wtsub")

            if not os.path.exists(nosub_root) or not os.path.exists(wtsub_root):
                continue

            for rp in os.listdir(nosub_root):

                nosub_rp = os.path.join(nosub_root, rp)
                wtsub_rp = os.path.join(wtsub_root, rp)

                if not os.path.exists(wtsub_rp):
                    continue

                print(f"\n📂 Processing: {year}/{scenario}/{rp}")

                nosub_p05 = find_file(nosub_rp, "perc_05")
                nosub_p50 = find_file(nosub_rp, "perc_50")

                wtsub_p05 = find_file(wtsub_rp, "perc_05")
                wtsub_p50 = find_file(wtsub_rp, "perc_50")

                if None in [nosub_p05, nosub_p50, wtsub_p05, wtsub_p50]:
                    print("   ❌ Missing files, skipping")
                    continue

                out_dir = os.path.join(OUTPUT_ROOT, year, scenario, rp)
                os.makedirs(out_dir, exist_ok=True)

                out_name = build_output_name(year, scenario, rp)
                out_path = os.path.join(out_dir, out_name)

                if os.path.exists(out_path):
                    print("   ✔ Already exists, skipping")
                    continue

                files = [
                    nosub_p05,
                    nosub_p50,
                    wtsub_p05,
                    wtsub_p50
                ]

                stack_and_clip(files, out_path)

                print("   ✅ Created:", out_name)

                total += 1

    print("\n🎉 DONE. Total created:", total)


if __name__ == "__main__":
    main()