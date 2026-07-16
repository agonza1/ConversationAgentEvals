from __future__ import annotations

import ssl

import certifi


def verified_ssl_context() -> ssl.SSLContext:
    """Return an SSL context that trusts certifi's CA bundle.

    python.org macOS installs often ship without system CA certs, so bare
    ``urllib.request.urlopen`` fails with CERTIFICATE_VERIFY_FAILED. httpx
    already uses certifi by default; stdlib callers need this context.
    """
    return ssl.create_default_context(cafile=certifi.where())
