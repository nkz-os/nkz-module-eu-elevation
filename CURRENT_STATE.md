# nkz-module-eu-elevation — Estado del Módulo

> Última actualización: 2026-04-06

## Estado: ✅ Código SOTA completo — Pendiente build + deploy

Implementación completa del sistema multi-tier de terrain providers con Factory pattern, BYOK support, y custom DEM sources.

---

## Arquitectura SOTA — Multi-Tier Terrain Providers

| Tier | Provider | Resolución | Setup | Cobertura |
|------|----------|-----------|-------|-----------|
| **Tier 0** | Cesium World Terrain | ~30m global | Cero (Ion free) | Global |
| **Tier 0** | MapTiler Terrain | Hasta 50cm | API key (free 100k/mes) | EU + UK |
| **Tier 1** | Custom DEM Source | Variable (0.4m-30m) | URL WCS/WMS + auth opcional | Cualquier país |
| **Tier 2** | Ingested Layers | Variable | Pipeline ETL → MinIO | BBOX definido |
| **Tier 3** | Self-hosted | Cualquiera | Custom terrain URL | Infra del user |

### Patrón Factory

```
Frontend: terrainFactory.ts
  ├── cesium_world → Cesium.createWorldTerrain()
  ├── maptiler → CesiumTerrainProvider(api.maptiler.com/...)
  ├── custom → CesiumTerrainProvider(user URL)
  └── off → EllipsoidTerrainProvider()
```

### Flujo del User

1. **Sin configuración** → mapa flat (ellipsoid)
2. **Un clic** → Cesium World Terrain (global, ~30m)
3. **Con API key** → MapTiler (EU/UK, hasta 50cm)
4. **Fuente custom** → Añade URL WCS/WMS con auth → pipeline genera tiles → terrain de alta resolución
5. **Self-hosted** → Apunta a su propio terrain server

## Correcciones aplicadas (2026-04-06)

### Auth (SOTA platform-compliant)
- `backend/app/middleware/auth.py` reescrito: Bearer + cookie `nkz_token` fallback
- RS256 JWT validation vía `python-jose` + JWKS
- `X-Tenant-ID` header priority, `TRUST_API_GATEWAY=true`, ADR 003 ready

### Backend — Nuevos modelos y endpoints
- `TenantTerrainPreferences` — BYOK tokens + provider selection por tenant
- `CustomDemSource` — fuentes DEM custom con auth headers opcionales
- `GET /api/elevation/providers` — lista todos los providers con estado activo
- `GET/PUT /api/elevation/preferences` — gestionar preferencias y tokens
- `GET/POST/DELETE /api/elevation/sources/custom` — CRUD fuentes DEM custom

### Frontend — Nuevos componentes
- `terrainFactory.ts` — Factory pattern para terrain providers
- `CustomDemSourceForm.tsx` — formulario para añadir fuentes DEM con auth
- `ElevationAdminControl.tsx` — reescrito con selector de providers + settings modal
- `ElevationLayer.tsx` — usa Factory en vez de instanciación directa
- `MainView.tsx` — integrado CustomDemSourceForm

### Limpieza
- Eliminados: `cdn-deployment.yaml`, `pvc.yaml`, `ingress.yaml`, `backend/nginx/`
- Terrain CDN redundante — tiles van a MinIO
- `imagePullPolicy: Always` en ambos deployments
- DB credentials a secret (`elevation-db-secret`)
- Cero credenciales hardcodeadas en repo público

### CI/CD
- `.github/workflows/build-push.yml` — validate → build IIFE → build+push Docker

### Dependencies
- `requirements.txt`: `python-jose[cryptography]`, `httpx`

## Pendiente para despliegue

1. Push a `main` → CI/CD build+push Docker
2. Build frontend: `pnpm install && pnpm run build:module`
3. Upload IIFE a MinIO: `python upload_module.py`
4. Crear secrets: `minio-secret` + `elevation-db-secret`
5. `kubectl apply -f k8s/backend-deployment.yaml`
6. Ejecutar `registration.sql` en PostgreSQL
7. Verificar e2e

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
| `DATABASE_URL` | (secret) | `elevation-db-secret` |
