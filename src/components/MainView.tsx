import { API_ORIGIN } from '../utils/apiOrigin';
import React, { useState, useEffect, useMemo } from 'react';
import { TerrainIngestionForm } from './TerrainIngestionForm';
import { CustomDemSourceForm } from './CustomDemSourceForm';
import { ElevationAdminControl } from './slots/ElevationAdminControl';
import { Trash2, Plus, RefreshCw, Layers, Map as MapIcon, ArrowRight, Settings } from 'lucide-react';
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
    provider_type: string;
    custom_terrain_url?: string;
}

export const MainView: React.FC = () => {
    const { t } = useTranslation('eu-elevation');
    const { getToken, getTenantId } = useAuth();

    const apiClient = useMemo(() => new NKZClient({
        baseUrl: `${API_ORIGIN}/api/elevation`,
        getToken,
        getTenantId
    }), [getToken, getTenantId]);

    const [layers, setLayers] = useState<ElevationLayer[]>([]);
    const [prefs, setPrefs] = useState<TerrainPreference | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [showAdvanced, setShowAdvanced] = useState(false);

    const [newName, setNewName] = useState('');
    const [newUrl, setNewUrl] = useState('');
    const [newBbox, setNewBbox] = useState('');

    const fetchData = async () => {
        setIsLoading(true);
        try {
            const [layerData, prefData] = await Promise.all([
                apiClient.get<ElevationLayer[]>('/layers'),
                apiClient.get<TerrainPreference>('/preferences').catch(() => null)
            ]);
            setLayers(layerData || []);
            setPrefs(prefData);
        } catch (err) {
            console.error("Failed to fetch module data", err);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => { 
        fetchData();
        // Listen for changes from the control panel
        const handlePrefChange = () => fetchData();
        window.addEventListener('nkz.elevation.change', handlePrefChange);
        return () => window.removeEventListener('nkz.elevation.change', handlePrefChange);
    }, []);

    const handleDelete = async (id: string) => {
        if (!window.confirm(t('confirmDeleteLayer', 'Delete this terrain layer?'))) return;
        try {
            await apiClient.delete(`/layers/${id}`);
            setLayers(layers.filter(l => l.id !== id));
        } catch (err) {
            console.error("Failed to delete layer", err);
        }
    };

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();

        try {
            const u = new URL(newUrl);
            if (!/^https?:$/.test(u.protocol)) {
                throw new Error('protocol');
            }
        } catch {
            window.alert(t('errInvalidUrl', 'Invalid URL — must start with http:// or https://'));
            return;
        }

        let bboxArgs = {};
        if (newBbox.trim()) {
            const parts = newBbox.split(',').map(s => parseFloat(s.trim()));
            if (parts.length === 4 && !parts.some(isNaN)) {
                bboxArgs = { bbox_minx: parts[0], bbox_miny: parts[1], bbox_maxx: parts[2], bbox_maxy: parts[3] };
            }
        }
        try {
            await apiClient.post('/layers', { name: newName, url: newUrl, is_active: true, ...bboxArgs });
            setNewName(''); setNewUrl(''); setNewBbox(''); setShowAdvanced(false);
            fetchData();
        } catch (err) {
            console.error("Failed to create layer", err);
        }
    };

    const activeProviderName = useMemo(() => {
        if (!prefs) return '...';
        if (prefs.provider_type === 'auto') return t('autoMode', 'Auto (Camera Match)');
        if (prefs.provider_type === 'europe_copernicus') return t('europeCopernicus', 'Copernicus EU Terrain');
        if (prefs.provider_type === 'cesium_world') return 'Cesium World Terrain';
        if (prefs.provider_type === 'maptiler') return 'MapTiler Terrain';
        if (prefs.provider_type === 'lidar_mds') return t('lidarMds', 'Surface Model (MDS)');
        if (prefs.provider_type === 'custom') {
            const layer = layers.find(l => l.url === prefs.custom_terrain_url);
            return layer ? layer.name : 'Custom URL';
        }
        return t('offMode', 'Off (Flat Map)');
    }, [prefs, layers, t]);

    return (
        <div className="w-full h-full p-4 lg:p-8 bg-gray-50 border-l border-gray-200 overflow-y-auto custom-scrollbar">
            <div className="max-w-7xl mx-auto space-y-6">
                
                {/* 1. STATUS HEADER */}
                <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div className="flex items-center gap-4">
                        <div className="w-12 h-12 bg-blue-100 rounded-xl flex items-center justify-center text-blue-600">
                            <Layers className="w-6 h-6" />
                        </div>
                        <div>
                            <h1 className="text-xl font-bold text-gray-900 leading-tight">{t('title', 'EU Elevation Module')}</h1>
                            <div className="flex items-center gap-2 mt-0.5">
                                <span className="text-xs text-gray-500">{t('activeProvider', 'Active Provider')}:</span>
                                <span className="text-xs font-semibold px-2 py-0.5 bg-green-100 text-green-700 rounded-full">
                                    {activeProviderName}
                                </span>
                            </div>
                        </div>
                    </div>
                    <a
                        href="/entities"
                        className="flex items-center justify-center gap-2 bg-gray-900 hover:bg-black text-white px-5 py-2.5 rounded-xl font-medium transition-all shadow-md active:scale-95 shrink-0"
                    >
                        <MapIcon className="w-4 h-4" />
                        {t('viewOnMap', 'View on Map')}
                        <ArrowRight className="w-4 h-4 ml-1 opacity-50" />
                    </a>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
                    {/* 2. MAIN OPERATION: INGESTION */}
                    <div className="lg:col-span-8 space-y-6">
                        <TerrainIngestionForm />
                        
                        {/* Processed Layers List (Simplified) */}
                        <section className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
                            <div className="p-5 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:bg-slate-800">
                                <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2 uppercase tracking-wider">
                                    <RefreshCw className="w-4 h-4 text-green-600" />
                                    {t('configuredSources', 'Processed Terrain Layers')}
                                </h2>
                                <button onClick={fetchData} className="p-1.5 text-gray-400 hover:text-gray-700 transition-colors rounded-lg">
                                    <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
                                </button>
                            </div>

                            <div className="divide-y divide-gray-100 max-h-[400px] overflow-y-auto custom-scrollbar">
                                {layers.length === 0 && !isLoading ? (
                                    <div className="p-8 text-center text-gray-400 text-sm">
                                        {t('noSources', 'No layers yet.')}
                                    </div>
                                ) : (
                                    layers.map(layer => (
                                        <div key={layer.id} className="p-4 flex items-center justify-between group hover:bg-gray-50 transition-colors">
                                            <div className="min-w-0 flex-1">
                                                <div className="flex items-center gap-2">
                                                    <h3 className="font-medium text-gray-800 text-sm truncate">{layer.name}</h3>
                                                    {prefs?.custom_terrain_url === layer.url && (
                                                        <span className="text-[10px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded-md font-bold uppercase">Active</span>
                                                    )}
                                                </div>
                                                <p className="text-[10px] text-gray-400 font-mono truncate mt-0.5">{layer.url}</p>
                                            </div>
                                            <button
                                                onClick={() => handleDelete(layer.id)}
                                                className="p-2 text-gray-300 hover:text-red-600 transition-all ml-2"
                                            >
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                    ))
                                )}
                            </div>

                            {/* Advanced: Manual Add */}
                            <div className="p-3 bg-gray-50 border-t border-gray-100">
                                {!showAdvanced ? (
                                    <button 
                                        onClick={() => setShowAdvanced(true)}
                                        className="text-[11px] text-gray-500 hover:text-blue-600 font-medium flex items-center gap-1 mx-auto"
                                    >
                                        <Plus className="w-3 h-3" /> {t('addManualLayer', 'Add layer manually (Advanced)')}
                                    </button>
                                ) : (
                                    <form onSubmit={handleCreate} className="space-y-3 p-2">
                                        <div className="grid grid-cols-2 gap-3">
                                            <input type="text" required value={newName} onChange={e => setNewName(e.target.value)}
                                                placeholder={t('myTerrainPlaceholder', "My Terrain")}
                                                className="w-full bg-white border border-gray-300 rounded-lg px-3 py-1.5 text-xs outline-none" />
                                            <input type="text" value={newBbox} onChange={e => setNewBbox(e.target.value)}
                                                placeholder={t('bboxPlaceholder', "minX, minY, maxX, maxY")}
                                                className="w-full bg-white border border-gray-300 rounded-lg px-3 py-1.5 text-xs outline-none" />
                                        </div>
                                        <div className="flex gap-2">
                                            <input type="url" required value={newUrl} onChange={e => setNewUrl(e.target.value)}
                                                placeholder={t('terrainProviderUrlPlaceholder', "https://...")}
                                                className="flex-1 bg-white border border-gray-300 rounded-lg px-3 py-1.5 text-xs font-mono outline-none" />
                                            <button type="submit" className="bg-blue-600 text-white px-3 py-1.5 rounded-lg text-xs font-medium shrink-0">Add</button>
                                            <button type="button" onClick={() => setShowAdvanced(false)} className="text-gray-400 text-xs px-2">Cancel</button>
                                        </div>
                                    </form>
                                )}
                            </div>
                        </section>
                    </div>

                    {/* 3. SETTINGS & SOURCES */}
                    <div className="lg:col-span-4 space-y-6">
                        {/* Selector (Reused widget) */}
                        <div className="shadow-sm rounded-2xl overflow-hidden border border-gray-200 bg-white">
                            <div className="p-4 border-b border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-slate-800">
                                <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2 uppercase tracking-wider">
                                    <Settings className="w-4 h-4 text-blue-600" />
                                    {t('terrainControl', 'Terrain Selection')}
                                </h2>
                            </div>
                            <ElevationAdminControl />
                        </div>

                        <CustomDemSourceForm />
                    </div>
                </div>
            </div>
        </div>
    );
};

export default MainView;
