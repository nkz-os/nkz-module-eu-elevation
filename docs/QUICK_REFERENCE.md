# Quick Reference Guide

Quick reference for common tasks when developing Nekazari modules.

## SDK Quick Reference

### API Client

```typescript
import { NKZClient, useAuth } from '@nekazari/sdk';

const { getToken, tenantId } = useAuth();
const client = new NKZClient({
  baseUrl: '/api',
  getToken: getToken,
  getTenantId: () => tenantId,
});

// Common operations
const entities = await client.get('/entities');
const parcels = await client.get('/parcels');
const result = await client.post('/entities', { type: 'Sensor', name: 'My Sensor' });
```

### Authentication

```typescript
import { useAuth } from '@nekazari/sdk';

const { user, token, tenantId, isAuthenticated, hasRole } = useAuth();

if (!isAuthenticated) return <div>Please log in</div>;
if (hasRole('PlatformAdmin')) { /* admin content */ }
```

### Internationalization

```typescript
import { useTranslation } from '@nekazari/sdk';

const { t, i18n } = useTranslation('common');
return <h1>{t('welcome')}</h1>;
```

## UI Components

```typescript
import { Button, Card, Input } from '@nekazari/ui-kit';

<Button variant="primary" size="md" onClick={handleClick}>
  Click Me
</Button>

<Card padding="lg">
  <h2>Card Title</h2>
  <p>Card content</p>
</Card>
```

## Common API Endpoints

- `GET /api/entities` - List entities
- `GET /api/entities/{id}` - Get entity
- `POST /api/entities` - Create entity
- `GET /api/parcels` - List parcels
- `GET /api/sensors` - List sensors

For complete API documentation: [API Integration Guide](https://github.com/nkz-os/nekazari-public/blob/main/docs/api/README.md)

## Manifest.json Required Fields

```json
{
  "id": "my-module",
  "name": "my-module",
  "display_name": "My Module",
  "version": "1.0.0",
  "description": "Module description",
  "author": {
    "name": "Your Name",
    "email": "your.email@example.com"
  },
  "module_type": "ADDON_FREE",
  "route_path": "/my-module",
  "build_config": {
    "type": "remote",
    "remote_entry_url": "/modules/my-module/assets/remoteEntry.js",
    "scope": "my_module_scope",
    "exposed_module": "./App"
  }
}
```

## Build & Package

```bash
# Build
npm run build

# Package
zip -r my-module-v1.0.0.zip \
  manifest.json \
  package.json \
  vite.config.ts \
  tsconfig.json \
  tailwind.config.js \
  postcss.config.js \
  src/ \
  assets/ \
  dist/
```

## Links

- [Complete Developer Guide](https://github.com/nkz-os/nekazari-public/blob/main/docs/development/EXTERNAL_DEVELOPER_GUIDE.md)
- [API Documentation](https://github.com/nkz-os/nekazari-public/blob/main/docs/api/README.md)
- [SDK NPM Package](https://www.npmjs.com/package/@nekazari/sdk)
- [UI-Kit NPM Package](https://www.npmjs.com/package/@nekazari/ui-kit)

