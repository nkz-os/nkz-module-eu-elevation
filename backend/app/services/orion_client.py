"""
Orion-LD Integration Service for EU Elevation Module.

Manages NGSI-LD entities for geospatial layers.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class OrionLDClient:
    """
    Client for Orion-LD Context Broker operations.
    
    Handles creation, update, and deletion of geospatial entities.
    """
    
    CONTEXT = [
        "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld",
        "https://smartdatamodels.org/context.jsonld"
    ]
    
    def __init__(self, base_url: str = None, tenant_id: str = None):
        self.base_url = base_url or settings.ORION_URL
        self.tenant_id = tenant_id
        self.headers = {
            "Content-Type": "application/ld+json",
            "Accept": "application/ld+json"
        }
        if tenant_id:
            self.headers["NGSILD-Tenant"] = tenant_id
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[Dict] = None
    ) -> Optional[Dict]:
        """Make HTTP request to Orion-LD."""
        url = f"{self.base_url}{endpoint}"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    json=json_data,
                    headers=self.headers
                )
                
                if response.status_code in (200, 201, 204):
                    if response.content:
                        return response.json()
                    return None
                    
                logger.warning(f"Orion-LD request failed: {response.status_code} {response.text}")
                return None
                
            except Exception as e:
                logger.error(f"Orion-LD request error: {e}")
                raise
    
    # =========================================================================
    # PointCloudLayer Entity
    # =========================================================================
    
    async def create_point_cloud_layer(
        self,
        layer_id: str,
        parcel_id: str,
        tileset_url: str,
        source: str = "PNOA",
        date_observed: Optional[datetime] = None,
        point_count: Optional[int] = None,
        bounds: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
        """
        Create a PointCloudLayer entity in Orion-LD.
        
        Args:
            layer_id: Unique layer identifier
            parcel_id: Reference to AgriParcel entity
            tileset_url: URL to 3D Tiles tileset.json
            source: Data source (PNOA, IDENA, user_upload)
            date_observed: Observation/flight date
            point_count: Number of points in cloud
            bounds: Bounding box {minX, minY, minZ, maxX, maxY, maxZ}
        """
        entity_id = f"urn:ngsi-ld:PointCloudLayer:{layer_id}"
        
        entity = {
            "@context": self.CONTEXT,
            "id": entity_id,
            "type": "PointCloudLayer",
            "refAgriParcel": {
                "type": "Relationship",
                "object": parcel_id if parcel_id.startswith("urn:") else f"urn:ngsi-ld:AgriParcel:{parcel_id}"
            },
            "tilesetUrl": {
                "type": "Property",
                "value": tileset_url
            },
            "source": {
                "type": "Property",
                "value": source
            },
            "dateObserved": {
                "type": "Property",
                "value": (date_observed or datetime.utcnow()).isoformat() + "Z"
            },
            "pipelineStatus": {
                "type": "Property",
                "value": "COMPLETED"
            }
        }
        
        if point_count:
            entity["pointCount"] = {
                "type": "Property",
                "value": point_count
            }
        
        if bounds:
            entity["boundingBox"] = {
                "type": "Property",
                "value": bounds
            }
        
        await self._request("POST", "/ngsi-ld/v1/entities", entity)
        logger.info(f"Created PointCloudLayer entity: {entity_id}")
        
        return entity
    
    async def update_point_cloud_layer(
        self,
        layer_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Update a PointCloudLayer entity."""
        entity_id = f"urn:ngsi-ld:PointCloudLayer:{layer_id}"
        
        patch_data = {"@context": self.CONTEXT}
        
        for key, value in updates.items():
            patch_data[key] = {"type": "Property", "value": value}
        
        await self._request(
            "PATCH",
            f"/ngsi-ld/v1/entities/{entity_id}/attrs",
            patch_data
        )
        return True
    
    async def delete_point_cloud_layer(self, layer_id: str) -> bool:
        """Delete a PointCloudLayer entity."""
        entity_id = f"urn:ngsi-ld:PointCloudLayer:{layer_id}"
        await self._request("DELETE", f"/ngsi-ld/v1/entities/{entity_id}")
        logger.info(f"Deleted PointCloudLayer entity: {entity_id}")
        return True
    
    # =========================================================================
    # AgriTree Entity
    # =========================================================================
    
    async def create_tree(
        self,
        tree_id: str,
        parcel_id: str,
        location: Dict[str, Any],  # GeoJSON Point
        height: float,
        crown_diameter: float,
        crown_area: float,
        ndvi_mean: Optional[float] = None,
        species: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create an AgriTree entity in Orion-LD.
        
        Args:
            tree_id: Unique tree identifier
            parcel_id: Reference to AgriParcel
            location: GeoJSON Point with coordinates
            height: Tree height in meters
            crown_diameter: Crown diameter in meters
            crown_area: Crown area in m²
            ndvi_mean: Mean NDVI value for tree crown
            species: Tree species (if known)
        """
        entity_id = f"urn:ngsi-ld:AgriTree:{tree_id}"
        
        entity = {
            "@context": self.CONTEXT,
            "id": entity_id,
            "type": "AgriTree",
            "refAgriParcel": {
                "type": "Relationship",
                "object": parcel_id if parcel_id.startswith("urn:") else f"urn:ngsi-ld:AgriParcel:{parcel_id}"
            },
            "location": {
                "type": "GeoProperty",
                "value": location
            },
            "height": {
                "type": "Property",
                "value": height,
                "unitCode": "MTR"
            },
            "crownDiameter": {
                "type": "Property",
                "value": crown_diameter,
                "unitCode": "MTR"
            },
            "crownArea": {
                "type": "Property",
                "value": crown_area,
                "unitCode": "MTK"  # square meters
            }
        }
        
        if ndvi_mean is not None:
            entity["ndviMean"] = {
                "type": "Property",
                "value": ndvi_mean
            }
        
        if species:
            entity["species"] = {
                "type": "Property",
                "value": species
            }
        
        await self._request("POST", "/ngsi-ld/v1/entities", entity)
        logger.info(f"Created AgriTree entity: {entity_id}")
        
        return entity
    
    async def create_trees_batch(
        self,
        parcel_id: str,
        trees: List[Dict[str, Any]]
    ) -> int:
        """
        Create multiple tree entities in batch.
        
        Args:
            parcel_id: Reference to AgriParcel
            trees: List of tree data dicts with location, height, etc.
        
        Returns:
            Number of trees created
        """
        count = 0
        for tree_data in trees:
            try:
                await self.create_tree(
                    tree_id=tree_data.get("id", f"{parcel_id}_{count}"),
                    parcel_id=parcel_id,
                    location=tree_data["location"],
                    height=tree_data["height"],
                    crown_diameter=tree_data.get("crown_diameter", 0),
                    crown_area=tree_data.get("crown_area", 0),
                    ndvi_mean=tree_data.get("ndvi_mean")
                )
                count += 1
            except Exception as e:
                logger.warning(f"Failed to create tree entity: {e}")
        
        logger.info(f"Created {count} tree entities for parcel {parcel_id}")
        return count
    
    async def delete_parcel_trees(self, parcel_id: str) -> int:
        """Delete all tree entities for a parcel."""
        # Query trees by parcel reference
        parcel_urn = parcel_id if parcel_id.startswith("urn:") else f"urn:ngsi-ld:AgriParcel:{parcel_id}"
        
        query_result = await self._request(
            "GET",
            f"/ngsi-ld/v1/entities?type=AgriTree&q=refAgriParcel=={parcel_urn}&limit=1000"
        )
        
        if not query_result:
            return 0
        
        count = 0
        for entity in query_result:
            try:
                await self._request("DELETE", f"/ngsi-ld/v1/entities/{entity['id']}")
                count += 1
            except Exception as e:
                logger.warning(f"Failed to delete tree {entity['id']}: {e}")
        
        logger.info(f"Deleted {count} tree entities for parcel {parcel_id}")
        return count
    
    # =========================================================================
    # Query Methods
    # =========================================================================
    
    async def get_parcel_layers(self, parcel_id: str) -> List[Dict]:
        """Get all PointCloudLayer entities for a parcel."""
        parcel_urn = parcel_id if parcel_id.startswith("urn:") else f"urn:ngsi-ld:AgriParcel:{parcel_id}"
        
        result = await self._request(
            "GET",
            f"/ngsi-ld/v1/entities?type=PointCloudLayer&q=refAgriParcel=={parcel_urn}"
        )
        
        return result or []
    
    async def get_parcel_trees(self, parcel_id: str) -> List[Dict]:
        """Get all AgriTree entities for a parcel."""
        parcel_urn = parcel_id if parcel_id.startswith("urn:") else f"urn:ngsi-ld:AgriParcel:{parcel_id}"
        
        result = await self._request(
            "GET",
            f"/ngsi-ld/v1/entities?type=AgriTree&q=refAgriParcel=={parcel_urn}&limit=1000"
        )
        
        return result or []


# Helper function for sync contexts
def get_orion_client(tenant_id: str = None) -> OrionLDClient:
    """Get an OrionLDClient instance."""
    return OrionLDClient(tenant_id=tenant_id)
