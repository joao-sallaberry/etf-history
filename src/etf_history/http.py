import logging
import time

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; etf-history/0.1)"


def client(timeout: float = 60, user_agent: str = USER_AGENT) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True)


def fetch(http: httpx.Client, url: str, *, retries: int = 3) -> bytes | None:
    """GET a URL. Returns None on 404."""
    for attempt in range(1, retries + 1):
        try:
            resp = http.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            if attempt == retries:
                raise
            log.warning("GET %s failed (%s), retrying", url, exc)
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")
