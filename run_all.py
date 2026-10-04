"""
Run the whole flood pipeline.

The Aqueduct rasters are GLOBAL, so they are downloaded ONCE into a shared
folder (config.RAW_DIR) and then clipped to each country. So the order is:

    step 01  (once)         download the shared global flood rasters
    then, for EACH country:
        step 00             boundary shapefile
        step 02             riverine: clip + collapse the GCM ensemble
        step 03             coastal: clip + stack the subsidence/percentile set
        step 04             convert results to Cloud-Optimized GeoTIFFs
        step 05             delete the regenerable pre-COG intermediates

Which countries are processed comes from config.COUNTRIES
(FLOOD_COUNTRIES="all" or a list; otherwise just the single FLOOD_COUNTRY).

Usage:
    python run_all.py            # confirm, then run
    python run_all.py --yes      # skip the confirmation prompt
    python run_all.py --dry-run  # print the plan (and resolved paths) and exit
"""

import os
import sys
import time
import subprocess

import config

HERE = os.path.dirname(os.path.abspath(__file__))

# Runs ONCE (country-independent) — the big shared download.
GLOBAL_STEPS = [
    ("01-download-flood-data.py", "Download raw Aqueduct rasters — shared across all countries"),
]

# Runs once PER COUNTRY.
PER_COUNTRY_STEPS = [
    ("download_shapefile.py",   "Boundary shapefile"),
    ("02-riverine-ensemble.py", "Riverine: clip to country + ensemble across models"),
    ("03-coastal-stack.py",     "Coastal: clip to country + stack variants"),
    ("04-cog.py",               "Convert to Cloud-Optimized GeoTIFFs"),
    ("05-cleanup.py",           "Remove regenerable intermediates"),
]

ALL_SCRIPTS = [s for s, _ in GLOBAL_STEPS + PER_COUNTRY_STEPS]


def banner(text):
    print("\n" + "=" * 62)
    print(text)
    print("=" * 62, flush=True)


def _country_env(country, multi):
    """Environment for one country's subprocesses."""
    env = os.environ.copy()
    env["FLOOD_COUNTRY"] = country
    if multi:
        # Let each country derive its own output/shapefile paths.
        env.pop("FLOOD_OUTPUT_ROOT", None)
        env.pop("FLOOD_SHAPEFILE", None)
        env.pop("FLOOD_COUNTRIES", None)
    return env


def _run(script, env):
    return subprocess.run([sys.executable, os.path.join(HERE, script)],
                          cwd=HERE, env=env).returncode


def _print_resolved(country, multi):
    """Show the paths a country would resolve to (for the dry-run plan)."""
    env = _country_env(country, multi)
    subprocess.run(
        [sys.executable, "-c",
         "import config; print('\\n'.join('    ' + l for l in config.summary_lines()))"],
        cwd=HERE, env=env,
    )


def _steps_for_run():
    """Skip a hazard step entirely when that hazard is not configured."""
    skip = set()
    if "riverine" not in config.HAZARDS:
        skip.add("02-riverine-ensemble.py")
    if "coastal" not in config.HAZARDS:
        skip.add("03-coastal-stack.py")
    return [(s, d) for s, d in PER_COUNTRY_STEPS if s not in skip]


def main():
    # The step scripts write straight to the console; without this the
    # orchestrator's own banners are block-buffered and surface out of order,
    # making it look like the steps ran before the headings that introduce them.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    args = sys.argv[1:]
    skip_confirm = "--yes" in args
    dry_run = "--dry-run" in args or "--plan" in args

    countries = config.COUNTRIES
    multi = len(countries) > 1
    steps = _steps_for_run()

    banner("FLOOD PIPELINE")
    print(f"Countries   : {', '.join(countries)}")
    print(f"Hazards     : {', '.join(config.HAZARDS)}")
    print(f"Years       : {', '.join(config.YEARS)}")
    print(f"Scenarios   : {', '.join(config.SCENARIOS)}")
    print(f"Return per. : {', '.join(config.RPS)}")
    print(f"Shared raw  : {config.RAW_DIR}")

    # ---- Pre-flight: all scripts present ----
    for script in ALL_SCRIPTS:
        if not os.path.exists(os.path.join(HERE, script)):
            print(f"\n[ERROR] Missing pipeline script: {script}")
            sys.exit(1)

    n_river = len(config.SCENARIOS) * len(config.YEARS) * len(config.RPS)
    print(
        f"\nNOTE: the Aqueduct rasters are GLOBAL and are downloaded ONCE to\n"
        f"      {config.RAW_DIR}\n"
        f"      (roughly {n_river} riverine groups x ~5 models, plus coastal —\n"
        f"      tens of GB) and reused for every country. Adding countries does\n"
        f"      NOT re-download them."
    )

    # ---- Dry run: show the plan and the resolved paths per country ----
    if dry_run:
        banner("PLAN (dry run — nothing will be downloaded or written)")
        print("Global (once):")
        for s, d in GLOBAL_STEPS:
            print(f"  - {s}: {d}")
        for ci, country in enumerate(countries, 1):
            print(f"\nCountry {ci}/{len(countries)}: {country}")
            _print_resolved(country, multi)
            for s, d in steps:
                print(f"      - {s}: {d}")
        return

    if not skip_confirm:
        try:
            input("\nPress Enter to begin  (Ctrl+C to cancel)... ")
        except KeyboardInterrupt:
            print("\nCancelled.")
            sys.exit(1)

    overall_start = time.time()

    # ---- 1) Global download (once) ----
    banner("GLOBAL — Download raw Aqueduct flood data (shared)")
    rc = _run("01-download-flood-data.py", os.environ.copy())
    if rc != 0:
        print(f"\n[ERROR] Download step failed (exit {rc}). Stopping — nothing "
              f"downstream can run without the data.")
        sys.exit(rc)

    # ---- 2) Per-country processing ----
    results = {}
    for ci, country in enumerate(countries, 1):
        banner(f"COUNTRY {ci}/{len(countries)} — {country}")
        env = _country_env(country, multi)
        ok = True
        for i, (script, desc) in enumerate(steps, 1):
            print(f"\n--- [{country}] step {i}/{len(steps)}: {desc} ---", flush=True)
            rc = _run(script, env)
            if rc != 0:
                print(f"\n[ERROR] {country}: '{script}' failed (exit {rc}).")
                ok = False
                if multi:
                    print("Continuing with the next country.")
                break
        results[country] = "ok" if ok else "FAILED"
        if not ok and not multi:
            sys.exit(1)

    # ---- Summary ----
    banner("ALL DONE")
    print(f"Total time: {time.time() - overall_start:.0f}s\n")
    for country, status in results.items():
        mark = "[ok] " if status == "ok" else "[!!] "
        cog = os.path.join(config.DATA_ROOT, "FloodData", country, "results", "cog")
        print(f"  {mark}{country:12s} {status:7s} -> {cog}")

    if any(v != "ok" for v in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
