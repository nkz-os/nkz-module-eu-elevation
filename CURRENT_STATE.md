# nkz-module-eu-elevation — Estado del Módulo

> Última actualización: 2026-03-25

## Estado: ✅ Código listo — Pendiente despliegue

El módulo ha sido auditado y corregido en profundidad. Todos los problemas de seguridad, identidad y arquitectura han sido resueltos. Falta build del frontend, rebuild de imagen Docker y despliegue a K8s.

---

## Arquitectura

| Componente | Tecnología | Estado |
|------------|-----------|--------|
| **Backend API** | FastAPI + Uvicorn | ✅ Corregido |
| **Worker ETL** | Celery + GDAL + pydelatin + quantized-mesh-encoder | ✅ Reescrito SOTA |
| **Terrain CDN** | NGINX (serve static .terrain tiles) | ✅ Sin cambios |
| **Frontend IIFE** | React 18 + Vite + `@nekazari/module-builder` | ✅ Corregido |
| **Storage** | MinIO vía S3 API (boto3/minio) | ✅ Integrado |

## Funcionalidades

### Terreno 3D (Quantized Mesh)
- **Catálogo pre-configurado de 20 fuentes DEM** (EU + UK) con auto-resolución por `country_code`
- Ingesta con solo `POST /api/elevation/ingest {"country_code": "ES"}` — BBOX y WCS endpoint se resuelven automáticamente
- Pipeline ETL SOTA: tiling geográfico Cesium TMS (zoom 8–14 configurable)
- Decimación de geometría con `pydelatin`, codificación `quantized-mesh-encoder`
- Upload automático a MinIO vía S3 API
- Generación de `layer.json` con rangos `available` reales
- Gestión multi-tenant de terrain sources (selector Auto/Manual/Off)
- WebSocket para tracking de progreso en tiempo real
- **Fallback automático**: si la fuente nacional falla, el pipeline retries con Copernicus GLO-30 (30m) y avisa al user vía WebSocket + campo `warning` en resultado
- API: `GET /sources` lista todas las fuentes, `GET /sources/{country_code}` detalle

### Fuentes DEM Pre-configuradas (`dem_sources.py`)

| Código | País | Endpoint | Tipo | Resolución |
|--------|------|----------|------|------------|
| ES | España | `servicios.idee.es/wcs-inspire/mdt` | WCS | 5m |
| PT | Portugal | `servicos.dgterritorio.pt/wcs/mdr` | WCS | 2m |
| FR | France | `data.geopf.fr/wms-r/wms` | WMS | 1m |
| BE | Belgium | `wcs.ngi.be/elevation/wcs` | WCS | 1m |
| NL | Netherlands | `service.pdok.nl/.../wcs/v1_0` | WCS | 0.5m |
| LU | Luxembourg | `download.data.public.lu/...` | DOWNLOAD | 1m |
| IE | Ireland | `inspireservices.geohive.ie/...` | REST | 10m |
| DE | Germany | `sgx.geodatenzentrum.de/wcs_dgm200` | WCS | 200m |
| AT | Austria | `gis.bev.gv.at/geoserver/wcs` | WCS | 10m |
| CH | Switzerland | `wms.geo.admin.ch/` | WMS | 2m |
| CZ | Czech Republic | `ags.cuzk.cz/.../WCSServer` | WCS | 1m |
| GB | United Kingdom | `environment.data.gov.uk/.../wcs` | WCS | 1m |
| DK | Denmark | `services.datafordeler.dk/.../wcs` | WCS | 0.4m |
| SE | Sweden | `minkarta.lantmateriet.se/` | DOWNLOAD | 1m |
| FI | Finland | `avoin-karttakuva.maanmittauslaitos.fi/.../wcs/v2` | WCS | 2m |
| NO | Norway | `wms.geonorge.no/.../wcs.hoyde-dtm-nhm-25832` | WCS | 1m |
| IT | Italy | `tinitaly.pi.ingv.it/TINItaly_1_1/wcs` | WCS | 10m |
| SI | Slovenia | `gis.arso.gov.si/geoserver/dmv/wcs` | WCS | 1m |
| PL | Poland | `mapy.geoportal.gov.pl/.../WCS/DigitalTerrainModel` | WCS | 1m |
| EU | Pan-European | Copernicus GLO-30 | DOWNLOAD | 30m |

### CORINE Land Cover (CLC) 2018 — NUEVO
- Capa WMS de EEA Copernicus (`discomap.eea.europa.eu`)
- Toggle on/off en panel de capas (Unified Viewer)
- Sin backend — consumo directo desde CesiumJS (`WebMapServiceImageryProvider`)
- Cobertura: EU + UK, alpha 60%
- Persistencia de preferencia en localStorage

## Slots Registrados

| Slot | Componente | Función |
|------|-----------|---------|
| `map-layer` | `ElevationLayer` | Inyecta terrain provider + CLC imagery en Cesium |
| `layer-toggle` | `ElevationAdminControl` | Toggle de CLC en panel de capas |
| `dashboard-widget` | `ElevationAdminControl` | Panel admin con selector de terreno |
| `context-panel` | `ElevationAdminControl` | Control contextual en Unified Viewer |

## Correcciones aplicadas (2026-03-25)

### Seguridad
- CORS: whitelist explícita vía env `ALLOWED_ORIGINS` (antes: wildcard `*`)
- Auth: validación estricta de issuer JWT (antes: bypass si mismatch)
- Cero credenciales hardcoded en todo el repo (antes: `minioadmin` en 2 archivos)
- Ingress: eliminada regla conflictiva con `nekazari.robotika.cloud`

### Limpieza
- Eliminados 6 archivos LiDAR residuales (~44K de código muerto)
- Eliminados 2 archivos legacy frontend (`api.ts`, `useUIKit.tsx`)
- Cero referencias a "lidar" en código Python

### Configuración
- `vite.config.ts` moduleId: `'my-module'` → `'nkz-module-eu-elevation'`
- `registration.sql`: reescrito con identidad correcta (antes: registraba `lidar`)
- `requirements.txt`: añadidos `SQLAlchemy`, `minio`
- `package.json`: añadido script `build:module`

### ETL Pipeline
- Reescritura completa de `elevation_tasks.py`
- Antes: generaba 1 solo tile hardcoded (`z=12, x=2048, y=2048`), escribía a filesystem
- Ahora: tiling TMS completo, iteración por zoom, upload a MinIO, `layer.json` real

## Pasos pendientes para despliegue

1. **Build frontend**: `pnpm install && pnpm run build:module`
2. **Upload IIFE a MinIO**: `python upload_module.py` (requiere env vars MinIO)
3. **Build Docker image**: `docker build -t ghcr.io/nkz-os/nkz-module-eu-elevation/backend:latest backend/`
4. **Push image**: `docker push ghcr.io/nkz-os/nkz-module-eu-elevation/backend:latest`
5. **Crear secret MinIO**: `kubectl create secret generic minio-secret -n nekazari --from-literal=MINIO_ACCESS_KEY=xxx --from-literal=MINIO_SECRET_KEY=xxx`
6. **Apply manifests**: `kubectl apply -f k8s/`
7. **Registrar módulo**: ejecutar `k8s/registration.sql` en PostgreSQL

## Env vars requeridas (k8s)

| Variable | Valor | Fuente |
|----------|-------|--------|
| `CELERY_BROKER_URL` | `redis://redis-service:6379/10` | deployment |
| `CELERY_RESULT_BACKEND` | `redis://redis-service:6379/11` | deployment |
| `ALLOWED_ORIGINS` | `https://nekazari.robotika.cloud` | deployment |
| `JWT_ISSUER` | `https://auth.robotika.cloud/auth/realms/nekazari` | deployment |
| `JWKS_URL` | `https://auth.robotika.cloud/auth/realms/nekazari/protocol/openid-connect/certs` | deployment |
| `MINIO_ENDPOINT` | `minio-service:9000` | deployment |
| `MINIO_BUCKET` | `terrain-tilesets` | deployment |
| `MINIO_ACCESS_KEY` | (secret) | `minio-secret` |
| `MINIO_SECRET_KEY` | (secret) | `minio-secret` |
| `DATABASE_URL` | `postgresql://nekazari:nekazari@postgresql:5432/nekazari` | deployment |
