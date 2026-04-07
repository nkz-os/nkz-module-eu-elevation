import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Layers } from 'lucide-react';

declare const Cesium: any;

const CLC_WMS_URL = 'https://image.discomap.eea.europa.eu/arcgis/services/Corine/CLC2018_WM/MapServer/WMSServer';
const CLC_WMS_LAYERS = '0';

export const CorineLandCoverToggle: React.FC<{ viewer?: any }> = ({ viewer }) => {
    const [enabled, setEnabled] = useState(false);
    const [opacity, setOpacity] = useState(0.6);
    const clcLayerRef = useRef<any>(null);
    const opacityRef = useRef(opacity);

    useEffect(() => { opacityRef.current = opacity; }, [opacity]);

    const addCLCLayer = useCallback(() => {
        if (!viewer || clcLayerRef.current) return;
        try {
            const clcProvider = new Cesium.WebMapServiceImageryProvider({
                url: CLC_WMS_URL,
                layers: CLC_WMS_LAYERS,
                parameters: { transparent: true, format: 'image/png' },
                rectangle: Cesium.Rectangle.fromDegrees(-32.0, 27.0, 45.0, 72.0),
                credit: new Cesium.Credit('© EEA Copernicus Land Monitoring Service — CORINE Land Cover 2018'),
            });
            clcLayerRef.current = viewer.imageryLayers.addImageryProvider(clcProvider);
            clcLayerRef.current.alpha = opacityRef.current;
        } catch {
            // Viewer not ready
        }
    }, [viewer]);

    const removeCLCLayer = useCallback(() => {
        if (!viewer || !clcLayerRef.current) return;
        try {
            viewer.imageryLayers.remove(clcLayerRef.current, true);
            clcLayerRef.current = null;
        } catch {
            // Layer already removed
        }
    }, [viewer]);

    useEffect(() => {
        if (enabled && viewer && !clcLayerRef.current) {
            addCLCLayer();
        } else if (!enabled && clcLayerRef.current) {
            removeCLCLayer();
        }
    }, [enabled, viewer, addCLCLayer, removeCLCLayer]);

    useEffect(() => {
        if (clcLayerRef.current) {
            clcLayerRef.current.alpha = opacity;
        }
    }, [opacity]);

    useEffect(() => {
        const saved = localStorage.getItem('nkz_clc_enabled') === 'true';
        const savedOpacity = parseFloat(localStorage.getItem('nkz_clc_opacity') || '0.6');
        if (saved) {
            setEnabled(true);
            setOpacity(savedOpacity);
        }
    }, []);

    useEffect(() => {
        localStorage.setItem('nkz_clc_enabled', String(enabled));
        localStorage.setItem('nkz_clc_opacity', String(opacity));
    }, [enabled, opacity]);

    useEffect(() => {
        if (!viewer) return;
        return () => {
            removeCLCLayer();
        };
    }, [viewer, removeCLCLayer]);

    return (
        <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-1.5 min-w-0">
                    <Layers className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                    <span className="text-xs font-medium text-gray-300 truncate">CORINE Land Cover</span>
                </div>
                <button
                    onClick={() => setEnabled(!enabled)}
                    className={`relative inline-flex h-5 w-8 items-center rounded-full transition-colors shrink-0 ${enabled ? 'bg-emerald-500' : 'bg-gray-600'}`}
                    role="switch"
                    aria-checked={enabled}
                >
                    <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${enabled ? 'translate-x-[18px]' : 'translate-x-[2px]'}`} />
                </button>
            </div>
            {enabled && (
                <div className="flex items-center gap-2 pl-5">
                    <span className="text-[10px] text-gray-500 w-6">0%</span>
                    <input
                        type="range"
                        min="0"
                        max="100"
                        value={Math.round(opacity * 100)}
                        onChange={e => setOpacity(parseInt(e.target.value) / 100)}
                        className="flex-1 h-1 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                    />
                    <span className="text-[10px] text-gray-500 w-8 text-right">{Math.round(opacity * 100)}%</span>
                </div>
            )}
        </div>
    );
};

export default CorineLandCoverToggle;
