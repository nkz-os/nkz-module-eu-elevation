import React, { useState, useEffect } from 'react';
import { Layers, ChevronDown, ChevronUp } from 'lucide-react';
import { useViewerOptional } from '@nekazari/sdk';

declare const Cesium: any;

const CLC_CATEGORIES = [
    { group: 'Artificial surfaces', color: '#E6004D', items: [
        { code: '111', name: 'Continuous urban fabric' },
        { code: '112', name: 'Discontinuous urban fabric' },
        { code: '121', name: 'Industrial or commercial units' },
        { code: '122', name: 'Road and rail networks' },
        { code: '123', name: 'Port areas' },
        { code: '124', name: 'Airports' },
        { code: '131', name: 'Mineral extraction sites' },
        { code: '132', name: 'Dump sites' },
        { code: '133', name: 'Construction sites' },
        { code: '141', name: 'Green urban areas' },
        { code: '142', name: 'Sport and leisure facilities' },
    ]},
    { group: 'Agricultural areas', color: '#FFA800', items: [
        { code: '211', name: 'Non-irrigated arable land' },
        { code: '212', name: 'Permanently irrigated land' },
        { code: '213', name: 'Rice fields' },
        { code: '221', name: 'Vineyards' },
        { code: '222', name: 'Fruit trees and berry plantations' },
        { code: '223', name: 'Olive groves' },
        { code: '231', name: 'Pastures' },
        { code: '241', name: 'Annual crops associated with permanent crops' },
        { code: '242', name: 'Complex cultivation patterns' },
        { code: '243', name: 'Land principally occupied by agriculture with natural vegetation' },
        { code: '244', name: 'Agro-forestry areas' },
    ]},
    { group: 'Forests & semi-natural', color: '#80CC00', items: [
        { code: '311', name: 'Broad-leaved forest' },
        { code: '312', name: 'Coniferous forest' },
        { code: '313', name: 'Mixed forest' },
        { code: '321', name: 'Natural grasslands' },
        { code: '322', name: 'Moors and heathland' },
        { code: '323', name: 'Sclerophyllous vegetation' },
        { code: '324', name: 'Transitional woodland-shrub' },
        { code: '331', name: 'Beaches, dunes, sand plains' },
        { code: '332', name: 'Bare rocks' },
        { code: '333', name: 'Sparsely vegetated areas' },
        { code: '334', name: 'Burnt areas' },
        { code: '335', name: 'Glaciers and perpetual snow' },
    ]},
    { group: 'Wetlands', color: '#CC4DFF', items: [
        { code: '411', name: 'Inland marshes' },
        { code: '412', name: 'Peat bogs' },
        { code: '421', name: 'Salt marshes' },
        { code: '422', name: 'Salines' },
        { code: '423', name: 'Intertidal flats' },
    ]},
    { group: 'Water bodies', color: '#00AEEF', items: [
        { code: '511', name: 'Water courses' },
        { code: '512', name: 'Water bodies' },
        { code: '521', name: 'Coastal lagoons' },
        { code: '522', name: 'Estuaries' },
        { code: '523', name: 'Sea and ocean' },
    ]},
];

export const CorineLandCoverToggle: React.FC = () => {
    const viewerContext = useViewerOptional();
    const viewer = viewerContext?.cesiumViewer;

    useEffect(() => {
        console.log('[CorineToggle] Component mounted, viewer present:', !!viewer);
    }, [viewer]);

    const [enabled, setEnabled] = useState(false);
    const [opacity, setOpacity] = useState(0.6);
    const [showLegend, setShowLegend] = useState(false);
    const [expandedGroup, setExpandedGroup] = useState<string | null>(null);

    useEffect(() => {
        console.log('[CorineToggle] Dispatching toggle event:', { enabled, opacity });
        window.dispatchEvent(new CustomEvent('nkz.clc.toggle', { 
            detail: { enabled, opacity } 
        }));
    }, [enabled, opacity]);

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
                <>
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

                    {/* Legend */}
                    <div className="pl-5 mt-1">
                        <button
                            onClick={() => setShowLegend(!showLegend)}
                            className="flex items-center gap-1 text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
                        >
                            {showLegend ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                            {showLegend ? 'Hide legend' : 'Show legend'}
                        </button>

                        {showLegend && (
                            <div className="mt-1 space-y-0.5 max-h-48 overflow-y-auto pr-1 custom-scrollbar">
                                {CLC_CATEGORIES.map(cat => (
                                    <div key={cat.group}>
                                        <button
                                            onClick={() => setExpandedGroup(expandedGroup === cat.group ? null : cat.group)}
                                            className="flex items-center gap-1.5 w-full text-[10px] text-gray-400 hover:text-gray-200 py-0.5"
                                        >
                                            <span
                                                className="w-2 h-2 rounded-sm shrink-0"
                                                style={{ backgroundColor: cat.color }}
                                            />
                                            <span className="truncate">{cat.group}</span>
                                            {expandedGroup === cat.group ? <ChevronUp className="w-2.5 h-2.5 ml-auto" /> : <ChevronDown className="w-2.5 h-2.5 ml-auto" />}
                                        </button>
                                        {expandedGroup === cat.group && (
                                            <div className="ml-3 space-y-0.5 py-0.5">
                                                {cat.items.map(item => (
                                                    <div key={item.code} className="flex items-center gap-1.5 text-[9px] text-gray-500">
                                                        <span
                                                            className="w-2 h-2 rounded-sm shrink-0"
                                                            style={{ backgroundColor: cat.color }}
                                                        />
                                                        <span className="font-mono text-gray-600 w-6">{item.code}</span>
                                                        <span className="truncate">{item.name}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                ))}
                                <div className="text-[9px] text-gray-600 pt-1">
                                    © EEA — CORINE Land Cover 2018
                                </div>
                            </div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
};
