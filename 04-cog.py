"""
Step 04 — Convert the per-country result GeoTIFFs into Cloud-Optimized
GeoTIFFs (COGs).

Input  : {OUTPUT_ROOT}/results/*.tif        (from steps 02 and 03)
Output : {OUTPUT_ROOT}/results/cog/*.tif    (valid COGs, same names)

Band descriptions, unit tags, CRS and NoData all carry through unchanged; only
the internal layout and the added overviews differ. Every output is validated
with cog_validate before being counted as done — step 05 will only delete
intermediates once real COGs exist here.

Uses rio-cogeo rather than shelling out to gdal_translate, so no GDAL command
line tool needs to be on PATH.
"""

import os
import sys
import glob
import shutil

import rasterio
from rio_cogeo.cogeo import cog_translate, cog_validate
from rio_cogeo.profiles import cog_profiles

import config

INPUT_DIR = os.path.join(config.OUTPUT_ROOT, "results")
COG_DIR = os.path.join(INPUT_DIR, "cog")

OVERVIEW_RESAMPLING = "average"
BLOCKSIZE = 512

# Scratch-space requirement. rio-cogeo builds the COG via an intermediate file
# that it creates IN THE DESTINATION DIRECTORY (not the system temp, so TMPDIR
# and CPL_TMPDIR do not move it), and that intermediate is written UNCOMPRESSED
# so overviews can be built over it cheaply.
#
# For a global-extent 43200x21600 5-band float32 output that is ~17.4 GB of
# intermediate, measured at ~23.7 GB peak once overviews and the final copy are
# counted -- even though the finished COG is well under 100 MB, because the
# rasters are overwhelmingly NoData.
#
# GDAL's own CHECK_DISK_FREE_SPACE guard catches this correctly and is left
# ENABLED on purpose: an early, explicit "needs N bytes" beats running for
# minutes and dying on "No space left on device". The pre-flight below just
# reports the same thing in terms the operator can act on.
# Peak scratch / uncompressed size.
#
# Evidence, such as it is: ONE conversion of ONE file (the 43200x21600 5-band
# USA riverine layer), with free space polled every 12-20 s. Free space fell
# 94.4 -> 70.7 GiB, i.e. a 25.4 GB peak against 18.66 GB uncompressed, a ratio
# of 1.36. The true peak may have been higher between samples, and GDAL's own
# independent estimate for the same file was 19.16 GB.
#
# 1.36 is therefore a floor, not a measurement, so this is set to 1.6 to leave
# real margin. It only controls the wording and timing of the failure -- GDAL's
# CHECK_DISK_FREE_SPACE is the actual backstop -- so erring high costs nothing
# but a slightly early refusal, while erring low costs a multi-minute run that
# dies on "No space left on device".
SCRATCH_FACTOR = 1.6


def scratch_needed(path):
    """Bytes of free space the conversion of `path` will require."""
    with rasterio.open(path) as src:
        uncompressed = src.width * src.height * src.count * 4
    return int(uncompressed * SCRATCH_FACTOR)


def free_bytes(path):
    usage = shutil.disk_usage(path)
    return usage.free


def build_profile():
    """LZW with the floating-point predictor — the data is float32 depth."""
    profile = cog_profiles.get("lzw")
    profile.update(predictor=3, blockxsize=BLOCKSIZE, blockysize=BLOCKSIZE)
    return profile


def make_cog(src_path, dst_path, profile):
    cog_translate(
        src_path, dst_path, profile,
        overview_resampling=OVERVIEW_RESAMPLING,
        web_optimized=False,        # keep native CRS; no reprojection
        forward_band_tags=True,     # carry per-band tags (units) into the COG
        in_memory=False,            # never buffer a global raster in RAM
        quiet=True,
    )


def main():
    print("=" * 62)
    print(f"STEP 04 — COG conversion for: {config.COUNTRY}")
    print("=" * 62)

    sources = sorted(glob.glob(os.path.join(INPUT_DIR, "*.tif")))
    if not sources:
        print(f"No GeoTIFFs found in {INPUT_DIR}")
        print("Run steps 02/03 first.")
        return

    os.makedirs(COG_DIR, exist_ok=True)

    # ---- Pre-flight: scratch space, checked before any work is done ----
    pending = [s for s in sources
               if not os.path.exists(os.path.join(COG_DIR, os.path.basename(s)))]
    if pending:
        need = max(scratch_needed(s) for s in pending)
        have = free_bytes(COG_DIR)
        print(f"Scratch space: {have/1e9:.1f} GB free, "
              f"{need/1e9:.1f} GB needed for the largest conversion.")
        if have < need:
            print(f"\n[ERROR] Not enough free space on the drive holding {COG_DIR}")
            print(f"        free   : {have/1e9:6.1f} GB")
            print(f"        needed : {need/1e9:6.1f} GB")
            print()
            print("rio-cogeo builds each COG through an UNCOMPRESSED intermediate")
            print("created in the destination folder, so the requirement scales with")
            print("the raster's full pixel count, not with the finished COG (which")
            print("is tiny here — the outputs are mostly NoData).")
            print()
            print("Setting TMPDIR/CPL_TMPDIR does NOT help: the intermediate is")
            print("placed next to the output, not in the system temp directory.")
            print("Point the whole output tree at a roomier drive instead:")
            print("    FLOOD_DATA_ROOT=D:/FloodData")
            sys.exit(1)
        print()

    profile = build_profile()
    print(f"Converting {len(sources)} file(s)...\n")

    ok, bad = 0, 0
    for src in sources:
        name = os.path.basename(src)
        dst = os.path.join(COG_DIR, name)

        if os.path.exists(dst):
            print(f"[skip]  {name}  (already converted)")
            ok += 1
            continue

        print(f"[..]    {name}")
        tmp = dst + ".part"
        try:
            make_cog(src, tmp, profile)
        except Exception as e:
            print(f"        [error] conversion failed: {e}")
            if os.path.exists(tmp):
                os.remove(tmp)
            bad += 1
            continue

        is_valid, errors, warnings = cog_validate(tmp)
        for w in warnings:
            print(f"        [warn] {w}")
        for e in errors:
            print(f"        [error] {e}")

        if is_valid:
            os.replace(tmp, dst)
            print(f"[ok]    {name}  -> {dst}")
            ok += 1
        else:
            os.remove(tmp)
            print(f"[FAIL]  {name}  is not a valid COG — not kept")
            bad += 1

    print(f"\nDone. {ok} valid, {bad} failed. COGs in {COG_DIR}")

    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
