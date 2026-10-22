import React from 'react';
import { Layers } from 'lucide-react';
import './index.css';

// Export slots for Module Federation
export { moduleSlots, default as viewerSlots } from './slots';

const ElevationApp: React.FC = () => {
  return (
    <div className="bg-gradient-to-br from-slate-900 to-slate-800 text-white p-6 md:p-10 min-h-screen">
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-12">
          <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-blue-900 text-blue-200 text-sm font-medium mb-6 border border-blue-800">
            EU Elevation Pipeline
          </div>
          <div className="flex items-center justify-center gap-4 mb-6">
            <div className="p-4 rounded-2xl bg-gradient-to-br from-blue-600 to-cyan-600 shadow-lg">
              <Layers className="w-10 h-10 text-white" />
            </div>
          </div>
          <h1 className="text-4xl md:text-5xl font-bold mb-4">
            3D Terrain Ingestion
          </h1>
          <p className="text-xl text-slate-400 max-w-2xl mx-auto">
            Process high-resolution elevation data (DTM/DSM) from EU WCS endpoints into Cesium Quantized Mesh format.
          </p>
        </div>
      </div>
    </div>
  );
};

export default ElevationApp;
