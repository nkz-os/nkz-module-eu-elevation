import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useAuth, NKZClient, useTranslation } from '@nekazari/sdk';

/**
 * Admin Panel for triggering EU Elevation Ingestion process via BBOX.
 * Renders in a dashboard-widget or context-panel.
 */
export const TerrainIngestionForm: React.FC = () => {
    const { t } = useTranslation('eu-elevation');
    const { getToken, getTenantId } = useAuth();

    const apiClient = useMemo(() => new NKZClient({
        baseUrl: '',
        getToken,
        getTenantId
    }), [getToken, getTenantId]);
    const [activeTab, setActiveTab] = useState<'remote' | 'local'>('remote');
    const [countryCode, setCountryCode] = useState('uk');
    const [bbox, setBbox] = useState('');
    const [urls, setUrls] = useState('');
    const [localFile, setLocalFile] = useState<File | null>(null);
    const [status, setStatus] = useState<{ message: string; isError: boolean } | null>(null);
    const [loading, setLoading] = useState(false);
    const [customSources, setCustomSources] = useState<any[]>([]);

    useEffect(() => {
        apiClient.get('/api/elevation/sources/custom').then((res: any) => {
            if (Array.isArray(res)) setCustomSources(res);
        }).catch(console.error);
    }, [apiClient]);

    // WebSocket Progress State
    const [progress, setProgress] = useState<{ percent: number, message: string } | null>(null);
    const wsRef = useRef<WebSocket | null>(null);

    // Cleanup websocket on unmount
    useEffect(() => {
        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, []);

    const connectWebSocket = (jobId: string) => {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/elevation/ws/status/${jobId}`;

        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);

                setProgress({
                    percent: payload.progress || 0,
                    message: payload.message || `Status: ${payload.status}`
                });

                if (payload.status === 'SUCCESS') {
                    setStatus({ message: `Pipeline Completed! Data is ready in MinIO.`, isError: false });
                    setLoading(false);
                    ws.close();
                } else if (payload.status === 'FAILURE' || payload.error) {
                    setStatus({ message: `Pipeline Failed: ${payload.message}`, isError: true });
                    setLoading(false);
                    ws.close();
                }
            } catch (e) {
                console.error("Failed to parse WS message", e);
            }
        };

        ws.onerror = (error) => {
            console.error("WebSocket error:", error);
            setStatus({ message: "WebSocket connection error", isError: true });
            setLoading(false);
            ws.close();
        };

        ws.onclose = () => {
            wsRef.current = null;
        };
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setStatus(null);
        setProgress(null);

        if (wsRef.current) {
            wsRef.current.close();
        }

        try {
            // Parse BBOX (minX, minY, maxX, maxY)
            let parsedBbox: number[] | null = null;
            if (bbox.trim()) {
                parsedBbox = bbox.split(',').map(s => parseFloat(s.trim()));
                if (parsedBbox.length !== 4 || parsedBbox.some(isNaN)) {
                    throw new Error("Invalid BBOX format. Use 'minX,minY,maxX,maxY'");
                }
            } else if (activeTab === 'remote') {
                throw new Error("BBOX is required for remote URLs");
            }

            let response;
            if (activeTab === 'remote') {
                const parsedUrls = urls.split('\n').map(s => s.trim()).filter(s => s.length > 0);
                if (parsedUrls.length === 0) {
                    throw new Error(t('errNoSource', "Provide at least one source URL"));
                }

                response = await apiClient.post('/api/elevation/ingest', {
                    country_code: countryCode,
                    bbox: parsedBbox as [number, number, number, number],
                    source_urls: parsedUrls
                });
            } else {
                if (!localFile) throw new Error(t('errNoFile', "Please select a file to upload"));

                const formData = new FormData();
                formData.append('file', localFile);
                formData.append('country_code', countryCode);
                if (bbox.trim()) formData.append('bbox', bbox.trim());

                // For FormData we need the raw fetch because NKZClient forces 'application/json' 
                // in the Content-Type header which breaks the multipart boundary if not deeply customized
                const headers = new Headers();
                const token = getToken();
                const tenant = getTenantId();
                if (token) headers.set('Authorization', `Bearer ${token}`);
                if (tenant) headers.set('X-Tenant-ID', tenant);

                const res = await fetch('/api/elevation/upload', {
                    method: 'POST',
                    headers,
                    body: formData
                });

                if (!res.ok) {
                    const errorData = await res.json().catch(() => ({}));
                    throw new Error(errorData.detail || t('errUploadFailed', 'Ingestion request failed'));
                }
                response = await res.json();
            }

            const data = activeTab === 'remote' ? response : response;
            setStatus({ message: `${t('jobQueued', 'Job Queued:')} ${data.job_id}. ${t('connectingWorker', 'Connecting to worker...')}`, isError: false });
            connectWebSocket(data.job_id);

        } catch (error: any) {
            setStatus({ message: error.message, isError: true });
            setLoading(false);
        }
    };

    return (
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden p-6 w-full flex flex-col space-y-6">
            <div className="flex justify-between items-center border-b border-gray-100 pb-4">
                <h2 className="text-gray-800 font-semibold flex items-center gap-2">
                    🌍 {t('ingestionTitle', 'Run Ingestion Pipeline')}
                </h2>
                <span className="bg-green-100 text-green-700 font-medium text-xs px-2 py-1 rounded-md">{t('bboxTaskBadge', 'BBOX Task')}</span>
            </div>

            <p className="text-gray-500 text-sm">
                {t('ingestionDesc', 'Enqueue Quantized Mesh processing for a specific region.')}
            </p>

            <div className="flex space-x-2 border-b border-gray-200 pb-2">
                <button
                    onClick={() => setActiveTab('remote')}
                    className={`px-3 py-1 text-sm font-medium rounded transition-colors ${activeTab === 'remote' ? 'bg-blue-600 text-white shadow-sm' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
                >
                    {t('remoteUrlsTab', 'Remote URLs')}
                </button>
                <button
                    onClick={() => setActiveTab('local')}
                    className={`px-3 py-1 text-sm font-medium rounded transition-colors ${activeTab === 'local' ? 'bg-blue-600 text-white shadow-sm' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
                >
                    {t('localUploadTab', 'Local File Upload')}
                </button>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-col space-y-4">
                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1">
                        <label className="text-xs font-medium text-gray-600">{t('countryCode', 'Country/Region Code')}</label>
                        <input
                            type="text"
                            value={countryCode}
                            onChange={(e) => setCountryCode(e.target.value)}
                            placeholder="e.g. uk, es, nl"
                            className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-gray-800 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-shadow"
                            disabled={loading && progress !== null}
                        />
                    </div>

                    <div className="space-y-1">
                        <label className="text-xs font-medium text-gray-600">
                            {t('bboxLabel', 'Bounding Box (EPSG:4326)')} {activeTab === 'local' && <span className="text-xs text-gray-400">({t('optional', 'Optional')})</span>}
                        </label>
                        <input
                            type="text"
                            value={bbox}
                            onChange={(e) => setBbox(e.target.value)}
                            placeholder="minX, minY, maxX, maxY"
                            className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-gray-800 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-shadow"
                            disabled={loading && progress !== null}
                        />
                    </div>
                </div>

                {activeTab === 'remote' ? (
                    <div className="space-y-4">
                        <div className="space-y-1">
                            <label className="text-xs font-medium text-gray-600">{t('selectCustomSource', 'Use Existing Custom Source')}</label>
                            <select
                                className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-gray-800 text-sm focus:border-blue-500 outline-none"
                                onChange={(e) => {
                                    const src = customSources.find(s => s.id === e.target.value);
                                    if (src) {
                                        setCountryCode(src.country_code);
                                        if (src.bbox_minx != null) {
                                            setBbox(`${src.bbox_minx}, ${src.bbox_miny}, ${src.bbox_maxx}, ${src.bbox_maxy}`);
                                        }
                                        setUrls(src.service_url);
                                    }
                                }}
                            >
                                <option value="">-- {t('selectSourceOptional', 'Select a saved source (optional)')} --</option>
                                {customSources.map(s => (
                                    <option key={s.id} value={s.id}>{s.name} ({s.country_code})</option>
                                ))}
                            </select>
                        </div>
                        <div className="space-y-1">
                            <label className="text-xs font-medium text-gray-600">{t('sourceUrlsLabel', 'Source URLs (One per line)')}</label>
                            <textarea
                                value={urls}
                                onChange={(e) => setUrls(e.target.value)}
                                placeholder="https://server/wcs?request=GetCoverage..."
                                rows={3}
                                className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-gray-800 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-shadow font-mono"
                                disabled={loading && progress !== null}
                            />
                        </div>
                    </div>
                ) : (
                    <div className="space-y-1">
                        <label className="text-xs font-medium text-gray-600">{t('localFileLabel', 'Local DEM File (.tif, .asc)')}</label>
                        <input
                            type="file"
                            accept=".tif,.tiff,.asc"
                            onChange={(e) => setLocalFile(e.target.files?.[0] || null)}
                            className="w-full bg-white border border-gray-300 rounded-lg px-3 py-2 text-gray-800 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none file:mr-4 file:py-1 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100 transition-colors"
                            disabled={loading && progress !== null}
                        />
                    </div>
                )}

                <div className="pt-2">
                    <button
                        type="submit"
                        disabled={loading && progress !== null}
                        className={`w-full py-2.5 px-4 rounded-lg font-medium transition-all shadow-sm ${(loading && progress !== null) ? 'bg-gray-100 text-gray-400 cursor-not-allowed border border-gray-200' : 'bg-blue-600 hover:bg-blue-700 text-white'
                            }`}
                    >
                        {(loading && progress !== null) ? t('processing', 'Processing...') : t('startIngestionBtn', 'Start Ingestion Pipeline')}
                    </button>
                </div>
            </form>

            {/* Status & Real-time Progress Bar */}
            {(status || progress) && (
                <div className="mt-4 flex flex-col space-y-3">
                    {status && (
                        <div className={`p-3 rounded-lg text-sm border ${status.isError ? 'bg-red-50 text-red-700 border-red-200' : 'bg-green-50 text-green-700 border-green-200'}`}>
                            {status.message}
                        </div>
                    )}

                    {progress && !status?.isError && (
                        <div className="bg-gray-50 rounded-lg p-4 border border-gray-200 shadow-inner">
                            <div className="flex justify-between text-xs font-medium text-gray-600 mb-2">
                                <span>{progress.message}</span>
                                <span>{progress.percent}%</span>
                            </div>
                            <div className="w-full bg-gray-200 rounded-full h-2.5 overflow-hidden">
                                <div
                                    className="bg-blue-500 h-full rounded-full transition-all duration-500 ease-out"
                                    style={{ width: `${Math.max(0, Math.min(100, progress.percent))}%` }}
                                ></div>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

