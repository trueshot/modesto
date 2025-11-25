"""
In-memory image cache with TTL
Stores recent camera frames to reduce NVR load
"""

import time
from typing import Optional, Dict, Any
from threading import Lock


class ImageCache:
    """Thread-safe in-memory cache for camera images"""

    def __init__(self, default_ttl: int = 30):
        """
        Initialize cache

        Args:
            default_ttl: Time-to-live in seconds (default 30)
        """
        self.default_ttl = default_ttl
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[bytes]:
        """
        Get cached image if not expired

        Args:
            key: Cache key (e.g., "lodge/bagel")

        Returns:
            Image bytes if found and not expired, None otherwise
        """
        with self._lock:
            if key not in self._cache:
                return None

            entry = self._cache[key]

            # Check if expired
            if time.time() > entry['expires_at']:
                del self._cache[key]
                return None

            return entry['data']

    def set(self, key: str, data: bytes, ttl: Optional[int] = None) -> None:
        """
        Store image in cache

        Args:
            key: Cache key (e.g., "lodge/bagel")
            data: Image bytes
            ttl: Time-to-live in seconds (uses default if not specified)
        """
        if ttl is None:
            ttl = self.default_ttl

        with self._lock:
            self._cache[key] = {
                'data': data,
                'cached_at': time.time(),
                'expires_at': time.time() + ttl
            }

    def invalidate(self, key: str) -> bool:
        """
        Remove entry from cache

        Args:
            key: Cache key

        Returns:
            True if entry was removed, False if not found
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Clear all cached entries"""
        with self._lock:
            self._cache.clear()

    def stats(self) -> Dict[str, Any]:
        """
        Get cache statistics

        Returns:
            Dictionary with cache stats
        """
        with self._lock:
            total = len(self._cache)
            expired = 0
            now = time.time()

            for entry in self._cache.values():
                if now > entry['expires_at']:
                    expired += 1

            return {
                'total_entries': total,
                'valid_entries': total - expired,
                'expired_entries': expired,
                'ttl_seconds': self.default_ttl
            }
