import React, { useState, useEffect, useMemo } from 'react';
import { Layers, Globe, Map } from 'lucide-react';
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

export const ElevationAdminControl: React.FC = () => {
    const { t } = useTranslation('eu-elevation');
    const { getToken, getTenantId } = useAuth();

    const apiClient = useMemo(() => new NKZClient({
        baseUrl: '/api/elevation',
        getToken,
        getTenantId
    }), [getToken, getTenantId]);

    const [layers, setLayers] = useState<ElevationLayer[]>([]);
    const [selected, setSelected] = useState<string>('auto');
    const [isLoading, setIsLoading] = useState(true);
    const [clcEnabled, setCLCEnabled] = useState(false);

    useEffect(() => {
        // Load initial preferences
        const savedPref = localStorage.getItem('nkz_elevation_pref');
        if (savedPref) {
            setSelected(savedPref);
        }

        const savedCLC = localStorage.getItem('nkz_clc_enabled') === 'true';
        setCLCEnabled(savedCLC);

        const fetchLayers = async () => {
            try {
                const data = await apiClient.get<ElevationLayer[]>('/layers');
                setLayers(data);
            } catch (err) {
                console.error("Failed to fetch terrain layers", err);
            } finally {
                setIsLoading(false);
            }
        };

        fetchLayers();
    }, []);

    const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const mode = e.target.value;
        setSelected(mode);
        localStorage.setItem('nkz_elevation_pref', mode);

        let detail: any = { mode };
        if (mode !== 'auto' && mode !== 'off') {
            const layer = layers.find(l => l.id === mode);
            detail = { mode: 'layer', layer };
        }

        window.dispatchEvent(new CustomEvent('nkz.elevation.change', { detail }));
    };

    const handleCLCToggle = () => {
        const newState = !clcEnabled;
        setCLCEnabled(newState);
        localStorage.setItem('nkz_clc_enabled', String(newState));

        window.dispatchEvent(new CustomEvent('nkz.clc.toggle', {
            detail: { enabled: newState }
        }));
    };

    return (
        <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4 flex flex-col space-y-3 relative overflow-hidden">
            {/* Terrain Provider Header */}
            <div className="flex items-center gap-2 mb-1">
                <Globe className="w-5 h-5 text-green-600" />
                <h3 className="text-gray-800 font-semibold flex-1">{t('globeTerrain', '3D Terrain')}</h3>
            </div>

            <p className="text-xs text-gray-500 mb-2">
                {t('terrainSelectDesc', 'Select the active elevation provider for the 3D globe.')}
            </p>

            <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                    <Layers className="h-4 w-4 text-green-500" />
                </div>
                <select
                    value={selected}
                    onChange={handleChange}
                    disabled={isLoading}
                    className="w-full pl-10 pr-8 py-2.5 bg-gray-50 border border-gray-300 text-gray-800 text-sm font-medium rounded-lg focus:outline-none focus:ring-2 focus:ring-green-500/50 appearance-none transition-shadow"
                >
                    <option value="auto">{t('autoMode', 'Automatic (By Region)')}</option>
                    <option value="off">{t('offMode', 'Off (Flat Map)')}</option>
                    {layers.length > 0 && <optgroup label={t('customTopography', 'Custom Topography')}>
                        {layers.map(layer => (
                            <option key={layer.id} value={layer.id}>
                                {layer.name}
                            </option>
                        ))}
                    </optgroup>}
                </select>
                <div className="absolute inset-y-0 right-0 flex items-center px-2 pointer-events-none">
                    <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7"></path></svg>
                </div>
            </div>

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
                        aria-label={t('corineToggle', 'Toggle CORINE Land Cover layer')}
                    >
                        <span
                            className={`inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform ${clcEnabled ? 'translate-x-6' : 'translate-x-1'}`}
                        />
                    </button>
                </div>
            </div>
        </div>
    );
};

export default ElevationAdminControl;
