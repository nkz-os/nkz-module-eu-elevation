import React, { useState, useEffect, useMemo } from 'react';
import { Globe, Map, Settings, Key, Link as LinkIcon, Trash2, Plus, RefreshCw, Layers, Info } from 'lucide-react';
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

export const ElevationAdminControl: React.FC = () => {
    const { t } = useTranslation('eu-elevation');
    const { getToken, getTenantId } = useAuth();

    const apiClient = useMemo(() => new NKZClient({
        baseUrl: '/api/elevation',
        getToken,
        getTenantId
    }), [getToken, getTenantId]);

    const [providers, setProviders] = useState<TerrainProviderInfo[]>([]);
    const [prefs, setPrefs] = useState<TerrainPreference | null>(null);
    const [layers, setLayers] = useState<ElevationLayer[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [showSettings, setShowSettings] = useState(false);
    const [clcEnabled, setCLCEnabled] = useState(false);

    // Settings form state
    const [cesiumToken, setCesiumToken] = useState('');
    const [maptilerKey, setMaptilerKey] = useState('');
    const [customUrl, setCustomUrl] = useState('');

    useEffect(() => {
        const savedCLC = localStorage.getItem('nkz_clc_enabled') === 'true';
        setCLCEnabled(savedCLC);

        Promise.all([
            apiClient.get<TerrainPreference>('/preferences').catch(() => null),
            apiClient.get<TerrainProviderInfo[]>('/providers').catch(() => []),
            apiClient.get<ElevationLayer[]>('/layers').catch(() => []),
        ]).then(([p, prov, lyr]) => {
            setPrefs(p);
            setProviders(prov);
            setLayers(lyr || []);
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

    const handleCLCToggle = () => {
        const newState = !clcEnabled;
        setCLCEnabled(newState);
        localStorage.setItem('nkz_clc_enabled', String(newState));
        window.dispatchEvent(new CustomEvent('nkz.clc.toggle', { detail: { enabled: newState } }));
    };

    return (
        <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4 flex flex-col space-y-3 relative overflow-hidden">
            <div className="flex items-center gap-2 mb-1">
                <Globe className="w-5 h-5 text-green-600" />
                <h3 className="text-gray-800 font-semibold flex-1">{t('globeTerrain', '3D Terrain')}</h3>
                <button
                    onClick={() => setShowSettings(!showSettings)}
                    className="p-1 text-gray-400 hover:text-gray-600 transition-colors rounded"
                    title={t('terrainSettings', 'Terrain Settings')}
                >
                    <Settings className="w-4 h-4" />
                </button>
            </div>

            <p className="text-xs text-gray-500 mb-2">
                {t('terrainSelectDesc', 'Select the active elevation provider for the 3D globe.')}
            </p>

            {isLoading ? (
                <div className="text-xs text-gray-400">{t('loading', 'Loading...')}</div>
            ) : (
                <div className="space-y-1">
                    {/* Built-in providers */}
                    {providers.filter(p => p.type === 'cesium_world' || p.type === 'maptiler').map(provider => (
                        <button
                            key={provider.id}
                            onClick={() => handleProviderChange(provider.type)}
                            className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-all ${
                                prefs?.provider_type === provider.type
                                    ? 'bg-green-50 border border-green-200 text-green-800'
                                    : 'bg-gray-50 border border-gray-100 text-gray-700 hover:bg-gray-100'
                            }`}
                        >
                            <div className="flex items-center justify-between">
                                <span className="font-medium">{provider.name}</span>
                                {prefs?.provider_type === provider.type && (
                                    <span className="text-xs bg-green-200 text-green-800 px-1.5 py-0.5 rounded">{t('active', 'Active')}</span>
                                )}
                            </div>
                            <div className="text-xs text-gray-500 mt-0.5">{provider.resolution} · {provider.coverage}</div>
                            {provider.requires_token && !prefs?.has_maptiler_key && provider.type === 'maptiler' && (
                                <div className="text-xs text-amber-600 mt-1 flex items-center gap-1">
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
                            className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-all ${
                                prefs?.provider_type === 'custom' && prefs?.custom_terrain_url === layer.url
                                    ? 'bg-green-50 border border-green-200 text-green-800'
                                    : 'bg-gray-50 border border-gray-100 text-gray-700 hover:bg-gray-100'
                            }`}
                        >
                            <div className="flex items-center justify-between">
                                <span className="font-medium truncate">{layer.name}</span>
                                {prefs?.provider_type === 'custom' && prefs?.custom_terrain_url === layer.url && (
                                    <span className="text-xs bg-green-200 text-green-800 px-1.5 py-0.5 rounded">{t('active', 'Active')}</span>
                                )}
                            </div>
                            <div className="text-xs text-gray-500 mt-0.5 truncate">{layer.url}</div>
                        </button>
                    ))}

                    {/* Off */}
                    <button
                        onClick={() => handleProviderChange('off')}
                        className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-all ${
                            prefs?.provider_type === 'off'
                                ? 'bg-gray-100 border border-gray-200 text-gray-800'
                                : 'bg-gray-50 border border-gray-100 text-gray-700 hover:bg-gray-100'
                        }`}
                    >
                        <span className="font-medium">{t('offMode', 'Off (Flat Map)')}</span>
                    </button>
                </div>
            )}

            {/* CORINE Land Cover Toggle */}
            <div className="border-t border-gray-100 pt-3 mt-1">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <Map className="w-4 h-4 text-emerald-600" />
                        <div>
                            <span className="text-sm font-medium text-gray-700">{t('corineLandCover', 'CORINE Land Cover')}</span>
                            <p className="text-xs text-gray-400">{t('corineDescription', 'EU/UK land use classification (2018)')}</p>
                        </div>
                    </div>
                    <button
                        onClick={handleCLCToggle}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-500/50 ${clcEnabled ? 'bg-emerald-500' : 'bg-gray-300'}`}
                        role="switch"
                        aria-checked={clcEnabled}
                    >
                        <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform ${clcEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
                    </button>
                </div>
            </div>

            {/* Settings Modal */}
            {showSettings && (
                <div className="absolute inset-0 bg-white/95 backdrop-blur-sm z-10 p-4 flex flex-col">
                    <div className="flex items-center justify-between mb-4">
                        <h4 className="font-semibold text-gray-800 flex items-center gap-2">
                            <Settings className="w-4 h-4" /> {t('terrainSettings', 'Terrain Settings')}
                        </h4>
                        <button onClick={() => setShowSettings(false)} className="text-gray-400 hover:text-gray-600">✕</button>
                    </div>

                    <div className="space-y-4 flex-1 overflow-y-auto">
                        <div>
                            <label className="text-xs font-medium text-gray-600 flex items-center gap-1 mb-1">
                                <Key className="w-3 h-3" /> {t('cesiumIonToken', 'Cesium Ion Access Token')}
                            </label>
                            <input
                                type="password"
                                value={cesiumToken}
                                onChange={e => setCesiumToken(e.target.value)}
                                placeholder="eyJhbGciOi..."
                                className="w-full bg-gray-50 border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:border-green-500 focus:ring-1 focus:ring-green-500 outline-none"
                            />
                            <p className="text-xs text-gray-400 mt-1">{t('cesiumTokenHint', 'Get free at cesium.com/ion/signup')}</p>
                        </div>

                        <div>
                            <label className="text-xs font-medium text-gray-600 flex items-center gap-1 mb-1">
                                <Key className="w-3 h-3" /> {t('maptilerApiKey', 'MapTiler API Key')}
                            </label>
                            <input
                                type="password"
                                value={maptilerKey}
                                onChange={e => setMaptilerKey(e.target.value)}
                                placeholder="..."
                                className="w-full bg-gray-50 border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:border-green-500 focus:ring-1 focus:ring-green-500 outline-none"
                            />
                            <p className="text-xs text-gray-400 mt-1">{t('maptilerKeyHint', 'Get free key at maptiler.com (100k tiles/month)')}</p>
                        </div>

                        <div>
                            <label className="text-xs font-medium text-gray-600 flex items-center gap-1 mb-1">
                                <LinkIcon className="w-3 h-3" /> {t('customTerrainUrl', 'Custom Terrain URL')}
                            </label>
                            <input
                                type="url"
                                value={customUrl}
                                onChange={e => setCustomUrl(e.target.value)}
                                placeholder="https://your-server/terrain/layer.json"
                                className="w-full bg-gray-50 border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:border-green-500 focus:ring-1 focus:ring-green-500 outline-none"
                            />
                        </div>
                    </div>

                    <button
                        onClick={handleSaveTokens}
                        className="w-full mt-4 py-2 bg-green-600 hover:bg-green-700 text-white font-medium rounded-lg transition-colors text-sm"
                    >
                        {t('saveSettings', 'Save Settings')}
                    </button>
                </div>
            )}
        </div>
    );
};

export default ElevationAdminControl;
