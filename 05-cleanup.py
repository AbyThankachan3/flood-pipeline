"""
Step 05 — Remove the regenerable intermediates after a successful run.

Runs last. The only intermediate this pipeline produces is the pre-COG
multi-band GeoTIFF in results/; the COG in results/cog/ supersedes it.

  deleted : {OUTPUT_ROOT}/results/*.tif   (pre-COG), and any stale *.part
  kept    : the raw Aqueduct downloads, the boundary shapefile, and the final
            COGs in {OUTPUT_ROOT}/results/cog/

Safety:
  * A pre-COG file is deleted only when ITS OWN COG exists — not merely when
    some COG exists — so a partially converted run never loses the inputs it
    still needs.
  * Only ever touches paths inside OUTPUT_ROOT.
  * Skipped entirely if FLOOD_KEEP_INTERMEDIATE is set.

To rebuild a deleted intermediate later, re-run steps 02-04. The raw downloads
are the source of truth and are never touched, so nothing is re-downloaded.
"""

import os
import glob

import config

RESULTS_DIR = os.path.join(config.OUTPUT_ROOT, "results")
COG_DIR = os.path.join(RESULTS_DIR, "cog")


def _mb(n):
    return f"{n / (1024 ** 2):.1f} MB"


def main():
    print("=" * 62)
    print(f"STEP 05 — Cleanup of intermediates for: {config.COUNTRY}")
    print("=" * 62)

    if config.KEEP_INTERMEDIATE:
        print("FLOOD_KEEP_INTERMEDIATE is set — keeping all intermediates.")
        return

    cogs = {os.path.basename(p) for p in glob.glob(os.path.join(COG_DIR, "*.tif"))}
    if not cogs:
        print(f"[skip] No final COGs found in {COG_DIR}.")
        print("Not deleting anything (nothing verified to keep).")
        return

    pre_cog = sorted(glob.glob(os.path.join(RESULTS_DIR, "*.tif")))
    freed = 0
    removed = 0
    kept_back = []

    for path in pre_cog:
        name = os.path.basename(path)
        if name not in cogs:
            kept_back.append(name)
            continue
        try:
            size = os.path.getsize(path)
            os.remove(path)
            freed += size
            removed += 1
        except OSError as e:
            print(f"  [warn] could not remove {name}: {e}")

    # Stale partials from an interrupted run are always safe to drop.
    stale = glob.glob(os.path.join(RESULTS_DIR, "*.part")) + \
        glob.glob(os.path.join(COG_DIR, "*.part"))
    for path in stale:
        try:
            freed += os.path.getsize(path)
            os.remove(path)
        except OSError:
            pass

    print(f"Final COGs present: {len(cogs)}")
    print(f"Removed {removed} pre-COG file(s)"
          + (f" and {len(stale)} stale partial(s)" if stale else ""))

    if kept_back:
        print(f"\nKept {len(kept_back)} pre-COG file(s) that have no COG yet:")
        for name in kept_back:
            print(f"  {name}")
        print("Re-run step 04 to convert them.")

    print(f"\n[ok] Freed {_mb(freed)}.")
    print(f"Kept: raw downloads ({config.RAW_DIR}), shapefile, and {COG_DIR}")


if __name__ == "__main__":
    main()
