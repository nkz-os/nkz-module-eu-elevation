"""
Authentication middleware for FastAPI.
Validates JWT tokens using Keycloak JWKS with strict issuer verification.
"""

import logging
from typing import Optional
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient
import os

logger = logging.getLogger(__name__)

# JWT configuration
JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'RS256')
JWT_ISSUER = os.getenv('JWT_ISSUER', '')
JWKS_URL = os.getenv('JWKS_URL', '')

if not JWT_ISSUER:
    logger.warning("JWT_ISSUER not set — token validation will reject all tokens. "
                   "Set JWT_ISSUER to your Keycloak realm URL.")

if not JWKS_URL and JWT_ISSUER:
    JWKS_URL = f'{JWT_ISSUER}/protocol/openid-connect/certs'

# Cache for JWKS
_jwks_client: Optional[PyJWKClient] = None

security = HTTPBearer()


def get_jwks_client() -> PyJWKClient:
    """Get or create JWKS client."""
    global _jwks_client
    if _jwks_client is None:
        if not JWKS_URL:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JWKS_URL not configured"
            )
        _jwks_client = PyJWKClient(JWKS_URL)
    return _jwks_client


async def verify_token(token: str) -> dict:
    """Verify JWT token and return payload. Strict issuer validation."""
    try:
        if not JWT_ISSUER:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JWT_ISSUER not configured"
            )

        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"verify_exp": True, "verify_iss": True}
        )

        return payload

    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.InvalidIssuerError:
        logger.warning("Token issuer mismatch — rejecting token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token issuer"
        )
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying token: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token verification failed"
        )


def get_tenant_id(request: Request) -> str:
    """Extract tenant ID from request headers."""
    tenant_id = request.headers.get('X-Tenant-ID')
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-ID header is required"
        )
    return tenant_id


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Dependency to require authentication."""
    token = credentials.credentials
    payload = await verify_token(token)
    return payload
