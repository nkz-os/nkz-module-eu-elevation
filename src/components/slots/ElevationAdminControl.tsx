import { API_ORIGIN } from '../../utils/apiOrigin';
import React, { useState, useEffect, useMemo } from 'react';
import { Globe, Settings, Key, Link as LinkIcon } from 'lucide-react';
import { SlotShell } from '@nekazari/viewer-kit';
import { Stack, Button } from '@nekazari/ui-kit';
import { useAuth, NKZClient, useTranslation } from '@nekazari/sdk';

export interface ElevationLayer {
    id: string;
    name: string;
    url: string;
    bbox_minx?: number;
    bbox_miny?: number;
    bbox_maxx?: number;
    bbox_maxy?: number;
    is_active: boolean;
}

export interface TerrainPreference {
    tenant_id: string;
    provider_type: string;
    has_cesium_token: boolean;
    has_maptiler_key: boolean;
    custom_terrain_url?: string;
    lidar_mds_url?: string;
    auto_mode: boolean;
}

export interface TerrainProviderInfo {
    id: string;
    name: string;
    type: string;
    description: string;
    resolution: string;
    coverage: string;
    requires_token: boolean;
    is_active: boolean;
}

const elevationAccent = { base: '#64748B', soft: '#F1F5F9', strong: '#475569' };

export const ElevationAdminControl: React.FC = () => {
    const { t } = useTranslation('eu-elevation');
    const { getToken, getTenantId } = useAuth();

    const apiClient = useMemo(() => new NKZClient({
        baseUrl: `${API_ORIGIN}/api/elevation`,
        getToken,
        getTenantId
    }), [getToken, getTenantId]);

    const [providers, setProviders] = useState<TerrainProviderInfo[]>([]);
    const [prefs, setPrefs] = useState<TerrainPreference | null>(null);
    const [layers, setLayers] = useState<ElevationLayer[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [showSettings, setShowSettings] = useState(false);

    // Settings form state
    const [cesiumToken, setCesiumToken] = useState('');
    const [maptilerKey, setMaptilerKey] = useState('');
    const [customUrl, setCustomUrl] = useState('');

    useEffect(() => {
        Promise.all([
            apiClient.get<TerrainPreference>('/sources').catch(() => null),
            apiClient.get<TerrainProviderInfo[]>('/sources/catalog').catch(() => []),
            apiClient.get<ElevationLayer[]>('/sources/custom').catch(() => []),
        ]).then(([p, prov, lyr]) => {
            setPrefs(p);
            setProviders(Array.isArray(prov) ? prov : []);
            setLayers(Array.isArray(lyr) ? lyr : []);
            if (p) {
                setCustomUrl(p.custom_terrain_url || '');
            }
            setIsLoading(false);
        });
    }, []);

    const handleProviderChange = async (type: string) => {
        try {
            await apiClient.put('/preferences', { provider_type: type });
            setPrefs(prev => prev ? { ...prev, provider_type: type } : null);
            window.dispatchEvent(new CustomEvent('nkz.elevation.change', { detail: { mode: 'refresh' } }));
        } catch (err) {
            console.error('Failed to update provider:', err);
        }
    };

    const handleSaveTokens = async () => {
        try {
            const payload: any = {};
            if (cesiumToken) payload.cesium_ion_token = cesiumToken;
            if (maptilerKey) payload.maptiler_api_key = maptilerKey;
            payload.custom_terrain_url = customUrl;
            await apiClient.put('/preferences', payload);
            setShowSettings(false);
            window.dispatchEvent(new CustomEvent('nkz.elevation.change', { detail: { mode: 'refresh' } }));
        } catch (err) {
            console.error('Failed to save tokens:', err);
        }
    };

    return (
        <SlotShell
            moduleId="nkz-module-eu-elevation"
            title={t('globeTerrain', '3D Terrain')}
            icon={<Globe className="w-4 h-4" />}
            accent={elevationAccent}
        >
            <Stack gap="stack" className="relative overflow-hidden">
                <div className="flex justify-end">
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setShowSettings(!showSettings)}
                        leadingIcon={<Settings className="w-4 h-4" />}
                        title={t('terrainSettings', 'Terrain Settings')}
                    />
                </div>

                {isLoading ? (
                    <div className="text-nkz-xs text-nkz-text-muted">{t('loading', 'Loading...')}</div>
                ) : (
                    <div className="space-y-nkz-tight">
                        {/* Built-in providers */}
                        {providers.filter(p => p.type === 'europe_copernicus' || p.type === 'cesium_world' || p.type === 'maptiler').map(provider => (
                            <button
                                key={provider.id}
                                onClick={() => handleProviderChange(provider.type)}
                                className={`w-full text-left px-3 py-2 rounded-nkz-md text-nkz-sm transition-all ${
                                    prefs?.provider_type === provider.type
                                        ? 'bg-nkz-accent-soft border border-nkz-accent-base text-nkz-accent-strong'
                                        : 'bg-nkz-surface-sunken border border-nkz-border text-nkz-text-primary hover:bg-nkz-surface'
                                }`}
                            >
                                <div className="flex items-center justify-between">
                                    <span className="font-medium">{provider.name}</span>
                                    {prefs?.provider_type === provider.type && (
                                        <span className="text-nkz-xs bg-nkz-accent-soft text-nkz-accent-strong px-1.5 py-0.5 rounded-nkz-sm">{t('active', 'Active')}</span>
                                    )}
                                </div>
                                <div className="text-nkz-xs text-nkz-text-muted mt-0.5">{provider.resolution} · {provider.coverage}</div>
                                {provider.requires_token && !prefs?.has_maptiler_key && provider.type === 'maptiler' && (
                                    <div className="text-nkz-xs text-amber-600 mt-1 flex items-center gap-1">
                                        <Key className="w-3 h-3" /> {t('needsApiKey', 'API key required — click ⚙ to configure')}
                                    </div>
                                )}
                            </button>
                        ))}

                        {/* Custom ingested layers */}
                        {layers.filter(l => l.is_active).map(layer => (
                            <button
                                key={layer.id}
                                onClick={() => {
                                    apiClient.put('/preferences', { provider_type: 'custom', custom_terrain_url: layer.url });
                                    setPrefs(prev => prev ? { ...prev, provider_type: 'custom', custom_terrain_url: layer.url } : null);
                                    window.dispatchEvent(new CustomEvent('nkz.elevation.change', { detail: { mode: 'refresh' } }));
                                }}
                                className={`w-full text-left px-3 py-2 rounded-nkz-md text-nkz-sm transition-all ${
                                    prefs?.provider_type === 'custom' && prefs?.custom_terrain_url === layer.url
                                        ? 'bg-nkz-accent-soft border border-nkz-accent-base text-nkz-accent-strong'
                                        : 'bg-nkz-surface-sunken border border-nkz-border text-nkz-text-primary hover:bg-nkz-surface'
                                }`}
                            >
                                <div className="flex items-center justify-between">
                                    <span className="font-medium truncate">{layer.name}</span>
                                    {prefs?.provider_type === 'custom' && prefs?.custom_terrain_url === layer.url && (
                                        <span className="text-nkz-xs bg-nkz-accent-soft text-nkz-accent-strong px-1.5 py-0.5 rounded-nkz-sm">{t('active', 'Active')}</span>
                                    )}
                                </div>
                                <div className="text-nkz-xs text-nkz-text-muted mt-0.5 truncate">{layer.url}</div>
                            </button>
                        ))}

                        {/* Auto */}
                        <button
                            onClick={() => handleProviderChange('auto')}
                            className={`w-full text-left px-3 py-2 rounded-nkz-md text-nkz-sm transition-all mb-2 ${
                                prefs?.provider_type === 'auto'
                                    ? 'bg-nkz-accent-soft border border-nkz-accent-base text-nkz-accent-strong'
                                    : 'bg-nkz-surface-sunken border border-nkz-border text-nkz-text-primary hover:bg-nkz-surface'
                            }`}
                        >
                            <div className="flex items-center justify-between">
                                <span className="font-medium">{t('autoMode', 'Auto (Camera Match)')}</span>
                                {prefs?.provider_type === 'auto' && (
                                    <span className="text-nkz-xs bg-nkz-accent-soft text-nkz-accent-strong px-1.5 py-0.5 rounded-nkz-sm">{t('active', 'Active')}</span>
                                )}
                            </div>
                            <div className="text-nkz-xs text-nkz-text-muted mt-0.5">{t('autoModeDesc', 'Automatically selects best custom terrain based on view')}</div>
                        </button>

                        {/* Off */}
                        <button
                            onClick={() => handleProviderChange('off')}
                            className={`w-full text-left px-3 py-2 rounded-nkz-md text-nkz-sm transition-all ${
                                prefs?.provider_type === 'off'
                                    ? 'bg-nkz-surface-sunken border border-nkz-border text-nkz-text-primary'
                                    : 'bg-nkz-surface-sunken border border-nkz-border text-nkz-text-primary hover:bg-nkz-surface'
                            }`}
                        >
                            <span className="font-medium">{t('offMode', 'Off (Flat Map)')}</span>
                        </button>
                    </div>
                )}

                {/* Settings Modal */}
                {showSettings && (
                    <div className="absolute inset-0 bg-white dark:bg-slate-900 z-10 p-4 flex flex-col">
                        <div className="flex items-center justify-between mb-4">
                            <h4 className="font-semibold text-nkz-text-primary flex items-center gap-2">
                                <Settings className="w-4 h-4" /> {t('terrainSettings', 'Terrain Settings')}
                            </h4>
                            <button onClick={() => setShowSettings(false)} className="text-nkz-text-muted hover:text-nkz-text-primary">✕</button>
                        </div>

                        <div className="space-y-4 flex-1 overflow-y-auto">
                            <div>
                                <label className="text-nkz-xs font-medium text-nkz-text-secondary flex items-center gap-1 mb-1">
                                    <Key className="w-3 h-3" /> {t('cesiumIonToken', 'Cesium Ion Access Token')}
                                </label>
                                <input
                                    type="password"
                                    value={cesiumToken}
                                    onChange={e => setCesiumToken(e.target.value)}
                                    placeholder="eyJhbGciOi..."
                                    className="w-full bg-nkz-surface-sunken border border-nkz-border rounded-nkz-md px-3 py-2 text-nkz-sm font-mono focus:border-nkz-accent-base focus:ring-1 focus:ring-nkz-accent-base outline-none"
                                />
                                <p className="text-nkz-xs text-nkz-text-muted mt-1">{t('cesiumTokenHint', 'Get free at cesium.com/ion/signup')}</p>
                            </div>

                            <div>
                                <label className="text-nkz-xs font-medium text-nkz-text-secondary flex items-center gap-1 mb-1">
                                    <Key className="w-3 h-3" /> {t('maptilerApiKey', 'MapTiler API Key')}
                                </label>
                                <input
                                    type="password"
                                    value={maptilerKey}
                                    onChange={e => setMaptilerKey(e.target.value)}
                                    placeholder="..."
                                    className="w-full bg-nkz-surface-sunken border border-nkz-border rounded-nkz-md px-3 py-2 text-nkz-sm font-mono focus:border-nkz-accent-base focus:ring-1 focus:ring-nkz-accent-base outline-none"
                                />
                                <p className="text-nkz-xs text-nkz-text-muted mt-1">{t('maptilerKeyHint', 'Get free key at maptiler.com (100k tiles/month)')}</p>
                            </div>

                            <div>
                                <label className="text-nkz-xs font-medium text-nkz-text-secondary flex items-center gap-1 mb-1">
                                    <LinkIcon className="w-3 h-3" /> {t('customTerrainUrl', 'Custom Terrain URL')}
                                </label>
                                <input
                                    type="url"
                                    value={customUrl}
                                    onChange={e => setCustomUrl(e.target.value)}
                                    placeholder="https://your-server/terrain/layer.json"
                                    className="w-full bg-nkz-surface-sunken border border-nkz-border rounded-nkz-md px-3 py-2 text-nkz-sm font-mono focus:border-nkz-accent-base focus:ring-1 focus:ring-nkz-accent-base outline-none"
                                />
                            </div>
                        </div>

                        <Button
                            variant="primary"
                            onClick={handleSaveTokens}
                            className="w-full mt-4"
                        >
                            {t('saveSettings', 'Save Settings')}
                        </Button>
                    </div>
                )}
            </Stack>
        </SlotShell>
    );
};

export default ElevationAdminControl;
