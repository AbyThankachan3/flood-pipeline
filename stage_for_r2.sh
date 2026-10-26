#!/usr/bin/env bash
# ============================================================
#  Stage the final flood COGs into the R2 upload layout.
#
#  This script does NOT upload. It copies into a local tree that mirrors the
#  R2 key layout exactly, so whoever does the upload can sync it as-is.
#
#  Reorganises:
#     <SRC>/<Country>/results/cog/<hazard>_<scenario>_<year>_<rp>.tif
#  into:
#     <DEST>/<iso>/<hazard>/<scenario>/<year>/<rp>/cog.tiff
#
#  e.g.  FloodData/UK/results/cog/coastal_rcp45_2030_rp0100.tif
#     -> r2_upload/flood/gb/coastal/rcp45/2030/rp0100/cog.tiff
#
#  Country second, mirroring wind's wind/<iso>/<ssp>/<year>/cog.tiff, so the
#  second segment is the ISO code for every hazard.
#
#  Files are HARDLINKED by default, so the staged tree costs no extra disk
#  (set LINK=0 for real copies). Originals are untouched either way. Then
#  upload <DEST> as-is, e.g.
#     rclone copy <DEST> r2:<bucket>/flood
#
#  Usage:
#     ./stage_for_r2.sh
#     ./stage_for_r2.sh --dry-run
#     SRC=/mnt/disks/winddata/flooddata/FloodData \
#       DEST=/mnt/disks/winddata/r2_upload/flood ./stage_for_r2.sh
# ============================================================
set -euo pipefail

SRC="${SRC:-/mnt/disks/winddata/flooddata/FloodData}"
DEST="${DEST:-/mnt/disks/winddata/r2_upload/flood}"

DRY_RUN=0
# Hardlink instead of copying. The staged tree is a read-only handoff on the
# same disk, so linking avoids duplicating tens of GB. LINK=0 forces real
# copies (needed only if the uploader must survive the source being deleted).
LINK="${LINK:-1}"
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# Folder name (as it appears under FloodData/) -> ISO 3166-1 alpha-2.
# "eu" is the exceptionally-reserved EU code; "gb" is the ISO code for the UK
# (wind uses gb, so flood matches). Keep this table in step with wind's.
declare -A CODE=(
  [USA]=us
  [Canada]=ca
  [EU]=eu
  [UK]=gb
  [Australia]=au
  ["Saudi Arabia"]=sa
  [UAE]=ae
  [Qatar]=qa
  [Kuwait]=kw
  [Bahrain]=bh
  [Oman]=om
  [Singapore]=sg
)

# Scenario token in the KEY.
#
# Default is RCP, unchanged from the pipeline's own filenames, because that is
# what flood has always used: the legacy innunriver_ensemble.py wrote
# inunriver_rcp4p5_aggregated_..., and innuncoast_download_data.py had
# SCENARIOS = ["rcp4p5", "rcp8p5"]. The rasters are CMIP5, so RCP is also the
# accurate name. Staging therefore translates nothing.
#
# Wind is genuinely CMIP6 and keys on ssp245/ssp585; flood does not, and the
# two are different scenario families that happen to share a forcing level. If
# the frontend sends ssp245 for flood, the backend aliases it to rcp45 when
# building the key -- one explicit mapping, rather than a silent rename here.
#
# USE_SSP=1 stages ssp245/ssp585 instead, if that is ever wanted.
declare -A SCENARIO=(
  [rcp45]=rcp45
  [rcp85]=rcp85
)
if [ "${USE_SSP:-0}" = "1" ]; then
  SCENARIO=( [rcp45]=ssp245 [rcp85]=ssp585 )
fi

echo "Source : $SRC"
echo "Dest   : $DEST"
echo "Mode   : $([ "$DRY_RUN" = 1 ] && echo 'DRY RUN (nothing written)' || { [ "$LINK" = 1 ] && echo 'hardlink (no extra disk used)' || echo 'copy'; })"
echo "Scenario naming: $([ "${USE_SSP:-0}" = 1 ] && echo 'ssp245/ssp585' || echo 'rcp45/rcp85 (CMIP5, as the pipeline writes them)')"
echo

if [ ! -d "$SRC" ]; then
  echo "[ERROR] Source not found: $SRC" >&2
  exit 1
fi

staged=0
skipped_country=0
skipped_file=0
collisions=0

for dir in "$SRC"/*/; do
  [ -d "$dir" ] || continue
  country="$(basename "$dir")"
  code="${CODE[$country]:-}"
  if [ -z "$code" ]; then
    echo "[skip] no ISO code mapped for country folder '$country'"
    skipped_country=$((skipped_country + 1))
    continue
  fi

  cogdir="$dir/results/cog"
  if [ ! -d "$cogdir" ]; then
    echo "[skip] $country: no results/cog folder (not processed yet?)"
    skipped_country=$((skipped_country + 1))
    continue
  fi

  n=0
  for f in "$cogdir"/*.tif; do
    [ -e "$f" ] || continue
    base="$(basename "$f" .tif)"      # coastal_rcp45_2030_rp0100

    # hazard_scenario_year_rp -- exactly four parts, or we do not guess.
    IFS='_' read -r hazard scen year rp extra <<< "$base"
    if [ -z "${rp:-}" ] || [ -n "${extra:-}" ]; then
      echo "  [skip] unexpected filename: $base"
      skipped_file=$((skipped_file + 1))
      continue
    fi
    case "$hazard" in
      riverine|coastal) ;;
      *) echo "  [skip] unknown hazard '$hazard' in $base"
         skipped_file=$((skipped_file + 1)); continue ;;
    esac

    key_scen="${SCENARIO[$scen]:-}"
    if [ -z "$key_scen" ]; then
      echo "  [skip] unmapped scenario '$scen' in $base"
      skipped_file=$((skipped_file + 1))
      continue
    fi

    out="$DEST/$code/$hazard/$key_scen/$year/$rp/cog.tiff"
    if [ -e "$out" ]; then
      echo "  [COLLISION] $out already staged -- two sources map to one key"
      collisions=$((collisions + 1))
      continue
    fi

    if [ "$DRY_RUN" = 0 ]; then
      mkdir -p "$(dirname "$out")"
      # Hardlink by default: the staged tree is a read-only handoff on the same
      # filesystem, so a real copy would double ~40 GB for no benefit. Removing
      # either name later leaves the other intact. Falls back to a copy if the
      # link fails (different filesystem, or a filesystem without hardlinks).
      if [ "$LINK" = "1" ]; then
        cp -l "$f" "$out" 2>/dev/null || cp "$f" "$out"
      else
        cp "$f" "$out"
      fi
    fi
    n=$((n + 1))
    staged=$((staged + 1))
  done
  echo "[ok]  $country -> $code   ($n file(s))"
done

echo
echo "Staged $staged file(s)."
[ "$skipped_country" -gt 0 ] && echo "Skipped $skipped_country country folder(s)."
[ "$skipped_file" -gt 0 ] && echo "Skipped $skipped_file file(s) with unexpected names."
if [ "$collisions" -gt 0 ]; then
  echo "[ERROR] $collisions key collision(s) -- nothing should map to the same key." >&2
  exit 1
fi

if [ "$DRY_RUN" = 0 ]; then
  echo "Tree ready at: $DEST"
  echo "Preview:"
  find "$DEST" -maxdepth 4 -type d | sort | head -20
  echo
  echo "Upload with e.g.:  rclone copy $DEST r2:<bucket>/flood"
fi
