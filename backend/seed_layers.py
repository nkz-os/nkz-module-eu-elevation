"""
Seed all EU/UK DEM sources as terrain layers in the database.

This pre-populates the elevation_layers table so the module ships with
all known European + UK data sources ready for ingestion.

Usage:
    python -m seed_layers
"""
from app.db.database import SessionLocal
from app.models.elevation_models import ElevationLayer
from app.dem_sources import DEM_SOURCES


def seed():
    """Register all DEM sources as terrain layers."""
    db = SessionLocal()
    created = 0
    updated = 0

    try:
        for src in DEM_SOURCES:
            existing = db.query(ElevationLayer).filter(
                ElevationLayer.id == src.country_code.lower()
            ).first()

            layer_data = {
                "id": src.country_code.lower(),
                "name": f"{src.country_name} ({src.resolution})",
                "url": f"/terrain/{src.country_code.lower()}",
                "bbox_minx": src.bbox[0],
                "bbox_miny": src.bbox[1],
                "bbox_maxx": src.bbox[2],
                "bbox_maxy": src.bbox[3],
                "is_active": True
            }

            if existing:
                for key, value in layer_data.items():
                    if key != "id":
                        setattr(existing, key, value)
                updated += 1
            else:
                db.add(ElevationLayer(**layer_data))
                created += 1

        db.commit()
        print(f"Seeded EU/UK terrain layers: {created} created, {updated} updated, {len(DEM_SOURCES)} total sources.")

    except Exception as e:
        db.rollback()
        print(f"Error seeding layers: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
