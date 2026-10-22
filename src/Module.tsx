import { defineModule } from '@nekazari/module-kit';
import { lazy } from 'react';
import './i18n';
import { moduleSlots } from './slots';
import pkg from '../package.json';

const MainPage = lazy(() => import('./components/MainView'));

export default defineModule({
  id: 'nkz-module-eu-elevation',
  displayName: 'EU Elevation',
  version: pkg.version,
  hostApiVersion: '^2.0.0',
  description: 'EU terrain, elevation and CORINE Land Cover layers — Nekazari Platform Module',
  accent: { base: '#3B82F6', soft: '#DBEAFE', strong: '#1D4ED8' },
  icon: 'mountain',
  main: MainPage,
  api: { basePath: '/api/eu-elevation' },
  slots: moduleSlots as never,
});
