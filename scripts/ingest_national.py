#!/usr/bin/env python3
"""
Batch ingestion script: submit all national WCS DEM sources for terrain tile generation.

Iterates over the built-in DEM_SOURCES catalog (non-fallback entries only) and
submits Celery tasks to the elevation worker. Each country's terrain tiles are
stored under terrain/{country_code}/ in MinIO.

The worker first tries to download from the national WCS endpoint. If that
fails, it falls back to Copernicus GLO-30 (30m).

Usage:
  # From within the elevation-worker container:
  python3 /app/scripts/ingest_national.py

  # Dry-run (just list sources without submitting):
  python3 /app/scripts/ingest_national.py --dry-run

  # Specific countries only:
  python3 /app/scripts/ingest_national.py --countries ES,FR,DE

  # Custom zoom range:
  python3 /app/scripts/ingest_national.py --min-zoom 7 --max-zoom 13
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.dem_sources import DEM_SOURCES, DEMSource
from app.tasks.elevation_tasks import process_dem_to_quantized_mesh


def resolve_resolution_m(dem: DEMSource) -> float:
    """Parse resolution string (e.g., '500m', '1m', '0.5m') to float meters."""
    try:
        return float(dem.resolution.replace("m", "").strip())
    except (ValueError, AttributeError):
        return 25.0


def submit_country(dem: DEMSource, zoom_range: tuple[int, int], max_error: float) -> str | None:
    """Submit a Celery task for one country. Returns task ID or None if skipped.

    National WCS sources get WCS download params passed to the worker.
    Non-WCS sources fall through to Copernicus immediately.
    """
    bbox = dem.bbox
    res_m = resolve_resolution_m(dem)

    task_kwargs = {
        "country_code": dem.country_code,
        "source_urls": [dem.service_url],
        "bbox": bbox,
        "zoom_min": zoom_range[0],
        "zoom_max": zoom_range[1],
        "max_error": max_error,
    }

    if dem.service_type == "WCS" and not dem.fallback:
        task_kwargs.update({
            "_wcs_service_url": dem.service_url,
            "_wcs_layer_name": dem.layer_name or "",
            "_wcs_resolution_m": res_m,
        })
        method = "WCS"
    else:
        method = f"fallback ({dem.service_type})"

    task = process_dem_to_quantized_mesh.delay(**task_kwargs)
    print(f"  [{dem.country_code}] {dem.country_name:30s} "
          f"res={dem.resolution:>6s}  method={method:20s}  "
          f"task={task.id}", flush=True)
    return task.id


def main():
    parser = argparse.ArgumentParser(
        description="Batch-submit national DEM sources for terrain tile ingestion"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="List sources without submitting tasks")
    parser.add_argument("--countries", type=str, default=None,
                        help="Comma-separated country codes (default: all non-fallback)")
    parser.add_argument("--min-zoom", type=int, default=8,
                        help="Minimum zoom level (default: 8)")
    parser.add_argument("--max-zoom", type=int, default=14,
                        help="Maximum zoom level (default: 14)")
    parser.add_argument("--max-error", type=float, default=0.5,
                        help="pydelatin max error (default: 0.5)")
    args = parser.parse_args()

    # Select sources
    if args.countries:
        codes = {c.strip().upper() for c in args.countries.split(",")}
        sources = [s for s in DEM_SOURCES if s.country_code in codes and not s.fallback]
    else:
        sources = [s for s in DEM_SOURCES if not s.fallback]

    print(f"National DEM Terrain Ingestion")
    print(f"  Sources: {len(sources)} countries")
    print(f"  Zoom range: {args.min_zoom}–{args.max_zoom}")
    print(f"  Max error: {args.max_error}")
    print()

    if args.dry_run:
        print("Sources that would be ingested:")
        for s in sources:
            print(f"  [{s.country_code}] {s.country_name:30s}  {s.resolution:>6s}  "
                  f"{s.service_type}  notes={s.notes[:60] if s.notes else '-'}")
        return

    confirm = input(f"Submit ingestion for {len(sources)} countries? [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return

    task_ids = []
    for dem in sources:
        try:
            tid = submit_country(dem, (args.min_zoom, args.max_zoom), args.max_error)
            if tid:
                task_ids.append((dem.country_code, tid))
        except Exception as e:
            print(f"  [{dem.country_code}] ERROR: {e}", file=sys.stderr)
        time.sleep(0.3)  # Brief pause to avoid overwhelming Redis

    print(f"\nSubmitted {len(task_ids)} tasks.")
    print("Monitor progress via:")
    for code, tid in task_ids:
        print(f"  [{code}] GET /api/elevation/status/{tid}")
    print(f"Once complete, terrain will be available at:")
    for code, _ in task_ids:
        print(f"  /api/elevation/terrain/{code}/layer.json")


if __name__ == "__main__":
    main()
