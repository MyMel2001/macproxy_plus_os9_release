"""
Coffee Extension: AI Page Cache
Caches AI-generated period-authentic 1998-era web pages for Project: Golden Years.

When Golden Years cannot find a page in the Internet Archive, it uses AI to
recreate what the page would have looked like in 1998. The result is cached
here keyed by (url, date) so subsequent requests for the same page on the
same date return the cached AI-generated version immediately.

The cache is stored in a JSON file alongside this module, and also kept in
an in-memory dict for fast lookups during the same process lifetime.
"""

import os
import json
import hashlib
import time

# Directory where the cache JSON file lives
_cache_dir = os.path.dirname(os.path.abspath(__file__))
_cache_file = os.path.join(_cache_dir, "ai_page_cache.json")

# In-memory cache: {cache_key: {"html": str, "created_at": float}}
_page_cache = {}

# Maximum age for a cached page in seconds (30 days)
_CACHE_MAX_AGE = 30 * 24 * 60 * 60


def _cache_key(url, date_str):
    """Generate a deterministic cache key from a URL and date string.
    
    The URL is normalized (lowercased, stripped of trailing slash, protocol-agnostic)
    so that http://EXAMPLE.com/ and https://example.com map to the same key.
    """
    # Normalize: lowercase, strip trailing slash, remove protocol
    normalized = url.lower().strip()
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    # Remove protocol prefix for protocol-agnostic matching
    for prefix in ("https://", "http://"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    raw = f"{normalized}|{date_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_cache():
    """Load the on-disk JSON cache into the in-memory dict.
    
    Old entries (older than _CACHE_MAX_AGE) are pruned during load.
    """
    global _page_cache
    _page_cache = {}
    if not os.path.exists(_cache_file):
        return

    try:
        with open(_cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[Coffee:PageCache] Error reading cache file: {e}")
        return

    now = time.time()
    pruned = 0
    for key, entry in data.items():
        age = now - entry.get("created_at", 0)
        if age < _CACHE_MAX_AGE:
            _page_cache[key] = entry
        else:
            pruned += 1

    if pruned:
        print(f"[Coffee:PageCache] Pruned {pruned} expired cache entr(ies)")


def _save_cache():
    """Persist the in-memory cache to the JSON file on disk."""
    try:
        with open(_cache_file, "w", encoding="utf-8") as f:
            json.dump(_page_cache, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[Coffee:PageCache] Error writing cache file: {e}")


def get_cached_page(url, date_str):
    """Retrieve a cached AI-generated page for the given URL and date.
    
    Args:
        url: The full URL of the page (e.g. "http://example.com/page.html")
        date_str: Date string in YYYYMMDD format (e.g. "19980710")
    
    Returns:
        str: The cached HTML content, or None if not found / expired.
    """
    if not _page_cache:
        _load_cache()

    key = _cache_key(url, date_str)
    entry = _page_cache.get(key)
    if entry is None:
        return None

    # Check expiry
    age = time.time() - entry.get("created_at", 0)
    if age >= _CACHE_MAX_AGE:
        del _page_cache[key]
        _save_cache()
        return None

    return entry.get("html")


def set_cached_page(url, date_str, html_content):
    """Store an AI-generated page in the cache.
    
    Args:
        url: The full URL of the page.
        date_str: Date string in YYYYMMDD format (e.g. "19980710").
        html_content: The complete HTML string to cache.
    """
    key = _cache_key(url, date_str)
    _page_cache[key] = {
        "html": html_content,
        "created_at": time.time()
    }
    _save_cache()
    print(f"[Coffee:PageCache] Cached AI page for {url} on {date_str}")


# Pre-load the cache when this module is imported
_load_cache()
