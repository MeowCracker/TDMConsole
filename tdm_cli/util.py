"""Small shared helpers that don't belong to any single frontend."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yarl import URL

MASKED_PASSWORD = "****"


def mask_proxy(url: "URL | str") -> str:
    """Render a proxy URL with its password hidden.

    The WebUI settings panel and the shared log both display the proxy — a
    ``user:pass@host`` URL must never reach the browser (or the log ring
    buffer, which is broadcast to every web client) with the real password.
    """
    if not url:
        return ""
    if isinstance(url, str):
        from yarl import URL as _URL

        try:
            url = _URL(url)
        except ValueError:
            return str(url)
    if url.password:
        return str(url.with_password(MASKED_PASSWORD))
    return str(url)
