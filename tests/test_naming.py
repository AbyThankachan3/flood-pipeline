"""Filename-grammar tests. Run: python tests/test_naming.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import naming

fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n     got  {got}\n     want {want}")
        print(f"[FAIL] {label}")
    else:
        print(f"[ok]   {label}")


# --- riverine: the real file sitting in the repo ---
m = naming.parse_riverine("inunriver_rcp4p5_00000NorESM1-M_2030_rp00002.tif")
check("riverine parse", m and (m["scenario"], m["model"], m["year"], m["rp"]),
      ("rcp45", "00000NorESM1-M", "2030", "rp0002"))

# --- coastal ---
m = naming.parse_coastal("inuncoast_rcp8p5_wtsub_2050_rp0100_0_perc_50.tif")
check("coastal parse",
      m and (m["scenario"], m["subsidence"], m["year"], m["rp"], m["projection"]),
      ("rcp85", "wtsub", "2050", "rp0100", "0_perc_50"))

# --- the two hazards must agree on the rp token for the same return period ---
r = naming.parse_riverine("inunriver_rcp4p5_00000NorESM1-M_2030_rp00002.tif")
c = naming.parse_coastal("inuncoast_rcp4p5_nosub_2030_rp0002_0_perc_05.tif")
check("rp normalised across hazards", r["rp"] == c["rp"], True)

# --- historical must be rejected by both ---
check("riverine historical rejected",
      naming.parse_riverine("inunriver_historical_000000000WATCH_1980_rp00002.tif"), None)
check("coastal historical rejected",
      naming.parse_coastal("inuncoast_historical_nosub_hist_rp0002_0.tif"), None)

# --- this pipeline's own outputs must not be re-ingested as inputs ---
check("own riverine output rejected",
      naming.parse_riverine("riverine_rcp45_2030_rp0002.tif"), None)
check("legacy aggregate rejected",
      naming.parse_riverine("inunriver_rcp4p5_aggregated_2030_rp00002.tif"), None)

# --- non-tif / junk ---
check("pickle rejected", naming.parse("inunriver_rcp4p5_x_2030_rp00002.pickle"), None)
check("unrelated rejected", naming.parse("something_else.tif"), None)

# --- scenario round trip ---
check("scenario roundtrip 4p5",
      naming.denormalize_scenario(naming.normalize_scenario("rcp4p5")), "rcp4p5")
check("scenario roundtrip 8p5",
      naming.denormalize_scenario(naming.normalize_scenario("rcp8p5")), "rcp8p5")

# --- rp normalisation table ---
check("rp00002 -> rp0002", naming.normalize_rp("rp00002"), "rp0002")
check("rp0002  -> rp0002", naming.normalize_rp("rp0002"), "rp0002")
check("rp01000 -> rp1000", naming.normalize_rp("rp01000"), "rp1000")
check("rp1000  -> rp1000", naming.normalize_rp("rp1000"), "rp1000")
check("garbage -> None", naming.normalize_rp("banana"), None)

# --- output name ---
check("output name",
      naming.output_name("riverine", "rcp45", "2030", "rp0002"),
      "riverine_rcp45_2030_rp0002.tif")

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all naming tests passed")
