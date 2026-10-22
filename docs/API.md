# EU Elevation — Module API

Cluster-internal elevation service (`elevation-api-service:80`, namespace `nekazari`).
Serves three concerns:

1. **Terrain 3D for Cesium** — `/terrain/{country}/layer.json`, `/{z}/{x}/{y}.terrain`, `/providers`.
2. **Elevation queries** — `/point` (single point) and `/raster` (DEM grid).
3. **Source management** — `/sources`, `/ingest`, `/upload`, `/layers`.

This document covers the **elevation query** surface that other modules consume.

## Auth (read endpoints: `/point`, `/raster`)

`require_elevation_reader` accepts **either**:

| Caller | Headers |
|--------|---------|
| Cluster-internal service | `X-Internal-Service-Secret: <INTERNAL_SERVICE_SECRET>` (org secret) |
| Browser/module via api-gateway | `X-Tenant-ID` + `X-User-ID` + `X-Auth-Signature` (HMAC `{hexdigest}:{timestamp}`) |

Internal callers MUST send a non-empty `X-User-ID` alongside `X-Tenant-ID` if they are
not using the internal secret (see platform auth rules).

## `GET /api/elevation/point`

Elevation (m) for a single WGS84 point.

```
GET /api/elevation/point?lat=42.6394&lon=-2.0788&source=auto&purpose=weather
```

| Param | Type | Default | Meaning |
|-------|------|---------|---------|
| `lat`, `lon` | float | — | WGS84 coordinates (required) |
| `source` | string | `auto` | `auto` / `national` / `cnig` / `copernicus` (see below) |
| `purpose` | string | `auto` | `auto`/`precision`/`routing`/`weather`/`visualization` (cache-key + routing dimension) |
| `refresh` | bool | `false` | bypass cache |

**Contract — `elevation_m` is NULLABLE.** The endpoint always answers HTTP 200 for a
well-formed query; it never fabricates a `0.0` as a fallback. When no DEM source can
answer, `elevation_m` is `null` and `status` is `"unavailable"`.

Success:
```json
{
  "lat": 42.6394, "lon": -2.0788,
  "elevation_m": 572.83,
  "status": "ok",
  "source": { "id": "builtin:EU", "name": "Pan-European (Copernicus DEM 30m)",
              "category": "copernicus_s3", "is_bare_earth": true,
              "accuracy_v_m": null, "resolution_m": 30 },
  "error": null
}
```

Unavailable (no source could answer — this is the signal to a consumer that the value is unknown):
```json
{
  "lat": 0.0, "lon": 0.0,
  "elevation_m": null,
  "status": "unavailable",
  "source": null,
  "error": { "code": "no_dem_coverage", "message": "Point (0.0, 0.0) outside all DEM coverage areas" }
}
```
`error.code` is `no_dem_coverage` (point outside every source bbox) or `source_unavailable`
(every source failed).

### `source` selection

| Value | Order of sources tried |
|-------|------------------------|
| `auto` | Copernicus GLO-30 S3 → national WCS (if any covers the point). Copernicus first: a single, well-tested integration is the robust baseline. |
| `national` | national WCS → Copernicus fallback. Use for slope/aspect where resolution matters. |
| `cnig` | Spanish IGN WCS only (no silent fallback). |
| `copernicus` | Copernicus GLO-30 S3 only (no silent fallback). |

The response's `source.id` reports the source that actually answered. Explicit
(`cnig`/`copernicus`) selectors never silently swap to another source.

## `GET /api/elevation/raster`

DEM grid for a bbox (used by hydrology, weather-map). Grid data, no nullable-value
contract needed here (returns `elevations` 2D list + `source`).

```
GET /api/elevation/raster?min_lon=..&min_lat=..&max_lon=..&max_lat=..&resolution_m=10&purpose=..
```

## Consumers

| Module | Endpoint | Purpose |
|--------|----------|---------|
| weather-worker | `/point` | parcel altitude (`auto`) + slope/aspect (`source=national`, 5-point stencil) |
| agrienergy | `/point` | solar energy calculations |
| hydrology | `/raster` | watershed delineation / flow accumulation |
| weather-map | `/raster` | per-pixel meteorology rasters |

**Rule for new consumers:** never assume `HTTP 200` + numeric `elevation_m` means real
data — check `status == "ok"` (or `elevation_m is not None`) before using the value.
