import React from 'react';
import { ElevationAdminControl } from '../components/slots/ElevationAdminControl';
import { ElevationLayer } from '../components/slots/ElevationLayer';
import { CorineLandCoverToggle } from '../components/slots/CorineLandCoverToggle';

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
 * - dashboard-widget: Full terrain configuration panel (providers, BYOK, ingestion)
 * - context-panel: Empty (avoid duplication with dashboard-widget)
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

  // 3. Full terrain configuration panel (providers, BYOK, ingestion)
  'dashboard-widget': [
    {
      id: 'elevation-admin-control',
      moduleId: MODULE_ID,
      component: 'ElevationAdminControl',
      priority: 50,
      localComponent: ElevationAdminControl
    }
  ],

  // Unused — avoids duplication with dashboard-widget
  'context-panel': [],
  'bottom-panel': [],
  'entity-tree': []
};

export default moduleSlots;
