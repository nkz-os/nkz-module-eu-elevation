import React from 'react';
import { ElevationAdminControl } from '../components/slots/ElevationAdminControl';
import { ElevationLayer } from '../components/slots/ElevationLayer';
import { CorineLandCoverToggle } from '../components/slots/CorineLandCoverToggle';
import { CorineLayer } from '../components/slots/CorineLayer';

const MODULE_ID = 'nkz-module-eu-elevation';

export type SlotType = 'layer-toggle' | 'context-panel' | 'bottom-panel' | 'entity-tree' | 'map-layer' | 'dashboard-widget';

export interface SlotWidgetDefinition {
  id: string;
  moduleId: string;
  component: string;
  priority: number;
  localComponent: React.ComponentType<any>;
  defaultProps?: Record<string, any>;
  showWhen?: {
    entityType?: string[];
    layerActive?: string[];
  };
}

export type ModuleViewerSlots = Record<SlotType, SlotWidgetDefinition[]> & {
  moduleProvider?: React.ComponentType<{ children: React.ReactNode }>;
};

/**
 * Elevation Module Slots Configuration
 *
 * Slot allocation:
 * - map-layer: Injects terrain provider into Cesium (invisible to user)
 * - layer-toggle: Simple CORINE Land Cover toggle with opacity slider
 * - context-panel: Full terrain configuration panel (providers, BYOK, ingestion)
 * - dashboard-widget: Empty (consolidated into module page and context-panel)
 */
export const moduleSlots: ModuleViewerSlots = {
  // 1. Inject the Terrain Provider into the Cesium map
  'map-layer': [
    {
      id: 'elevation-cesium-layer',
      moduleId: MODULE_ID,
      component: 'ElevationLayer',
      priority: 10,
      localComponent: ElevationLayer
    },
    {
      id: 'clc-imagery-layer',
      moduleId: MODULE_ID,
      component: 'CorineLayer',
      priority: 20,
      localComponent: CorineLayer
    }
  ],

  // 2. Simple CORINE Land Cover toggle with opacity slider
  'layer-toggle': [
    {
      id: 'clc-layer-toggle',
      moduleId: MODULE_ID,
      component: 'CorineLandCoverToggle',
      priority: 30,
      localComponent: CorineLandCoverToggle
    }
  ],

  // 3. Admin configuration — module page only, NEVER in viewer context-panel
  'context-panel': [
    {
      id: 'elevation-context-control',
      moduleId: MODULE_ID,
      component: 'ElevationAdminControl',
      priority: 50,
      localComponent: ElevationAdminControl,
      showWhen: { entityType: [] }, // empty → never matches, module page only
    }
  ],

  // Consistently removed from dashboard to avoid fragmentation
  'dashboard-widget': [],
  'bottom-panel': [],
  'entity-tree': []
};

export default moduleSlots;
