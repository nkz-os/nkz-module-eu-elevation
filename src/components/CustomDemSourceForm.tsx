import React, { useState, useMemo } from 'react';
import { useAuth, NKZClient, useTranslation } from '@nekazari/sdk';
import { Plus, Trash2, Globe, Key, Save } from 'lucide-react';

export interface CustomDemSource {
    id: string;
    name: string;
    country_code?: string;
    service_url: string;
    service_type: string;
    format: string;
    resolution?: string;
    layer_name?: string;
    bbox_minx?: number;
    bbox_miny?: number;
    bbox_maxx?: number;
    bbox_maxy?: number;
    has_auth: boolean;
    is_active: boolean;
    notes?: string;
}

export const CustomDemSourceForm: React.FC = () => {
    const { t } = useTranslation('eu-elevation');
    const { getToken, getTenantId } = useAuth();

    const apiClient = useMemo(() => new NKZClient({
        baseUrl: '/api/elevation',
        getToken,
        getTenantId
    }), [getToken, getTenantId]);

    const [sources, setSources] = useState<CustomDemSource[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [showForm, setShowForm] = useState(false);

    // Form state
    const [name, setName] = useState('');
    const [countryCode, setCountryCode] = useState('');
    const [serviceUrl, setServiceUrl] = useState('');
    const [serviceType, setServiceType] = useState('WCS');
    const [format, setFormat] = useState('GeoTIFF');
    const [resolution, setResolution] = useState('');
    const [layerName, setLayerName] = useState('');
    const [bbox, setBbox] = useState('');
    const [authHeaderName, setAuthHeaderName] = useState('');
    const [authHeaderValue, setAuthHeaderValue] = useState('');
    const [notes, setNotes] = useState('');

    React.useEffect(() => {
        apiClient.get<CustomDemSource[]>('/sources/custom')
            .then(data => setSources(data))
            .catch(() => {})
            .finally(() => setIsLoading(false));
    }, []);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!name || !serviceUrl) return;

        let bboxArgs = {};
        if (bbox.trim()) {
            const parts = bbox.split(',').map(s => parseFloat(s.trim()));
            if (parts.length === 4 && !parts.some(isNaN)) {
                bboxArgs = { bbox_minx: parts[0], bbox_miny: parts[1], bbox_maxx: parts[2], bbox_maxy: parts[3] };
            }
        }

        try {
            const newSource = await apiClient.post<CustomDemSource>('/sources/custom', {
                name,
                country_code: countryCode || undefined,
                service_url: serviceUrl,
                service_type: serviceType,
                format,
                resolution: resolution || undefined,
                layer_name: layerName || undefined,
                auth_header_name: authHeaderName || undefined,
                auth_header_value: authHeaderValue || undefined,
                notes: notes || undefined,
                ...bboxArgs,
            });
            setSources([...sources, newSource]);
            resetForm();
        } catch (err) {
            console.error('Failed to create custom DEM source:', err);
        }
    };

    const handleDelete = async (id: string) => {
        if (!window.confirm(t('confirmDeleteSource', 'Delete this DEM source?'))) return;
        try {
            await apiClient.delete(`/sources/custom/${id}`);
            setSources(sources.filter(s => s.id !== id));
        } catch (err) {
            console.error('Failed to delete custom DEM source:', err);
        }
    };

    const resetForm = () => {
        setName(''); setCountryCode(''); setServiceUrl('');
        setServiceType('WCS'); setFormat('GeoTIFF'); setResolution('');
        setLayerName(''); setBbox(''); setAuthHeaderName('');
        setAuthHeaderValue(''); setNotes(''); setShowForm(false);
    };

    return (
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
            <div className="p-5 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
                <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
                    <Globe className="w-5 h-5 text-blue-600" />
                    {t('customDemSources', 'Custom DEM Sources')}
                </h2>
                {!showForm && (
                    <button
                        onClick={() => setShowForm(true)}
                        className="p-2 text-gray-400 hover:text-blue-600 transition-colors rounded-full hover:bg-blue-50"
                    >
                        <Plus className="w-4 h-4" />
                    </button>
                )}
            </div>

            {/* Source List */}
            <div className="divide-y divide-gray-100">
                {isLoading ? (
                    <div className="p-6 text-center text-gray-400 text-sm">{t('loading', 'Loading...')}</div>
                ) : sources.length === 0 ? (
                    <div className="p-6 text-center text-gray-500 text-sm">
                        <p>{t('noCustomSources', 'No custom DEM sources registered.')}</p>
                        <p className="text-xs text-gray-400 mt-1">{t('customSourcesHint', 'Add a WCS/WMS endpoint to process terrain from any provider.')}</p>
                    </div>
                ) : (
                    sources.map(source => (
                        <div key={source.id} className="p-4 flex items-start justify-between group hover:bg-gray-50 transition-colors">
                            <div className="space-y-1 flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                    <h3 className="font-medium text-gray-800">{source.name}</h3>
                                    {source.has_auth && (
                                        <Key className="w-3 h-3 text-amber-500" />
                                    )}
                                </div>
                                <p className="text-xs text-gray-500 font-mono truncate">{source.service_url}</p>
                                <div className="flex gap-2 text-xs text-gray-400">
                                    <span className="bg-gray-100 px-1.5 py-0.5 rounded">{source.service_type}</span>
                                    <span className="bg-gray-100 px-1.5 py-0.5 rounded">{source.format}</span>
                                    {source.resolution && <span className="bg-gray-100 px-1.5 py-0.5 rounded">{source.resolution}</span>}
                                    {source.country_code && <span className="bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">{source.country_code}</span>}
                                </div>
                            </div>
                            <button
                                onClick={() => handleDelete(source.id)}
                                className="p-1.5 text-gray-300 hover:text-red-600 hover:bg-red-50 rounded opacity-0 group-hover:opacity-100 transition-all"
                            >
                                <Trash2 className="w-4 h-4" />
                            </button>
                        </div>
                    ))
                )}
            </div>

            {/* Add Form */}
            {showForm && (
                <form onSubmit={handleSubmit} className="p-5 bg-gray-50 border-t border-gray-200 space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="text-xs font-medium text-gray-600">{t('sourceName', 'Source Name')} *</label>
                            <input type="text" required value={name} onChange={e => setName(e.target.value)}
                                placeholder="e.g. IGN Spain WCS"
                                className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none" />
                        </div>
                        <div>
                            <label className="text-xs font-medium text-gray-600">{t('countryCode', 'Country Code')}</label>
                            <input type="text" value={countryCode} onChange={e => setCountryCode(e.target.value.toUpperCase())}
                                placeholder="ES" maxLength={2}
                                className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none uppercase" />
                        </div>
                    </div>

                    <div>
                        <label className="text-xs font-medium text-gray-600">{t('serviceUrl', 'Service URL')} *</label>
                        <input type="url" required value={serviceUrl} onChange={e => setServiceUrl(e.target.value)}
                            placeholder="https://server/wcs?request=GetCoverage"
                            className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none" />
                    </div>

                    <div className="grid grid-cols-3 gap-3">
                        <div>
                            <label className="text-xs font-medium text-gray-600">{t('serviceType', 'Type')}</label>
                            <select value={serviceType} onChange={e => setServiceType(e.target.value)}
                                className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none">
                                <option value="WCS">WCS</option>
                                <option value="WMS">WMS</option>
                                <option value="DOWNLOAD">DOWNLOAD</option>
                                <option value="REST">REST</option>
                            </select>
                        </div>
                        <div>
                            <label className="text-xs font-medium text-gray-600">{t('format', 'Format')}</label>
                            <select value={format} onChange={e => setFormat(e.target.value)}
                                className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none">
                                <option value="GeoTIFF">GeoTIFF</option>
                                <option value="ASCII">ASCII</option>
                            </select>
                        </div>
                        <div>
                            <label className="text-xs font-medium text-gray-600">{t('resolution', 'Resolution')}</label>
                            <input type="text" value={resolution} onChange={e => setResolution(e.target.value)}
                                placeholder="1m"
                                className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none" />
                        </div>
                    </div>

                    <div>
                        <label className="text-xs font-medium text-gray-600">{t('layerName', 'Layer Name')}</label>
                        <input type="text" value={layerName} onChange={e => setLayerName(e.target.value)}
                            placeholder="EL.ElevationGridCoverage"
                            className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none" />
                    </div>

                    <div>
                        <label className="text-xs font-medium text-gray-600">{t('bboxOptional', 'Bounding Box (Optional)')}</label>
                        <input type="text" value={bbox} onChange={e => setBbox(e.target.value)}
                            placeholder="west, south, east, north"
                            className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none" />
                    </div>

                    {/* Auth section */}
                    <div className="border-t border-gray-200 pt-3">
                        <p className="text-xs font-medium text-gray-600 mb-2 flex items-center gap-1">
                            <Key className="w-3 h-3" /> {t('optionalAuth', 'Optional Authentication')}
                        </p>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="text-xs text-gray-500">{t('authHeaderName', 'Header Name')}</label>
                                <input type="text" value={authHeaderName} onChange={e => setAuthHeaderName(e.target.value)}
                                    placeholder="X-API-Key"
                                    className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none" />
                            </div>
                            <div>
                                <label className="text-xs text-gray-500">{t('authHeaderValue', 'Header Value')}</label>
                                <input type="password" value={authHeaderValue} onChange={e => setAuthHeaderValue(e.target.value)}
                                    placeholder="••••••••"
                                    className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none" />
                            </div>
                        </div>
                    </div>

                    <div>
                        <label className="text-xs font-medium text-gray-600">{t('notes', 'Notes')}</label>
                        <input type="text" value={notes} onChange={e => setNotes(e.target.value)}
                            placeholder="Optional description"
                            className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none" />
                    </div>

                    <div className="flex justify-end gap-2 pt-2">
                        <button type="button" onClick={resetForm}
                            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors rounded-lg hover:bg-gray-100">
                            {t('cancel', 'Cancel')}
                        </button>
                        <button type="submit"
                            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition-colors shadow-sm flex items-center gap-1">
                            <Save className="w-3 h-3" /> {t('registerSource', 'Register Source')}
                        </button>
                    </div>
                </form>
            )}
        </div>
    );
};

export default CustomDemSourceForm;
