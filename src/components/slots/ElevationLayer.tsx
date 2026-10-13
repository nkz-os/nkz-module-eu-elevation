import React, { useEffect, useRef } from 'react';
import { useViewerOptional } from '@nekazari/sdk';
import { API_ORIGIN } from '../../utils/apiOrigin';
import { createTerrainProvider } from '../../utils/terrainFactory';

declare const Cesium: any;

export interface HostRegion {
  currentRegion: 'navarra' | 'spain' | 'eu' | 'world';
  layerAutoMode: boolean;
}

export function shouldInjectEuTerrain(region: HostRegion | null): boolean {
  if (!region) return false;
  if (!region.layerAutoMode) return false;
  return region.currentRegion === 'eu' || region.currentRegion === 'world';
}

function getDefaultCopernicusUrl(): string {
  const w = window as any;
  if (w.__ENV__?.EU_ELEVATION_COPERNICUS_URL) {
    return w.__ENV__.EU_ELEVATION_COPERNICUS_URL;
  }
  return `${API_ORIGIN}/api/elevation/terrain/EU/layer.json`;
}

/** Auto-inject Copernicus terrain when region is EU/world. Never shows UI. */
export const ElevationLayer: React.FC = () => {
  const viewerContext = useViewerOptional();
  const viewer = viewerContext?.cesiumViewer;
  const activeRef = useRef(false);
  const lastAppliedRef = useRef('');

  useEffect(() => {
    // A destroyed Cesium viewer is still a truthy object; touching .scene /
    // .camera then throws "_cesiumWidget is undefined". Guard on isDestroyed().
    const alive = (v: any) => !!v && !(typeof v.isDestroyed === 'function' && v.isDestroyed());
    if (!alive(viewer) || !viewer.scene) return;

    const inject = () => {
      if (!alive(viewer)) return;
      const region = (viewer as any).__nkzRegion as HostRegion | undefined;
      if (!shouldInjectEuTerrain(region ?? null)) {
        if (activeRef.current) {
          // Region is Navarra/Spain — let host manage terrain (IGN/IDENA).
          // Don't reset to Ellipsoid; host's useTerrainProvider handles it.
          activeRef.current = false;
          lastAppliedRef.current = '';
        }
        return;
      }

      if (lastAppliedRef.current === 'europe_copernicus') return;

      const provider = createTerrainProvider({
        type: 'europe_copernicus',
        europeCopernicusUrl: getDefaultCopernicusUrl(),
      });

      if (provider && typeof provider.then === 'function') {
        provider.then((resolved: any) => {
          if (!alive(viewer)) return;
          viewer.terrainProvider = resolved;
          activeRef.current = true;
          lastAppliedRef.current = 'europe_copernicus';
        }).catch(() => {});
      } else if (alive(viewer)) {
        viewer.terrainProvider = provider;
        activeRef.current = true;
        lastAppliedRef.current = 'europe_copernicus';
      }
    };

    viewer.camera.moveEnd.addEventListener(inject);
    // Initial evaluation for camera already over EU at mount
    inject();

    return () => {
      if (alive(viewer)) viewer.camera.moveEnd.removeEventListener(inject);
    };
  }, [viewer]);

  return null; // never renders UI
};

export default ElevationLayer;
