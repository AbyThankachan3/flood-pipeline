"""
Shared raster machinery for the per-country flood steps.

Boundary handling follows the original FloodRiverine/FloodCoastal scripts
exactly: the country mask is rasterized over the FULL global grid and the
output keeps the full global extent.

That is deliberate, and it is not negotiable for correctness reasons:

  * No bounding box is computed anywhere. A bounding box cannot wrap the
    antimeridian, so for any country with territory on both sides of the
    dateline (the USA, via Alaska's Aleutians) it either degenerates to ~360
    degrees wide or, if "fixed" with a clip box, silently drops real territory.
    Dropping territory from a national boundary is not a tradeoff worth making.
  * Rasterizing over the true grid places every polygon exactly where it
    belongs on both sides of the dateline, with no special cases.

The cost is that every input raster is read in full. Memory is still bounded:
the read/compute/write loop is tiled, so only (n_inputs x tile x tile) floats
are held at once. The one large allocation is the mask itself (one bool per
global pixel), which is cached per process because it is identical for every
group on the same grid.
"""

import math

import numpy as np
import rasterio
from rasterio import features
from rasterio.windows import Window
import geopandas as gpd


# One rasterized mask per (grid, boundary) per process. Rasterizing a national
# boundary over the global grid is expensive and the result is identical for
# every scenario/year/return-period group, so it is built once and reused.
_MASK_CACHE = {}


def load_boundary(shapefile, crs):
    """
    Read the boundary shapefile and reproject it to the raster CRS.

    The full shapefile is used as-is. Nothing is clipped, filtered or dropped.
    """
    gdf = gpd.read_file(shapefile)
    if gdf.empty:
        raise ValueError(f"Boundary shapefile has no features: {shapefile}")
    return gdf.to_crs(crs)


def build_mask(src, gdf):
    """
    Boolean mask over the FULL raster grid: True inside the boundary.

    Cached per process — see _MASK_CACHE.
    """
    # Keyed by grid, and validated against the boundary object itself. The
    # cache holds a strong reference to that object, so `is` cannot be fooled
    # by an address being recycled after a garbage collection.
    key = (src.width, src.height, tuple(src.transform)[:6], str(src.crs))
    cached_gdf, cached_mask = _MASK_CACHE.get(key, (None, None))
    if cached_mask is not None and cached_gdf is gdf:
        return cached_mask

    shapes = [(geom, 1) for geom in gdf.geometry
              if geom is not None and not geom.is_empty]
    if not shapes:
        raise ValueError("Boundary shapefile contains no usable geometry.")

    mask = features.rasterize(
        shapes=shapes,
        out_shape=(src.height, src.width),
        transform=src.transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    ).astype(bool)

    _MASK_CACHE.clear()          # only ever keep one; it is large
    _MASK_CACHE[key] = (gdf, mask)
    return mask


def data_extent(mask):
    """
    The outermost rows and columns of `mask` that contain any True.

    Returns (row_start, row_stop, col_start, col_stop) as a half-open range, or
    None if the mask is empty.

    This is a MEASUREMENT of the rasterized mask, not a prediction from the
    shapefile's bounding box -- that distinction is the whole point. A row or
    column is only ever discarded when every pixel in it is already NoData, so
    territory cannot be lost, including territory that sits on both sides of the
    antimeridian: if the first and last columns both hold data (as they do for
    the USA, via Alaska's Aleutians) there is simply nothing to trim
    horizontally and the full width is kept. No country special-cases.
    """
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def trimmed_window(mask, width, height):
    """
    A Window covering every masked pixel, with the blank border removed.

    Returns (window, report) where report is a short line for the log, or
    (None, reason) when trimming is not safe or not worth doing -- in which
    case the caller must fall back to the full extent.
    """
    extent = data_extent(mask)
    if extent is None:
        return None, "mask is empty"

    r0, r1, c0, c1 = extent

    # The guarantee, checked rather than assumed: every masked pixel must
    # survive the trim. True by construction, verified anyway so a bug in the
    # scanning above cannot silently drop territory.
    total = int(mask.sum())
    kept = int(mask[r0:r1, c0:c1].sum())
    if kept != total:
        return None, (f"SAFETY CHECK FAILED: trim would keep {kept:,} of "
                      f"{total:,} masked pixels")

    if (r1 - r0, c1 - c0) == (height, width):
        return None, "nothing to trim (data reaches every edge)"

    window = Window(c0, r0, c1 - c0, r1 - r0)
    factor = (width * height) / max(1, (c1 - c0) * (r1 - r0))
    report = (f"trimmed {width}x{height} -> {c1 - c0}x{r1 - r0} "
              f"({factor:.1f}x fewer pixels, all {total:,} masked pixels kept)")
    return window, report


def block_size(extent, requested):
    """
    A legal internal block dimension for a GeoTIFF.

    Tiled TIFF block dimensions must be multiples of 16, so the requested tile
    size cannot simply be clamped to the raster: a small or awkwardly sized
    grid would otherwise be rejected outright by the TIFF writer.
    """
    return max(16, (min(requested, extent) // 16) * 16)


def check_alignment(datasets):
    """All inputs must share one grid; mismatches would silently misalign bands."""
    ref = datasets[0]
    for ds in datasets[1:]:
        if (ds.width, ds.height) != (ref.width, ref.height):
            raise ValueError(
                f"Raster grid mismatch: {ds.name} is {ds.width}x{ds.height}, "
                f"expected {ref.width}x{ref.height}"
            )
        if ds.crs != ref.crs:
            raise ValueError(f"CRS mismatch: {ds.name} is {ds.crs}, expected {ref.crs}")
        if not ds.transform.almost_equals(ref.transform):
            raise ValueError(f"Transform mismatch: {ds.name}")


def process_group(src_paths, out_path, gdf, compute_fn, n_out_bands,
                  band_descriptions, band_units, dataset_tags,
                  tile_size=1024, crs=None, trim=False, log=None):
    """
    Clip a group of aligned rasters to the country and write one multi-band
    GeoTIFF.

    compute_fn(stack) receives a float32 array of shape (n_inputs, h, w) with
    NaN outside the country / outside each raster's own valid data, and returns
    (n_out_bands, h, w).

    With trim=False (the default) the output keeps the full source extent,
    matching the original FloodRiverine/FloodCoastal scripts exactly.

    With trim=True the entirely-blank border is removed -- see trimmed_window().
    Anything that cannot be proven lossless falls back to the full extent, so
    the worst case of enabling it is that it does nothing.

    Returns the output path, or None if the boundary covers no pixel of the
    raster at all.
    """
    def say(msg):
        if log:
            log(msg)

    datasets = [rasterio.open(p) for p in src_paths]
    try:
        check_alignment(datasets)
        ref = datasets[0]

        mask = build_mask(ref, gdf)
        if not mask.any():
            return None

        # ---- Decide the output extent ------------------------------------
        win = None
        if trim:
            win, report = trimmed_window(mask, ref.width, ref.height)
            if win is None:
                say(f"full extent kept — {report}")
            else:
                say(report)

        if win is None:
            win = Window(0, 0, ref.width, ref.height)

        col_off, row_off = int(win.col_off), int(win.row_off)
        width, height = int(win.width), int(win.height)

        profile = ref.profile.copy()
        profile.update(
            driver="GTiff",
            width=width,
            height=height,
            transform=ref.window_transform(win),
            count=n_out_bands,
            dtype="float32",
            nodata=np.float32(np.nan),
            compress="LZW",
            predictor=3,
            BIGTIFF="IF_SAFER",
        )
        if width >= 16 and height >= 16:
            profile.update(
                tiled=True,
                blockxsize=block_size(width, tile_size),
                blockysize=block_size(height, tile_size),
            )
        else:
            profile.update(tiled=False)
            profile.pop("blockxsize", None)
            profile.pop("blockysize", None)

        if crs:
            profile["crs"] = crs

        tiles_x = math.ceil(width / tile_size)
        tiles_y = math.ceil(height / tile_size)

        with rasterio.open(out_path, "w", **profile) as dst:
            for ty in range(tiles_y):
                for tx in range(tiles_x):
                    xoff = tx * tile_size
                    yoff = ty * tile_size
                    w = min(tile_size, width - xoff)
                    h = min(tile_size, height - yoff)

                    # Two coordinate systems here: reads are offset into the
                    # SOURCE grid, writes are relative to the OUTPUT origin.
                    # They only coincide when nothing was trimmed.
                    read_window = Window(col_off + xoff, row_off + yoff, w, h)
                    write_window = Window(xoff, yoff, w, h)
                    tile_mask = mask[row_off + yoff:row_off + yoff + h,
                                     col_off + xoff:col_off + xoff + w]

                    stack = np.full((len(datasets), h, w), np.nan, dtype=np.float32)
                    for i, ds in enumerate(datasets):
                        arr = ds.read(1, window=read_window, masked=True)
                        data = arr.data.astype(np.float32)
                        data[np.ma.getmaskarray(arr) | (~tile_mask)] = np.nan
                        stack[i] = data

                    out = compute_fn(stack)

                    for b in range(n_out_bands):
                        dst.write(out[b].astype(np.float32), b + 1,
                                  window=write_window)

            for i, (desc, units) in enumerate(zip(band_descriptions, band_units),
                                              start=1):
                dst.set_band_description(i, desc)
                dst.update_tags(i, units=units)
            dst.update_tags(**dataset_tags)

        return out_path
    finally:
        for ds in datasets:
            ds.close()
