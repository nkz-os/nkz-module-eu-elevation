/**
 * Terrain Provider Factory — SOTA multi-tier elevation for EU/UK.
 *
 * Tiers:
 *   - Europe Copernicus: GLO-30 ~30m, free, self-hosted on platform MinIO (no token)
 *   - Cesium World Terrain: Global ~30m (requires Cesium Ion token)
 *   - MapTiler: High-res EU/UK up to 50cm (requires API key)
 *   - Custom: User-provided quantized mesh URL (self-hosted or ingested)
 *
 * Usage:
 *   const provider = createTerrainProvider({ type: 'maptiler', apiKey: '...' });
 *   viewer.terrainProvider = provider;
 */

declare const Cesium: any;

export type TerrainProviderType = 'off' | 'europe_copernicus' | 'cesium_world' | 'maptiler' | 'custom' | 'auto';

export interface TerrainProviderConfig {
    type: TerrainProviderType;
    cesiumIonToken?: string;
    maptilerApiKey?: string;
    customUrl?: string;
    europeCopernicusUrl?: string;
}

/**
 * Create a Cesium terrain provider from configuration.
 * Returns EllipsoidTerrainProvider for 'off', or the appropriate provider.
 */
export function createTerrainProvider(config: TerrainProviderConfig): any {
    switch (config.type) {
        case 'europe_copernicus':
            return createEuropeCopernicusTerrain(config);
        case 'cesium_world':
            return createCesiumWorldTerrain(config.cesiumIonToken);
        case 'maptiler':
            return createMapTilerTerrain(config.maptilerApiKey);
        case 'custom':
            return createCustomTerrain(config.customUrl);
        case 'off':
        default:
            return new Cesium.EllipsoidTerrainProvider();
    }
}

function createCesiumWorldTerrain(token?: string): any {
    // Cesium 1.136 reads Cesium.Ion.defaultAccessToken for all Ion requests.
    // When the user explicitly chooses Cesium World Terrain with their own token,
    // we set it globally and DON'T restore the old one — restoring triggers
    // Cesium to re-validate sessions, causing black flashes on terrain reload.
    if (token) {
        Cesium.Ion.defaultAccessToken = token;
    }

    try {
        if (typeof Cesium.CesiumTerrainProvider?.fromIonAssetId === 'function') {
            return Cesium.CesiumTerrainProvider.fromIonAssetId(1, {
                requestVertexNormals: true,
                requestWaterMask: false,
            });
        }
        if (typeof Cesium.createWorldTerrain === 'function') {
            return Cesium.createWorldTerrain({
                requestVertexNormals: true,
                requestWaterMask: false,
            });
        }
        throw new Error('No compatible Cesium World Terrain API found (Cesium ' + (Cesium.VERSION || 'unknown') + ')');
    } catch (error) {
        console.warn('[Elevation] Cesium World Terrain failed, falling back to ellipsoid:', error);
        return new Cesium.EllipsoidTerrainProvider();
    }
}

function createMapTilerTerrain(apiKey?: string): any {
    if (!apiKey) {
        console.warn('[Elevation] MapTiler API key missing, falling back to ellipsoid');
        return new Cesium.EllipsoidTerrainProvider();
    }
    try {
        const url = `https://api.maptiler.com/tiles/terrain-quantized-mesh-v2/?key=${apiKey}`;
        return new Cesium.CesiumTerrainProvider({
            url,
            requestVertexNormals: true,
            requestWaterMask: false,
        });
    } catch (error) {
        console.warn('[Elevation] MapTiler terrain failed, falling back to ellipsoid:', error);
        return new Cesium.EllipsoidTerrainProvider();
    }
}

function createCustomTerrain(url?: string): any {
    if (!url) {
        console.warn('[Elevation] Custom terrain URL missing, falling back to ellipsoid');
        return new Cesium.EllipsoidTerrainProvider();
    }
    try {
        return new Cesium.CesiumTerrainProvider({
            url,
            requestVertexNormals: true,
            requestWaterMask: false,
        });
    } catch (error) {
        console.warn('[Elevation] Custom terrain failed, falling back to ellipsoid:', error);
        return new Cesium.EllipsoidTerrainProvider();
    }
}

function createEuropeCopernicusTerrain(config: TerrainProviderConfig): any {
    // On-demand sovereign terrain: Copernicus GLO-30 served live by the module
    // backend (GET /api/elevation/heightmap/{z}/{x}/{y}.png, MinIO write-through
    // cache — only viewed tiles are stored). Zooms below the module window use
    // the AWS Open Data terrarium fallback (globe view; detail is irrelevant).
    // Legacy bulk pre-ingested quantized-mesh tilesets are no longer required.
    // Legacy URL (/api/elevation/terrain/EU/layer.json) is reduced to its origin;
    // same-origin '' by default. The heightmap path is built by the provider.
    const idx = config.europeCopernicusUrl?.indexOf('/api/elevation') ?? -1;
    const base = idx >= 0
        ? config.europeCopernicusUrl!.slice(0, idx)
        : (config.europeCopernicusUrl || '').replace(/\/+$/, '');
    return createOnDemandHeightmapTerrain(base);
}

// ── On-demand heightmap provider ─────────────────────────────────────────────

const ON_DEMAND_MIN_ZOOM = 6; // must match backend MIN_ZOOM (services/heightmap.py)
const HEIGHTMAP_GRID = 65;    // heightmap samples per tile edge for Cesium
const TERRARIUM_FALLOFF = 'https://elevation-tiles-prod.s3.amazonaws.com/terrarium';

function createOnDemandHeightmapTerrain(baseUrl: string): any {
    // baseUrl: same-origin '' by default (module API behind the platform
    // gateway) or an explicit prefix passed via europeCopernicusUrl.
    let ctx: CanvasRenderingContext2D | null = null;

    return new Cesium.CustomHeightmapTerrainProvider({
        width: HEIGHTMAP_GRID,
        height: HEIGHTMAP_GRID,
        callback: async (x: number, y: number, level: number): Promise<Float32Array> => {
            // Low zooms (globe view): open-data global fallback, browser-direct.
            const url = level < ON_DEMAND_MIN_ZOOM
                ? `${TERRARIUM_FALLOFF}/${level}/${x}/${y}.png`
                : `${baseUrl}/api/elevation/heightmap/${level}/${x}/${y}.png`;
            // fetch default credentials ('same-origin') sends the session
            // cookie to the module endpoint while leaving the AWS fallback
            // cookie-free (S3 CORS does not allow credentials).
            const res = await fetch(url);
            if (!res.ok) {
                throw new Error(`heightmap tile ${level}/${x}/${y} failed: HTTP ${res.status}`);
            }
            const bitmap = await createImageBitmap(await res.blob());
            if (!ctx) {
                const canvas = document.createElement('canvas');
                canvas.width = HEIGHTMAP_GRID;
                canvas.height = HEIGHTMAP_GRID;
                ctx = canvas.getContext('2d', { willReadFrequently: true })!;
            }
            ctx.drawImage(bitmap, 0, 0, HEIGHTMAP_GRID, HEIGHTMAP_GRID);
            const data = ctx.getImageData(0, 0, HEIGHTMAP_GRID, HEIGHTMAP_GRID).data;
            bitmap.close();
            const heights = new Float32Array(HEIGHTMAP_GRID * HEIGHTMAP_GRID);
            for (let i = 0, j = 0; i < heights.length; i++, j += 4) {
                heights[i] = data[j] * 256 + data[j + 1] + data[j + 2] / 256 - 32768;
            }
            return heights;
        },
    });
}
