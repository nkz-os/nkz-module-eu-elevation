# Nekazari EU Elevation Module (`nkz-module-eu-elevation`)

Premium micro-module for the Nekazari Platform ecosystem, delivering multi-tier 3D terrain intelligence across the **European Union and the United Kingdom**.

## Architecture — Multi-Tier Terrain Providers

| Tier | Provider | Resolution | Setup |
|------|----------|-----------|-------|
| **Tier 0** | Cesium World Terrain | ~30m global | Zero (free Ion account) |
| **Tier 0** | MapTiler Terrain | Up to 50cm EU/UK | Free API key (100k tiles/month) |
| **Tier 1** | Custom DEM Source | 0.4m–30m | WCS/WMS URL + optional auth |
| **Tier 2** | Ingested Layers | Variable | Pipeline ETL → MinIO |
| **Tier 3** | Self-hosted | Any | Custom terrain URL |

### How It Works

1. **User selects provider** from the 3D Terrain panel (Cesium World, MapTiler, or custom)
2. **Factory pattern** (`terrainFactory.ts`) creates the appropriate `CesiumTerrainProvider`
3. **For custom sources**: user registers a WCS/WMS endpoint → pipeline processes it via GDAL + pydelatin → quantized mesh tiles uploaded to MinIO → terrain appears on map
4. **Auto mode**: camera position is matched against layer BBOXes to auto-select terrain

### Backend

- **FastAPI** + Uvicorn — REST API + WebSockets
- **Celery** + GDAL + pydelatin + quantized-mesh-encoder — ETL pipeline
- **PostgreSQL** — tenant preferences, custom sources, ingested layers
- **Redis** — task queue + job results
- **MinIO** — terrain tile storage (S3 API)

### Frontend

- **React 18** + TypeScript — IIFE bundle via `@nekazari/module-builder`
- **Terrain Factory** — abstracts provider creation (Cesium/MapTiler/Custom)
- **Slots**: `map-layer` (terrain injection), `layer-toggle` (admin control), `dashboard-widget`, `context-panel`

## Quick Start

### Development

```bash
pnpm install
pnpm dev          # Frontend dev server (port 5003)
```

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Build & Deploy

```bash
pnpm run build:module          # → dist/nkz-module.js
python upload_module.py        # → MinIO
docker build -t ghcr.io/nkz-os/nkz-module-eu-elevation/backend:latest backend/
docker push ghcr.io/nkz-os/nkz-module-eu-elevation/backend:latest
kubectl apply -f k8s/backend-deployment.yaml
```

## License

AGPL-3.0-or-later. See `LICENSE`.
