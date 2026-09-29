import React, { useState } from 'react';
import { Layers, ChevronDown, ChevronUp } from 'lucide-react';
import { SlotShellCompact } from '@nekazari/viewer-kit';
import { Toggle, Slider } from '@nekazari/ui-kit';
import { useClcLayer } from '../../services/clcLayerStore';

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

const elevationAccent = { base: '#64748B', soft: '#F1F5F9', strong: '#475569' };

export const CorineLandCoverToggle: React.FC = () => {
    const [{ enabled, opacity }, setClc] = useClcLayer();
    const setEnabled = (next: boolean) => setClc({ enabled: next });
    const setOpacity = (next: number) => setClc({ opacity: next });
    const [showLegend, setShowLegend] = useState(false);
    const [expandedGroup, setExpandedGroup] = useState<string | null>(null);

    return (
        <SlotShellCompact moduleId="nkz-module-eu-elevation" accent={elevationAccent}>
            <div className="flex flex-col gap-nkz-tight">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-nkz-inline min-w-0">
                        <Layers className="w-3.5 h-3.5 text-nkz-accent-base shrink-0" />
                        <span className="text-nkz-xs font-medium text-nkz-text-primary truncate">CORINE Land Cover</span>
                    </div>
                    <Toggle
                        checked={enabled}
                        onChange={() => setEnabled(!enabled)}
                        label=""
                        size="sm"
                    />
                </div>
                {enabled && (
                    <>
                        <Slider
                            value={Math.round(opacity * 100)}
                            onChange={(v) => setOpacity(v / 100)}
                            min={0}
                            max={100}
                            step={1}
                            label=""
                            unit="%"
                        />

                        {/* Legend */}
                        <div>
                            <button
                                onClick={() => setShowLegend(!showLegend)}
                                className="flex items-center gap-1 text-nkz-xs text-nkz-text-muted hover:text-nkz-text-primary transition-colors"
                            >
                                {showLegend ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                                {showLegend ? 'Hide legend' : 'Show legend'}
                            </button>

                            {showLegend && (
                                <div className="mt-1 space-y-0.5 max-h-48 overflow-y-auto pr-1">
                                    {CLC_CATEGORIES.map(cat => (
                                        <div key={cat.group}>
                                            <button
                                                onClick={() => setExpandedGroup(expandedGroup === cat.group ? null : cat.group)}
                                                className="flex items-center gap-1.5 w-full text-nkz-xs text-nkz-text-muted hover:text-nkz-text-primary py-0.5"
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
                                                        <div key={item.code} className="flex items-center gap-1.5 text-nkz-xs text-nkz-text-muted">
                                                            <span
                                                                className="w-2 h-2 rounded-sm shrink-0"
                                                                style={{ backgroundColor: cat.color }}
                                                            />
                                                            <span className="font-mono text-nkz-text-disabled w-6">{item.code}</span>
                                                            <span className="truncate">{item.name}</span>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                    <div className="text-nkz-xs text-nkz-text-disabled pt-1">
                                        © EEA — CORINE Land Cover 2018
                                    </div>
                                </div>
                            )}
                        </div>
                    </>
                )}
            </div>
        </SlotShellCompact>
    );
};
