"""
Coastal band naming and ordering, including the 6-band (95th percentile) case.

The trap this guards: Aqueduct's 95th-percentile layer is the bare token "0",
which looks like a null/default placeholder. Deriving a label by stripping
"0_perc_" leaves it as "0", so a six-band output would carry a band called
depth_nosub_0 instead of depth_nosub_p95.

Run: python tests/test_coastal_bands.py
"""
import importlib
import importlib.util
import os
import sys

FLOOD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FLOOD)

fails = []


def check(label, cond):
    print(f"[{'ok' if cond else 'FAIL'}]   {label}")
    if not cond:
        fails.append(label)


def load(percentiles):
    """Reload config + the coastal step with a given percentile set."""
    os.environ["FLOOD_COASTAL_PERCENTILES"] = percentiles
    import config
    importlib.reload(config)
    spec = importlib.util.spec_from_file_location(
        "coastal", os.path.join(FLOOD, "03-coastal-stack.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return config, mod


# ------------------------------------------------- 4 bands (legacy parity)
cfg, coastal = load("0_perc_05,0_perc_50")
order = coastal.variant_order()
labels = [coastal.band_label(s, p) for s, p in order]
print(f"\n4-band (legacy parity): {labels}")
check("4 bands when only p05/p50 are configured", len(order) == 4)
check("4-band labels correct",
      labels == ["depth_nosub_p05", "depth_nosub_p50",
                 "depth_wtsub_p05", "depth_wtsub_p50"])

# ------------------------------------------------- 6 bands (the default)
cfg, coastal = load("0_perc_05,0_perc_50,0")
order = coastal.variant_order()
labels = [coastal.band_label(s, p) for s, p in order]
print(f"6-band (default):       {labels}")
check("6 bands when the 95th is enabled", len(order) == 6)
check("95th percentile labelled p95, not 0",
      labels == ["depth_nosub_p05", "depth_nosub_p50", "depth_nosub_p95",
                 "depth_wtsub_p05", "depth_wtsub_p50", "depth_wtsub_p95"])
check("no band is named with a bare token",
      not any(l.endswith("_0") for l in labels))

# Subsidence is the outer loop, percentile the inner — band order must be
# stable, since consumers index by position.
check("nosub bands come before wtsub",
      all("nosub" in l for l in labels[:3]) and all("wtsub" in l for l in labels[3:]))

# ---------------------------------------------------------------- unknown
try:
    coastal.band_label("nosub", "0_perc_99")
    check("unknown projection raises", False)
except ValueError as e:
    check("unknown projection raises", True)
    check("the error names the known tokens", "0_perc_05" in str(e))

# ------------------------------------------------- source filenames still fine
import naming
check("95th source filename has no percentile suffix",
      naming.coastal_source_name("rcp45", "nosub", "2030", "rp0100", "0")
      == "inuncoast_rcp4p5_nosub_2030_rp0100_0.tif")
check("5th source filename unchanged",
      naming.coastal_source_name("rcp45", "nosub", "2030", "rp0100", "0_perc_05")
      == "inuncoast_rcp4p5_nosub_2030_rp0100_0_perc_05.tif")

# The parser must read the bare-0 name back correctly
meta = naming.parse_coastal("inuncoast_rcp4p5_nosub_2030_rp0100_0.tif")
check("bare-0 filename parses", meta is not None)
check("bare-0 projection read as '0'", meta and meta["projection"] == "0")

print()
if fails:
    print(f"{len(fails)} FAILURE(S): " + "; ".join(fails))
    sys.exit(1)
print("coastal band naming correct for 4- and 6-band output")
