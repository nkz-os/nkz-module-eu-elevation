import React from 'react';
import { ElevationAdminControl } from '../components/slots/ElevationAdminControl';
import { ElevationLayer } from '../components/slots/ElevationLayer';

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
 */
export const moduleSlots: ModuleViewerSlots = {
  // 1. Inject the Terrain Provider + CLC WMS layer into the Cesium map
  'map-layer': [
    {
      id: 'elevation-cesium-layer',
      moduleId: MODULE_ID,
      component: 'ElevationLayer',
      priority: 10,
      localComponent: ElevationLayer
    }
  ],

  // 2. Layer toggle for CORINE Land Cover in the layers panel
  'layer-toggle': [
    {
      id: 'clc-layer-toggle',
      moduleId: MODULE_ID,
      component: 'ElevationAdminControl',
      priority: 30,
      localComponent: ElevationAdminControl
    }
  ],

  // 3. Add the Admin Panel to the dashboard
  'dashboard-widget': [
    {
      id: 'elevation-admin-control',
      moduleId: MODULE_ID,
      component: 'ElevationAdminControl',
      priority: 50,
      localComponent: ElevationAdminControl
    }
  ],

  // 4. Add to Context Panel for Unified Viewer access
  'context-panel': [
    {
      id: 'elevation-context-control',
      moduleId: MODULE_ID,
      component: 'ElevationAdminControl',
      priority: 50,
      localComponent: ElevationAdminControl
    }
  ],

  // Unused slots
  'bottom-panel': [],
  'entity-tree': []
};

// Export as default for convenience
export default moduleSlots;
