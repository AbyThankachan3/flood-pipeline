# Coastal flood: required code changes

**Status:** the coastal summary-analytics panel reports the wrong numbers in
production today. Riverine is correct and needs only one addition.

This document lists the changes repo by repo. It was written against
`develop` on each repo as of 2026-10-18.

---

## 1. The defect

`CoastalFloodService` reads bands 1–4 as `mean / stdDev / median / max`. Those
constants are correct for **riverine**, which collapses a 5-GCM ensemble into
statistics. Coastal has no ensemble — its bands are four **distinct scenarios**:

| Band | Coastal layer is | UI currently calls it |
|---|---|---|
| 1 | `depth_nosub_p05` | Mean depth |
| 2 | `depth_nosub_p50` | **Std. deviation** |
| 3 | `depth_wtsub_p05` | Median depth |
| 4 | `depth_wtsub_p50` | Max depth |

Every tile is mislabelled. The worst is **Std. deviation**, which shows a flood
depth in metres presented as a spread. On a Louisiana sample it reads `0.88 m`
beside a mean of `0.87 m` — σ ≈ mean is near-impossible, and that is the
visible tell.

`CoastalFloodService` is a copy of `RiverineFloodService` with band 5 removed
and the class renamed; the band constants were never revisited. Nothing throws,
because both files have at least four readable bands.

### How the band identities were established

Two independent lines of evidence.

**WRI's own data dictionary** (`Aqueduct_Floods_Data_Dictionary.xlsx`, sheet
`coastal`), filename structure
`floodtype_climatescenario_subsidence_year_returnperiod_projection`:

```
subsidence  | nosub     | Subsidence not included in projection
subsidence  | wtsub     | Subsidence included in projection
projection  | 0         | 95th percentile (default)
projection  | 0_perc_05 | 5th percentile
projection  | 0_perc_50 | 50th percentile
```

**The data itself.** Across 88,314 flooded pixels in five regions (Louisiana,
Florida, NY/NJ, Netherlands, Bangladesh), the strict ordering
`p05 <= p50 <= "0"` holds in **100.000%** of pixels, and `"0"` is never equal
to `p50`. That is only consistent with three distinct percentiles of one
distribution.

Note the trap: the 95th-percentile file has **no `perc` in its name** — it is
the bare `0` token, which reads like a null placeholder.

---

## 2. `com-hartreeresearch/hartree-business-contracts`

Change these first; they set the API shape the other repos consume.

### `bluebird-contracts/.../domain/CoastalFloodAttributes.java`

Current:

```java
public class CoastalFloodAttributes {
    private BigDecimal mean;
    private BigDecimal stdDev;   // nothing in coastal can honestly fill this
    private BigDecimal median;
    private BigDecimal max;
}
```

Replace with fields that match the agreed panel design: the three spatial
statistics the card shows, the sea-level-rise range, and the case they describe.

```java
public class CoastalFloodAttributes {
    // Which of the six bands these numbers came from
    private String subsidence;        // "nosub" | "wtsub"
    private String slrPercentile;     // "p05" | "p50" | "p95"

    // Spatial statistics over the selected band, within the radius
    private BigDecimal medianDepth;
    private BigDecimal meanDepth;
    private BigDecimal maxDepth;

    // Sea-level-rise range at the SAME subsidence setting.
    // All three are populated: six bands is the pipeline default and all
    // twelve countries were built with it.
    private BigDecimal depthP05;
    private BigDecimal depthP50;
    private BigDecimal depthP95;

    // Only needed if a four-band (legacy-parity) build is ever served, where
    // depthP95 is absent and the fourth tile shows this instead.
    private BigDecimal floodedFraction;   // 0..1, pixels with depth > 0
}
```

How those map onto the card (see `design/coastal-panel-proposed.html`):

| Card slot | Field |
|---|---|
| Pill, top right | `slrPercentile` — e.g. "50th pct" |
| Top left tile | `medianDepth` |
| Top right tile | `meanDepth` |
| Bottom left tile | `maxDepth` |
| Bottom right tile | `depthP05`–`depthP95` as a range |

The first three are deliberately the same three riverine shows. Only the
fourth slot differs, and in both cards it carries that hazard's own
uncertainty measure — standard deviation across models for riverine, the
sea-level-rise range for coastal.

`stdDev` is removed deliberately. Do not keep it "for compatibility" — a field
that cannot be computed correctly will be populated with something wrong.

> **Breaking change.** Bump the contracts version; `bluebird` pins it.

### `bluebird-contracts/.../domain/ReportCoastalFloodAttributesDto.java`

Current fields: `year`, `scenario`, `returnPeriod`, `coastalFloodAttributes`.

Add `subsidence` and `slrPercentile`, otherwise a report row cannot say which
of the six cases it describes.

---

## 3. `com-hartreeresearch/bluebird`

### 3.0 Band layout to code against

Six bands, pipeline default as of 2026-10-25:

```
1 depth_nosub_p05   2 depth_nosub_p50   3 depth_nosub_p95
4 depth_wtsub_p05   5 depth_wtsub_p50   6 depth_wtsub_p95
```

Subsidence is the outer loop, percentile the inner. Select by NAME, never by
position — a four-band legacy build has p95 absent and the indices shift.

### 3.1 `services/CoastalFloodService.java` — the fix

Delete the four band constants. Select the band by **name**, not position:

```java
public CoastalFloodAttributes getCoastalFloodAttributes(
        final String year, final String scenario, final String returnPeriod,
        final String subsidence,      // "nosub" | "wtsub"
        final String slrPercentile,   // "p05" | "p50" | "p95"
        final double latitude, final double longitude, final double radiusMetres) {

    final HazardLayer layer = hazardLayerResolver.resolve(
        "flood", "coastal", year, scenario, returnPeriod);

    final String wanted = "depth_" + subsidence + "_" + slrPercentile;
    final Integer band = layer.bandsByName().get(wanted);
    if (band == null) {
        throw new IllegalArgumentException(
            "No band " + wanted + " in " + layer.objectKey()
                + "; available: " + layer.bandsByName().keySet());
    }
    ...
}
```

Band-by-name is the point. It makes this class of bug impossible to reintroduce
and means adding the 95th percentile later needs no Java change.

**Until the resolver exists**, a band map keyed on subsidence+percentile is an
acceptable interim — but keep it in one place, not as four loose `static final
int` constants.

### 3.2 `controllers/RegionalHazardProjectionController.java`

The coastal endpoint (twin of `/api/v1/climate-resilience/riverine-flood-attributes`)
needs two more parameters:

```java
@RequestParam(defaultValue = "wtsub") String subsidence,
@RequestParam(defaultValue = "p50")   String slrPercentile,
```

Defaults chosen so an un-updated caller gets a defensible case (subsidence
included, median SLR) rather than the most optimistic one. Validate against the
allowed sets and return 400 on anything else.

### 3.3 `services/ClimateReportService.java`

`getCoastalFloodProjections()` (around line 317) loops year × scenario ×
returnPeriod. It must now also decide the subsidence/percentile case, and set
those fields on `ReportCoastalFloodAttributesDto`. Either fix a documented
default for reports, or iterate the cases — but the report must state which it
used.

### 3.4 `services/RegionalHazardProjectionService.java` — decouple the R2 layout

Today the object key is computed in Java, so every R2 reorganisation is a code
change and a release.

**The real R2 layout carries an ISO country code.** Wind's `stage_for_r2.sh`
copies per-country output into:

```
wind/<iso>/<ssp>/<year>/cog.tiff        e.g. wind/gb/ssp245/2030/cog.tiff
```

That is the corrected, intended convention. Note `.tiff`, and note the `<iso>`
segment: `EU → eu`, `UK → gb`, `USA → us`, and so on.

The path builders on `develop` predate it and are **stale**:

```java
private String buildFloodPath(type, year, scenario, returnPeriod) {
    return "flood/" + type + "/" + year + "/" + scenario + "/" + returnPeriod + "/cog.tif";
}
private String buildWindPath(year, scenario) {
    return "wind/" + scenario + "/" + year + "/cog.tif";   // no <iso>, .tif not .tiff
}
```

So `buildFloodPath` needs the same treatment wind's key already had: an `<iso>`
segment and the `.tiff` extension.

**Settled (2026-10-25).** The flood key is:

```
flood/<iso>/<hazard>/<scenario>/<year>/<rp>/cog.tiff

e.g. flood/gb/coastal/rcp45/2030/rp0100/cog.tiff
```

Country second, mirroring `wind/<iso>/<ssp>/<year>/cog.tiff`, so segment 2 is
the ISO code for every hazard and nothing generic has to special-case. The
return period keeps its `rp0100` form.

**Scenario is `rcp45`/`rcp85`** — unchanged from the pipeline's own filenames,
and unchanged from what flood has always used: the legacy
`innunriver_ensemble.py` wrote `inunriver_rcp4p5_aggregated_...` and
`innuncoast_download_data.py` had `SCENARIOS = ["rcp4p5", "rcp8p5"]`. The
rasters are CMIP5, so RCP is also the accurate name. Staging translates
nothing. (`USE_SSP=1` stages `ssp245`/`ssp585` if that is ever wanted.)

Wind differs legitimately: it is CMIP6 and keys on `ssp245`/`ssp585`. The two
are different scenario families that share a forcing level, so flood does not
inherit wind's token.

**This makes an alias necessary in the backend.** The frontend sends `ssp245`
for flood (the UI label), but the key says `rcp45`, so the resolver must map
one to the other when building the object key:

```yaml
aliases:
  scenario: { ssp245: rcp45, ssp585: rcp85 }
```

One explicit mapping in one place, rather than a silent rename at staging time
that would make the stored data claim to be CMIP6.

`stage_for_r2.sh` in the repo root produces that tree. It only **copies into a
local staging folder** — it does not upload; someone else syncs the tree to R2
unchanged.

This is also the strongest argument for the resolver below: with the layout in
config or a table, a correction like the one wind already went through is a
config edit, not a code change in two languages.

### Why the ISO segment matters for EU

Because country is in the key, `eu/` and `gb/` are separate objects, so the
fact that the NUTS 2021 level-0 boundary contains the UK (and Switzerland,
Norway, Türkiye, Iceland, Liechtenstein, Serbia, Albania, Montenegro, North
Macedonia — 37 features, not 27) is **not** a collision. It is redundant
storage: UK pixels appear in both layers with identical values, and an app that
resolves a site to an ISO code will only ever read `gb` for a British site.

Wind already shipped with this, using the same two shapefiles.

**Decided (2026-10-25): leave as-is.** The layer stays labelled `EU` and keeps
all 37 NUTS features. Filtering to the EU-27 was considered and rejected — of
the ten non-member features, only the UK has its own layer, so filtering would
delete coverage for Switzerland, Norway, Iceland, Liechtenstein, Türkiye,
Serbia, Albania, Montenegro and North Macedonia, which nothing else in the set
covers.

Two consequences to be aware of rather than fix:

- A site in the UK is inside two layers. The site → layer mapping must be an
  explicit table preferring the national layer (`GB → gb`), never
  point-in-polygon against layer boundaries, which is ambiguous there.
- Anything that sums *across* layers rather than per site would double-count
  UK exposure. Site-wise aggregation is unaffected.

Introduce a seam:

```java
public interface HazardLayerResolver {
    HazardLayer resolve(String hazard, String type, String year,
                        String scenario, String returnPeriod);
}

public record HazardLayer(String objectKey, Map<String, Integer> bandsByName) { }
```

Then pick an implementation:

**A — config templates (recommended first step).** `application.yml`:

```yaml
hazard:
  layers:
    templates:
      flood: "flood/{type}/{year}/{scenario}/{returnPeriod}/cog.tif"
      wind:  "wind/{scenario}/{year}/cog.tif"
    bands:
      coastal: [depth_nosub_p05, depth_nosub_p50, depth_wtsub_p05, depth_wtsub_p50]
      riverine: [mean_flood_depth, flood_depth_stddev, median_flood_depth,
                 peak_flood_depth, model_count]
    aliases:
      scenario: { ssp245: rcp4p5, ssp585: rcp8p5 }
```

Bind with `@ConfigurationProperties("hazard.layers")`. Path change becomes a
config change — no recompile. `@RefreshScope` removes the restart. The
`aliases` block also absorbs the SSP/RCP naming mismatch (see §6).

**B — manifest object in R2.** The uploader writes a manifest; bluebird reads
and caches it. No backend involvement at all when paths move. Needs a
cache-invalidation story.

**C — Postgres registry.** A `hazard_layer` table under the existing
`climate_resilience` schema (Flyway is already wired). Path change = `UPDATE`,
transactional, supports an `active` flag for gradual cutover. Most robust, most
work.

Recommended: **A now, C later.** The interface means swapping is one new bean.

### 3.5 `services/GDALRasterService.java` — verify, don't assume

No change required, but two things to know:

- `sampleCircularWindow(url, bandCount, lat, lon, radius)` skips a pixel when
  **band 1** is NoData, for all bands. Harmless for coastal today (all bands
  share one valid mask — verified: identical valid-pixel counts across all
  four), but it is an assumption worth a comment.
- Add a startup or first-read assertion that the file's band descriptions match
  the expected names. Production COGs written by the legacy scripts have **no
  band descriptions at all**, which is why nothing caught this. COGs from the
  current pipeline do set them (`depth_nosub_p05` etc. plus `units=m`), so the
  check becomes possible once those are deployed.

### 3.6 Tests

`src/test/java/.../HazardStatsIT.java` and `ClimateReportControllerIT.java`
reference coastal. Add a case asserting that a known pixel returns the band
matching the requested subsidence/percentile — not merely that four numbers
came back.

---

## 4. `org-puffincreek/core-climate-risk` — no change

Checked. Coastal appears only as a risk **category**:
`RiskScores.coastalFlooding` is a `HazardRiskAttributes`, and
`current_hazard.yml` carries `coastal-flooding:` scoring thresholds. Nothing
there touches flood-depth bands or the mean/median/max fields. The defect does
not reach this repo.

---

## 5. Frontend

Repo not available to me; changes are specified by behaviour. Reference design:
the "Coastal Flood Panel Redesign" canvas (three artboards — current, proposed
coastal, proposed riverine).

### 5.1 Two new controls on the Coastal Flood panel only

Insert between **Return period** and **Summary analytics**, above a hairline
divider so they read as coastal-specific. Same segmented-button pattern,
uppercase label, `What is this?` link right-aligned.

| Control | Options | Maps to |
|---|---|---|
| `LAND SUBSIDENCE` | Excluded / **Included** | `nosub` / `wtsub` |
| `SEA LEVEL RISE` | 5th / **50th** / 95th | `p05` / `p50` / `p95` |

Bold = default. Each needs one line of helper text; neither term is
self-explanatory to a non-specialist. Show the 95th option only when the
six-band layers are deployed, or disable it with a tooltip.

### 5.2 Keep the card structure, change one tile

The coastal card keeps the **same shape as the riverine card already in
production** — header, pill top-right, 2×2 tile grid. Only the contents change.

| Slot | Riverine (as built) | Coastal |
|---|---|---|
| Pill | `5 models` | `50th pct` — the selected percentile |
| Top left | Median depth | Median depth |
| Top right | Mean depth | Mean depth |
| Bottom left | Max depth | Max depth |
| Bottom right | Std. deviation | **Sea level rise range** |

Three tiles are identical. Only the bottom-right slot differs, and in both
cards that slot holds the hazard's own uncertainty measure: riverine has
disagreement across models, coastal has the 5th–95th sea-level-rise range
(`depthP05`–`depthP95`). Same position, same role, each correct for its data.

**There must be no standard-deviation tile on the coastal panel.** Coastal has
no model ensemble, so there is nothing for a standard deviation to describe.

Coastal's four values are all **spatial** statistics over the selected band
within the radius. Riverine's are a mix of spatial and across-model. They are
not comparable, so do not add a header implying they are.

> **Six bands is now the pipeline default**, so `depthP95` is populated and the
> range tile works. A four-band build is still reachable with
> `FLOOD_COASTAL_PERCENTILES=0_perc_05,0_perc_50` for legacy parity — in that
> case there is no 95th percentile and the slot must show **Flooded area %**
> instead, because 5th–50th is the lower half of a distribution and would
> understate rather than describe.

**No footer row.** An earlier draft repeated the selected case under the grid;
it is already stated by the pill and by the two selectors directly above, so it
was removed. Riverine's footer earns its place because `5 of 5 models` is data
completeness at that pixel, read from `model_count` — coastal has no equivalent.

A rendered mockup is at [`design/coastal-panel-proposed.html`](design/coastal-panel-proposed.html)
(open it in a browser; no build step).

### 5.2.1 Still open: the flood-defences caveat

Aqueduct simulates its hazard layers **with no flood protection at all** —
defences are handled separately in WRI's risk calculation. So for New Orleans
these depths are what happens with the post-Katrina levee system absent. The
same is true of riverine. Nothing in the UI currently says so.

It is a property of the data source, not of either card, so it belongs once at
panel level rather than inside both. Candidates, in preference order:

1. A muted line below the card, shared by both hazards:
   `(i) Undefended — flood defences are not modelled in these layers`
2. The existing `What is this?` link beside SUMMARY ANALYTICS, for the longer
   explanation.

Decision pending.

### 5.3 Riverine: surface model agreement

`RiverineFloodService` already populates `numModels`; the UI drops it. Add a
**Model agreement** row (`5 of 5` plus five bars) and label the existing stat
group **"Across 5 climate models"**.

### 5.4 Do not make the two panels look identical

Riverine stats vary across **climate models**; coastal stats vary across
**space** within the radius. A riverine σ of 0.42 m and a coastal spread of
0.03 m are not comparable. The group headers are what prevent that misreading —
they are load-bearing, not decoration.

---

## 6. Open decision: scenario naming

The UI says `SSP2-4.5` / `SSP5-8.5`. Aqueduct Floods v2 is **CMIP5**, so the
data is `rcp4p5` / `rcp8p5`. Same radiative forcing (4.5 and 8.5 W/m²), different
scenario families. The wind layers genuinely are SSP (CMIP6); flood is not.

**Decided (2026-10-26): storage keeps RCP**, matching both the data and every
previous flood pipeline. The UI label is a separate question and is unchanged
for now; §3.4's `aliases` map bridges the two. If the panel is ever relabelled
to RCP, the alias can go.

---

## 7. Rollout order

1. **contracts** — new fields, publish the version
2. **pipeline** — run all 12 countries (USA included) for both hazards. Coastal
   is six bands by default now, and the COGs carry band descriptions; upload to
   the existing keys
3. **bluebird** — resolver, `CoastalFloodService`, controller params, report
   service; enable the band-description assertion
4. **frontend** — selectors and card

Step 2 before step 3: a service that knows about bands 5–6 reading an old
4-band file will fail. Either ship the files first, or have the resolver read
the actual band count and adapt.

---

## 8. Caveats on this document

- Read from a shallow clone of `develop` on each repo; callers on other
  branches may exist.
- `bluebird`'s checkout hit Windows `MAX_PATH` limits, so files were read from
  git objects rather than a working tree. No file was missed in the listings,
  but nothing was compiled or run.
- The frontend repo was not available; §5 is behavioural, not file-level.
- `CloudflareHmacBackedClient` was not read; §3.4 assumes only that
  `getHmacSignedDownloadURL(domain, objectKey)` takes an object key.
