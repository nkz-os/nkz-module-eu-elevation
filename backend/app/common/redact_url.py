"""Remove credentials from connection URLs before they are logged."""

from urllib.parse import urlsplit, urlunsplit


def redact_url_credentials(url: str) -> str:
    """Return ``url`` with any user/password replaced by ``***``.

    Scheme, host, port, path and query are kept. A URL without credentials is
    returned unchanged.
    """
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit(parts._replace(netloc=f"***@{host}"))
