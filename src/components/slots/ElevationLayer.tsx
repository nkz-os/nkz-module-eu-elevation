import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useAuth, NKZClient, useTranslation } from '@nekazari/sdk';
import { createTerrainProvider, TerrainProviderConfig, TerrainProviderType } from '../../utils/terrainFactory';

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

export interface TerrainTokens {
    cesium_ion_token?: string;
    maptiler_api_key?: string;
    custom_terrain_url?: string;
    provider_type: string;
}

export const ElevationLayer: React.FC<{ viewer?: any }> = ({ viewer }) => {
    const { t } = useTranslation('eu-elevation');
    const { getToken, getTenantId } = useAuth();

    const apiClient = React.useMemo(() => new NKZClient({
        baseUrl: '/api/elevation',
        getToken,
        getTenantId
    }), [getToken, getTenantId]);

    const activeProviderRef = useRef<any>(null);
    const [isLoadingTiles, setIsLoadingTiles] = useState(false);
    const layersRef = useRef<ElevationLayerConfig[]>([]);
    const tokensRef = useRef<TerrainTokens | null>(null);
    const currentModeRef = useRef<TerrainProviderType>('off');

    // Fetch tokens + layers on mount
    useEffect(() => {
        Promise.all([
            apiClient.get<TerrainTokens>('/preferences/tokens').catch(() => null),
            apiClient.get<ElevationLayerConfig[]>('/layers').catch(() => []),
        ]).then(([tok, layers]) => {
            tokensRef.current = tok;
            layersRef.current = layers || [];
            if (viewer && tok) {
                applyPreference(tok, layers || []);
            }
        });
    }, [viewer]);

    const applyPreference = useCallback((tok: TerrainTokens, layers: ElevationLayerConfig[]) => {
        if (!viewer) return;
        currentModeRef.current = tok.provider_type as TerrainProviderType;

        let config: TerrainProviderConfig;

        if (tok.provider_type === 'auto') {
            const match = findLayerByCameraPosition(layers);
            config = { type: match ? 'custom' : 'off', customUrl: match?.url };
        } else if (tok.provider_type === 'custom' && tok.custom_terrain_url) {
            config = { type: 'custom', customUrl: tok.custom_terrain_url };
        } else if (tok.provider_type === 'maptiler') {
            config = { type: 'maptiler', maptilerApiKey: tok.maptiler_api_key };
        } else if (tok.provider_type === 'cesium_world') {
            config = { type: 'cesium_world', cesiumIonToken: tok.cesium_ion_token };
        } else {
            config = { type: 'off' };
        }

        const provider = createTerrainProvider(config);
        setTerrainProvider(provider);
    }, [viewer]);

    const findLayerByCameraPosition = (layers: ElevationLayerConfig[]): ElevationLayerConfig | null => {
        if (!viewer?.camera) return null;
        try {
            const pos = viewer.camera.positionCartographic;
            const lon = Cesium.Math.toDegrees(pos.longitude);
            const lat = Cesium.Math.toDegrees(pos.latitude);
            return layers.find(l => {
                if (l.bbox_minx == null || l.bbox_maxx == null || l.bbox_miny == null || l.bbox_maxy == null) return false;
                return lon >= l.bbox_minx && lon <= l.bbox_maxx && lat >= l.bbox_miny && lat <= l.bbox_maxy;
            }) || null;
        } catch {
            return null;
        }
    };

    const setTerrainProvider = (provider: any) => {
        if (!viewer) return;
        activeProviderRef.current = provider;
        try {
            viewer.terrainProvider = provider;
        } catch (error) {
            console.error('[Elevation] Failed to set terrain provider:', error);
        }
    };

    useEffect(() => {
        if (!viewer?.scene) return;

        const onTileLoadProgress = (queuedTiles: number) => {
            setIsLoadingTiles(queuedTiles > 0);
        };
        if (viewer.scene.globe.tileLoadProgressEvent) {
            viewer.scene.globe.tileLoadProgressEvent.addEventListener(onTileLoadProgress);
        }

        const onPrefChange = (e: any) => {
            const detail = e.detail;
            if (detail.mode === 'refresh') {
                Promise.all([
                    apiClient.get<TerrainTokens>('/preferences/tokens').catch(() => null),
                    apiClient.get<ElevationLayerConfig[]>('/layers').catch(() => []),
                ]).then(([tok, layers]) => {
                    if (tok) {
                        tokensRef.current = tok;
                        layersRef.current = layers || [];
                        applyPreference(tok, layers || []);
                    }
                });
            }
        };

        window.addEventListener('nkz.elevation.change', onPrefChange);
        viewer.camera.moveEnd.addEventListener(() => {
            if (currentModeRef.current === 'auto') {
                applyPreference(tokensRef.current || { provider_type: 'off' }, layersRef.current);
            }
        });

        return () => {
            window.removeEventListener('nkz.elevation.change', onPrefChange);
            if (viewer && !viewer.isDestroyed()) {
                if (viewer.scene.globe.tileLoadProgressEvent) {
                    viewer.scene.globe.tileLoadProgressEvent.removeEventListener(onTileLoadProgress);
                }
                viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
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
