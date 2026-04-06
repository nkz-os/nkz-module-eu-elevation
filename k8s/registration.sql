-- =============================================================================
-- EU Elevation Module Registration for Nekazari Platform
-- =============================================================================
-- Register the EU Elevation module in the platform database
-- Execute: kubectl exec -it -n nekazari $(kubectl get pods -n nekazari -l app=postgresql -o jsonpath='{.items[0].metadata.name}') -- psql -U nekazari -d nekazari -f -
-- =============================================================================

-- Register in marketplace_modules
INSERT INTO marketplace_modules (
    id,
    name,
    display_name,
    description,
    remote_entry_url,
    scope,
    exposed_module,
    version,
    author,
    category,
    icon_url,
    route_path,
    label,
    module_type,
    required_plan_type,
    pricing_tier,
    is_active,
    required_roles,
    metadata
) VALUES (
    'nkz-module-eu-elevation',
    'nkz-module-eu-elevation',
    'EU Elevation Module',
    'Premium module for 3D terrain and elevation data across the European Union and the United Kingdom. Includes Quantized Mesh generation, CORINE Land Cover overlay, and multi-tenant terrain source management.',
    '/modules/nkz-module-eu-elevation/nkz-module.js',
    'nkz_module_eu_elevation',
    './moduleEntry',
    '1.0.0',
    'Nekazari Team',
    'analytics',
    NULL,
    '/eu-elevation',
    'EU Elevation',
    'ADDON_PAID',
    'premium',
    'PAID',
    true,
    ARRAY['Farmer', 'TenantAdmin', 'PlatformAdmin'],
    '{
        "icon": "🏔️",
        "color": "#3B82F6",
        "shortDescription": "Pan-European 3D Elevation Models",
        "features": [
            "Quantized Mesh Processing",
            "WCS/GeoTIFF Ingestion",
            "CORINE Land Cover Overlay",
            "Geometry Decimation",
            "Multi-tenant Terrain Sources"
        ],
        "slots": {
            "map-layer": [
                {"id": "elevation-cesium-layer", "component": "ElevationLayer", "priority": 10}
            ],
            "layer-toggle": [
                {"id": "clc-layer-toggle", "component": "ElevationAdminControl", "priority": 30}
            ],
            "dashboard-widget": [
                {"id": "elevation-admin-control", "component": "ElevationAdminControl", "priority": 50}
            ],
            "context-panel": [
                {"id": "elevation-context-control", "component": "ElevationAdminControl", "priority": 50}
            ]
        },
        "navigationItems": [
            {
                "path": "/eu-elevation",
                "label": "EU Elevation",
                "icon": "mountain"
            }
        ],
        "backend_only": false,
        "backend_url": "http://elevation-api-service:80"
    }'::jsonb
) ON CONFLICT (id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    version = EXCLUDED.version,
    remote_entry_url = EXCLUDED.remote_entry_url,
    scope = EXCLUDED.scope,
    exposed_module = EXCLUDED.exposed_module,
    route_path = EXCLUDED.route_path,
    label = EXCLUDED.label,
    module_type = EXCLUDED.module_type,
    pricing_tier = EXCLUDED.pricing_tier,
    is_active = EXCLUDED.is_active,
    metadata = EXCLUDED.metadata,
    updated_at = NOW();

-- Verify registration
SELECT id, name, display_name, version, is_active, route_path, module_type 
FROM marketplace_modules 
WHERE id = 'nkz-module-eu-elevation';
