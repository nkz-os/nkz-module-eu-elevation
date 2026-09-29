import React, { useEffect, useRef } from 'react';
import { useViewerOptional } from '@nekazari/sdk';
import { useClcLayer } from '../../services/clcLayerStore';

// EEA ArcGIS REST endpoint is more robust than WMS for CORINE in Cesium 1.100+
const CLC_REST_URL = 'https://image.discomap.eea.europa.eu/arcgis/rest/services/Corine/CLC2018_WM/MapServer';
const CLC_CREDIT = '© EEA Copernicus Land Monitoring Service — CORINE Land Cover 2018';

const alive = (v: any) => !!v && !(typeof v.isDestroyed === 'function' && v.isDestroyed());

async function createClcProvider(C: any): Promise<any> {
  const options = { credit: new C.Credit(CLC_CREDIT) };
  if (typeof C.ArcGisMapServerImageryProvider.fromUrl === 'function') {
    return C.ArcGisMapServerImageryProvider.fromUrl(CLC_REST_URL, options);
  }
  return new C.ArcGisMapServerImageryProvider({ url: CLC_REST_URL, ...options });
}

/** CORINE Land Cover imagery overlay driven by the CLC layer store. Never shows UI. */
export const CorineLayer: React.FC = () => {
  const viewer = useViewerOptional()?.cesiumViewer;
  const [{ enabled, opacity }] = useClcLayer();
  const layerRef = useRef<any>(null);
  const opacityRef = useRef(opacity);
  opacityRef.current = opacity;

  // Add the imagery layer while enabled; remove it when disabled or unmounted.
  useEffect(() => {
    const C = (window as any).Cesium;
    if (!alive(viewer) || !C || !enabled) return;

    let cancelled = false;
    createClcProvider(C)
      .then((provider) => {
        if (cancelled || !alive(viewer)) return;
        const layer = viewer.imageryLayers.addImageryProvider(provider);
        layer.alpha = opacityRef.current;
        viewer.imageryLayers.raiseToTop(layer);
        layerRef.current = layer;
      })
      .catch((error: unknown) => {
        console.error('[CorineLayer] Failed to add CORINE Land Cover layer:', error);
      });

    return () => {
      cancelled = true;
      if (layerRef.current && alive(viewer)) {
        viewer.imageryLayers.remove(layerRef.current, true);
      }
      layerRef.current = null;
    };
  }, [viewer, enabled]);

  useEffect(() => {
    if (layerRef.current) layerRef.current.alpha = opacity;
  }, [opacity]);

  return null;
};

export default CorineLayer;
