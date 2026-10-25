"""
Re-running a finished country must not rebuild it.

Step 05 deletes the pre-COG GeoTIFF once its own COG exists, so after a country
completes, results/ holds nothing and results/cog/ holds everything. A skip
check that looks only at results/<name>.tif goes stale at exactly that point,
and a re-run would redo every completed country from scratch.

Run: python tests/test_resume.py
"""
import os
import shutil
import sys
import tempfile

FLOOD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FLOOD)
import raster_utils

fails = []


def check(label, cond):
    print(f"[{'ok' if cond else 'FAIL'}]   {label}")
    if not cond:
        fails.append(label)


tmp = tempfile.mkdtemp()
results = os.path.join(tmp, "results")
cog = os.path.join(results, "cog")
os.makedirs(cog)
NAME = "coastal_rcp45_2030_rp0100.tif"

# 1) Nothing built yet
check("nothing built -> not skipped", not raster_utils.already_built(results, NAME))

# 2) Mid-run: pre-COG written, step 04 not reached yet
open(os.path.join(results, NAME), "w").close()
check("pre-COG present -> skipped", raster_utils.already_built(results, NAME))

# 3) After step 04 but before step 05: both exist
open(os.path.join(cog, NAME), "w").close()
check("pre-COG and COG present -> skipped", raster_utils.already_built(results, NAME))

# 4) After step 05: the pre-COG is gone, only the COG remains.
#    This is the case the old check got wrong.
os.remove(os.path.join(results, NAME))
check("COG only (country finished) -> still skipped",
      raster_utils.already_built(results, NAME))

# 5) A different group in the same finished country is still unbuilt
check("a different group is not skipped",
      not raster_utils.already_built(results, "coastal_rcp85_2080_rp0002.tif"))

# 6) A finished country: every COG present, results/ empty -> all skipped
shutil.rmtree(tmp)
tmp = tempfile.mkdtemp()
results = os.path.join(tmp, "results")
cog = os.path.join(results, "cog")
os.makedirs(cog)
names = [f"riverine_rcp45_2030_rp{rp:04d}.tif" for rp in (2, 5, 10, 25, 50)]
for n in names:
    open(os.path.join(cog, n), "w").close()
check("finished country: all groups skipped, none rebuilt",
      all(raster_utils.already_built(results, n) for n in names))
check("results/ is genuinely empty (step 05 really did delete)",
      [f for f in os.listdir(results) if f.endswith(".tif")] == [])

print()
if fails:
    print(f"{len(fails)} FAILURE(S): " + "; ".join(fails))
    sys.exit(1)
print("resume logic correct")
