import React, { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { useAuth, NKZClient, useTranslation } from '@nekazari/sdk';
// The host env exposes Cesium globally
declare const Cesium: any;

export interface ElevationLayerConfig {
    id: string;
    name: string;
    url: string;
    bbox_minx?: number;
    bbox_miny?: number;
    bbox_maxx?: number;
    bbox_maxy?: number;
    is_active: boolean;
}

// CORINE Land Cover 2018 WMS configuration
const CLC_WMS_URL = 'https://image.discomap.eea.europa.eu/arcgis/services/Corine/CLC2018_WM/MapServer/WMSServer';
const CLC_WMS_LAYERS = '0'; // First layer = CLC 2018 raster

export const ElevationLayer: React.FC<{ viewer?: any }> = ({ viewer }) => {
    const { t } = useTranslation('eu-elevation');
    const { getToken, getTenantId } = useAuth();

    const apiClient = useMemo(() => new NKZClient({
        baseUrl: '/api/elevation',
        getToken,
        getTenantId
    }), [getToken, getTenantId]);

    const originalProviderRef = useRef<any>(null);
    const activeUrlRef = useRef<string | null>(null);
    const clcLayerRef = useRef<any>(null);
    const [isLoadingTiles, setIsLoadingTiles] = useState(false);

    const layersRef = useRef<ElevationLayerConfig[]>([]);
    const currentModeRef = useRef<string>('auto');

    // Fetch layers once for Auto mode calculations
    useEffect(() => {
        apiClient.get<ElevationLayerConfig[]>('/layers')
            .then(data => {
                layersRef.current = data;
                // Trigger auto check if currently in auto mode and viewer exists
                if (currentModeRef.current === 'auto' && viewer) {
                    checkAutoBBOX();
                }
            })
            .catch(err => console.error("Failed to fetch layers", err));
    }, [viewer]);

    const checkAutoBBOX = useCallback(() => {
        if (currentModeRef.current !== 'auto' || !viewer || !viewer.camera) return;

        try {
            const position = viewer.camera.positionCartographic;
            const lon = Cesium.Math.toDegrees(position.longitude);
            const lat = Cesium.Math.toDegrees(position.latitude);

            // Find first matching layer
            const match = layersRef.current.find((l: ElevationLayerConfig) => {
                if (l.bbox_minx == null || l.bbox_maxx == null || l.bbox_miny == null || l.bbox_maxy == null) return false;
                return lon >= l.bbox_minx && lon <= l.bbox_maxx && lat >= l.bbox_miny && lat <= l.bbox_maxy;
            });

            const targetUrl = match ? match.url : null;
            applyTerrain(targetUrl);
        } catch (e) {
            console.warn("Auto BBOX check failed", e);
        }
    }, [viewer]);

    const applyTerrain = (url: string | null) => {
        if (!viewer) return;

        // Don't recreate if it's already the active one
        if (activeUrlRef.current === url) return;

        try {
            if (!url) {
                // Flat Ellipsoid terrain
                viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
                activeUrlRef.current = null;
            } else {
                viewer.terrainProvider = new Cesium.CesiumTerrainProvider({
                    url: url,
                    requestVertexNormals: true,
                    requestWaterMask: false,
                });
                activeUrlRef.current = url;
            }
        } catch (error) {
            console.error("[nkz-module-eu-elevation] Failed to switch terrain provider", error);
        }
    };

    // CORINE Land Cover WMS layer management
    const addCLCLayer = useCallback(() => {
        if (!viewer || clcLayerRef.current) return;

        try {
            const clcProvider = new Cesium.WebMapServiceImageryProvider({
                url: CLC_WMS_URL,
                layers: CLC_WMS_LAYERS,
                parameters: {
                    transparent: true,
                    format: 'image/png',
                },
                rectangle: Cesium.Rectangle.fromDegrees(-32.0, 27.0, 45.0, 72.0), // EU + UK extent
                credit: new Cesium.Credit('© EEA Copernicus Land Monitoring Service — CORINE Land Cover 2018'),
            });

            clcLayerRef.current = viewer.imageryLayers.addImageryProvider(clcProvider);
            clcLayerRef.current.alpha = 0.6; // Semi-transparent overlay
        } catch (error) {
            console.error("[nkz-module-eu-elevation] Failed to add CLC layer", error);
        }
    }, [viewer]);

    const removeCLCLayer = useCallback(() => {
        if (!viewer || !clcLayerRef.current) return;

        try {
            viewer.imageryLayers.remove(clcLayerRef.current, true);
            clcLayerRef.current = null;
        } catch (error) {
            console.error("[nkz-module-eu-elevation] Failed to remove CLC layer", error);
        }
    }, [viewer]);

    useEffect(() => {
        if (!viewer || !viewer.scene) return;

        // Save original provider to restore on unmount if needed
        originalProviderRef.current = viewer.terrainProvider;

        const onTileLoadProgress = (queuedTiles: number) => {
            setIsLoadingTiles(queuedTiles > 0);
        };

        if (viewer.scene.globe.tileLoadProgressEvent) {
            viewer.scene.globe.tileLoadProgressEvent.addEventListener(onTileLoadProgress);
        }

        // Terrain mode change handler
        const onPrefChange = (e: any) => {
            const detail = e.detail;
            currentModeRef.current = detail.mode;

            if (detail.mode === 'off') {
                applyTerrain(null);
            } else if (detail.mode === 'layer' && detail.layer) {
                applyTerrain(detail.layer.url);
            } else if (detail.mode === 'auto') {
                checkAutoBBOX();
            }
        };

        // CLC toggle handler
        const onCLCToggle = (e: any) => {
            const enabled = e.detail?.enabled;
            if (enabled) {
                addCLCLayer();
            } else {
                removeCLCLayer();
            }
        };

        // Initialize terrain state
        const savedMode = localStorage.getItem('nkz_elevation_pref') || 'auto';
        currentModeRef.current = savedMode;

        if (savedMode === 'off') {
            applyTerrain(null);
        } else if (savedMode !== 'auto') {
            const found = layersRef.current.find(l => l.id === savedMode);
            if (found) applyTerrain(found.url);
        }

        // Initialize CLC state
        const clcEnabled = localStorage.getItem('nkz_clc_enabled') === 'true';
        if (clcEnabled) {
            addCLCLayer();
        }

        window.addEventListener('nkz.elevation.change', onPrefChange);
        window.addEventListener('nkz.clc.toggle', onCLCToggle);
        viewer.camera.moveEnd.addEventListener(checkAutoBBOX);

        // Cleanup
        return () => {
            window.removeEventListener('nkz.elevation.change', onPrefChange);
            window.removeEventListener('nkz.clc.toggle', onCLCToggle);
            removeCLCLayer();
            if (viewer && !viewer.isDestroyed()) {
                if (viewer.scene.globe.tileLoadProgressEvent) {
                    viewer.scene.globe.tileLoadProgressEvent.removeEventListener(onTileLoadProgress);
                }
                if (viewer.camera && viewer.camera.moveEnd) {
                    viewer.camera.moveEnd.removeEventListener(checkAutoBBOX);
                }
                if (originalProviderRef.current) {
                    viewer.terrainProvider = originalProviderRef.current;
                }
            }
        };
    }, [viewer]);

    return (
        <div className={`absolute top-6 left-1/2 transform -translate-x-1/2 bg-gray-900/90 text-gray-100 px-4 py-2 rounded-full shadow-lg text-sm flex items-center gap-2 z-50 backdrop-blur-md pointer-events-none transition-opacity duration-500 ease-in-out ${isLoadingTiles ? 'opacity-100' : 'opacity-0'}`}>
            <svg className="animate-spin h-4 w-4 text-green-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span className="font-medium text-green-300">{t('updatingRelief', 'Updating 3D Relief...')}</span>
        </div>
    );
};

export default ElevationLayer;
