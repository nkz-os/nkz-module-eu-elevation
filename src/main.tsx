/**
 * Main entry point for LiDAR Module
 * 
 * For development/standalone mode, renders the app to #root.
 * In production (Module Federation), the app is loaded via remoteEntry.js.
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import LidarApp from './App';
import './index.css';

// Check if we're running standalone (not as federated module)
const rootElement = document.getElementById('root');

if (rootElement) {
    ReactDOM.createRoot(rootElement).render(
        <React.StrictMode>
            <LidarApp />
        </React.StrictMode>
    );
}
