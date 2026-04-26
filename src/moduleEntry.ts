import { moduleSlots } from './slots/index';
import MainView from './components/MainView';
import pkg from '../package.json';
import { i18n } from '@nekazari/sdk';
import enTranslations from './locales/en.json';
import esTranslations from './locales/es.json';

// Use strict module ID that matches database
// This should match the ID in manifest.json
const MODULE_ID = 'nkz-module-eu-elevation';
const BUNDLE_VERSION = '1.0.0-audit-' + Date.now();

console.warn(`[${MODULE_ID}] 🔥 BUNDLE STARTING - VERSION: ${BUNDLE_VERSION}`);

declare global {
    interface Window {
        __NKZ__: any;
    }
}

// Self-register with the host runtime
try {
    if (window.__NKZ__) {
        console.log(`[${MODULE_ID}] 🚀 Found window.__NKZ__, registering components...`);
        // Register module translations
        if (i18n && i18n.addResourceBundle) {
            i18n.addResourceBundle('en', 'eu-elevation', enTranslations, true, true);
            i18n.addResourceBundle('es', 'eu-elevation', esTranslations, true, true);
        }

        window.__NKZ__.register({
            id: MODULE_ID,
            viewerSlots: moduleSlots,
            main: MainView,
            version: pkg.version,
        });
        console.log(`[${MODULE_ID}] ✅ Registration call complete`);
    } else {
        console.error(`[${MODULE_ID}] ❌ window.__NKZ__ not found! Module registration failed.`);
    }
} catch (e) {
    console.error(`[${MODULE_ID}] 💥 Fatal error during registration:`, e);
}
