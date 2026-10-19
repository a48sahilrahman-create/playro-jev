"""
Playro JEV — Backend Addon Engine Specialist
---------------------------------------------
Manages third-party and community Stremio addons, manifest normalization,
thread/async-safe persistence, and multi-catalog federated search with deduplication.
"""

import os
import sys
import json
import copy
import uuid
import asyncio
import logging
import urllib.parse
from typing import Optional, List, Dict, Any

import httpx

logger = logging.getLogger("playro_jev.addon_manager")

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "installed_addons.json")
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

SEARCH_HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "application/json"
}

DEFAULT_ADDONS: List[Dict[str, Any]] = [
    {
        "id": "com.linvo.cinemeta",
        "name": "Cinemeta",
        "version": "3.0.12",
        "description": "Official Cinemeta Catalog for Movies and TV Series",
        "icon": "https://v3-cinemeta.strem.io/logo.png",
        "background": "",
        "manifest_url": "https://v3-cinemeta.strem.io/manifest.json",
        "transport_url": "https://v3-cinemeta.strem.io",
        "enabled": True,
        "is_default": True,
        "types": ["movie", "series"],
        "catalogs": [
            {
                "id": "top",
                "type": "movie",
                "name": "Popular",
                "extra": [{"name": "genre"}, {"name": "search"}, {"name": "skip"}]
            },
            {
                "id": "top",
                "type": "series",
                "name": "Popular",
                "extra": [{"name": "genre"}, {"name": "search"}, {"name": "skip"}]
            }
        ],
        "resources": ["catalog", "meta"]
    },
    {
        "id": "tmdb-addon",
        "name": "The Movie Database Addon",
        "version": "0.0.10",
        "description": "The Movie Database (TMDB) catalog and metadata addon",
        "icon": "https://94c8cb9f702d-tmdb-addon.baby-beamup.club/logo.png",
        "background": "",
        "manifest_url": "https://94c8cb9f702d-tmdb-addon.baby-beamup.club/manifest.json",
        "transport_url": "https://94c8cb9f702d-tmdb-addon.baby-beamup.club",
        "enabled": True,
        "is_default": True,
        "types": ["movie", "series"],
        "catalogs": [
            {
                "id": "tmdb.search",
                "type": "movie",
                "name": "Search",
                "extra": [{"name": "search", "isRequired": True, "options": []}]
            },
            {
                "id": "tmdb.search",
                "type": "series",
                "name": "Search",
                "extra": [{"name": "search", "isRequired": True, "options": []}]
            }
        ],
        "resources": ["catalog", "meta"]
    },
    {
        "id": "org.cyberflix.service",
        "name": "CyberFlix Catalog",
        "version": "1.4.1",
        "description": "CyberFlix Catalog for Stremio",
        "icon": "https://cyberflix.elfhosted.com/logo.png",
        "background": "",
        "manifest_url": "https://cyberflix.elfhosted.com/c/catalogs/manifest.json",
        "transport_url": "https://cyberflix.elfhosted.com/c/catalogs",
        "enabled": True,
        "is_default": True,
        "types": ["movie", "series"],
        "catalogs": [
            {
                "id": "cyberflix_movies",
                "type": "movie",
                "name": "CyberFlix Movies",
                "extra": [{"name": "search"}]
            },
            {
                "id": "cyberflix_series",
                "type": "series",
                "name": "CyberFlix Series",
                "extra": [{"name": "search"}]
            }
        ],
        "resources": ["catalog", "meta"]
    }
]


def normalize_manifest_url(url: str) -> str:
    """
    Normalizes a Stremio manifest URL:
    - Strip whitespace.
    - Replace 'stremio://' scheme with 'https://'.
    - Strip trailing slashes.
    - If URL does not end with '/manifest.json', append '/manifest.json'.
    """
    if not url:
        return ""
    clean = url.strip()
    if clean.startswith("stremio://"):
        clean = "https://" + clean[len("stremio://"):]
    clean = clean.rstrip("/")
    if not clean.endswith("/manifest.json"):
        clean = f"{clean}/manifest.json"
    return clean


def parse_and_validate_manifest(manifest_data: dict, manifest_url: str) -> dict:
    """
    Validates manifest data and normalizes into standard Addon schema:
    - Validates 'id' and 'name' are non-empty strings.
    - Computes transport_url.
    - Extracts searchable catalogs (extra has 'search' or extraSupported has 'search' or catalog ID contains 'search').
    - Checks resources declaring 'catalog'.
    """
    if not isinstance(manifest_data, dict):
        raise ValueError("Invalid manifest: root object must be a dictionary")

    addon_id = str(manifest_data.get("id") or "").strip()
    addon_name = str(manifest_data.get("name") or "").strip()
    if not addon_id or not addon_name:
        raise ValueError("Invalid manifest: 'id' and 'name' must be non-empty strings")

    norm_manifest_url = normalize_manifest_url(manifest_url)
    if norm_manifest_url.endswith("/manifest.json"):
        transport_url = norm_manifest_url[:-len("/manifest.json")]
    else:
        transport_url = norm_manifest_url

    raw_catalogs = manifest_data.get("catalogs", [])
    searchable_catalogs = []
    if isinstance(raw_catalogs, list):
        for cat in raw_catalogs:
            if not isinstance(cat, dict):
                continue
            cat_id = str(cat.get("id") or "").strip()
            cat_type = str(cat.get("type") or "").strip()
            if not cat_id or not cat_type:
                continue

            supports_search = False

            extra = cat.get("extra", [])
            if isinstance(extra, list):
                for ex in extra:
                    if isinstance(ex, str) and ex.strip().lower() == "search":
                        supports_search = True
                        break
                    elif isinstance(ex, dict) and str(ex.get("name") or "").strip().lower() == "search":
                        supports_search = True
                        break

            extra_supported = cat.get("extraSupported", [])
            if isinstance(extra_supported, list):
                for es in extra_supported:
                    if isinstance(es, str) and es.strip().lower() == "search":
                        supports_search = True
                        break

            if "search" in cat_id.lower():
                supports_search = True

            if supports_search:
                searchable_catalogs.append({
                    "id": cat_id,
                    "type": cat_type,
                    "name": cat.get("name") or cat_id,
                    "extra": extra
                })

    resources = manifest_data.get("resources", ["catalog", "meta"])
    has_catalog = False
    if isinstance(resources, list):
        for r in resources:
            if isinstance(r, str) and r.strip().lower() == "catalog":
                has_catalog = True
                break
            elif isinstance(r, dict) and str(r.get("name") or "").strip().lower() == "catalog":
                has_catalog = True
                break
    if bool(raw_catalogs):
        has_catalog = True

    return {
        "id": addon_id,
        "name": addon_name,
        "version": str(manifest_data.get("version") or "1.0.0"),
        "description": str(manifest_data.get("description") or ""),
        "icon": str(manifest_data.get("logo") or manifest_data.get("icon") or ""),
        "background": str(manifest_data.get("background") or ""),
        "manifest_url": norm_manifest_url,
        "transport_url": transport_url,
        "enabled": True,
        "is_default": False,
        "has_catalog": has_catalog,
        "types": manifest_data.get("types", ["movie", "series"]),
        "catalogs": searchable_catalogs,
        "resources": resources
    }


class AddonManager:
    """
    Thread- and async-safe Stremio Addon Manager.
    Persists installed addons to disk atomically and executes federated catalog queries.
    """

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = os.path.abspath(config_path)
        self._lock = asyncio.Lock()
        self._addons: List[Dict[str, Any]] = []
        self._load_or_init()

    def _load_or_init(self):
        """Loads addons from JSON file, or initializes defaults if missing or empty."""
        if os.path.exists(self.config_path) and os.path.getsize(self.config_path) > 0:
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        self._addons = data
                        return
            except Exception as e:
                logger.warning(f"Failed to read addons config {self.config_path}: {e}")

        # Initialize defaults and persist
        self._addons = copy.deepcopy(DEFAULT_ADDONS)
        self._save()

    def _save(self):
        """Thread/async-safe atomic save using temporary file + os.replace."""
        dir_name = os.path.dirname(self.config_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        temp_path = f"{self.config_path}.tmp.{uuid.uuid4().hex}"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self._addons, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, self.config_path)
        except Exception:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise

    def get_addons(self) -> List[Dict[str, Any]]:
        """Returns deep copy of all installed addons."""
        return copy.deepcopy(self._addons)

    def get_addon(self, addon_id: str) -> Optional[Dict[str, Any]]:
        """Returns single addon by ID, or None if not found."""
        for a in self._addons:
            if a.get("id") == addon_id:
                return copy.deepcopy(a)
        return None

    async def install_addon(self, url: str, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
        """
        Fetches manifest from URL with browser User-Agent headers (8.0s timeout),
        validates, updates or appends to installed addons, and saves atomically.
        """
        norm_url = normalize_manifest_url(url)
        if not norm_url:
            raise ValueError("Addon URL cannot be empty")

        headers = {
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "application/json"
        }

        if client is not None:
            resp = await client.get(norm_url, headers=headers, timeout=8.0)
            resp.raise_for_status()
            manifest_data = resp.json()
        else:
            async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as local_client:
                resp = await local_client.get(norm_url, headers=headers)
                resp.raise_for_status()
                manifest_data = resp.json()

        addon_obj = parse_and_validate_manifest(manifest_data, norm_url)

        async with self._lock:
            existing_idx = next((i for i, a in enumerate(self._addons) if a.get("id") == addon_obj["id"]), None)
            if existing_idx is not None:
                addon_obj["is_default"] = self._addons[existing_idx].get("is_default", False)
                self._addons[existing_idx] = addon_obj
            else:
                self._addons.append(addon_obj)
            self._save()

        return copy.deepcopy(addon_obj)

    async def toggle_addon(self, addon_id: str, enabled: Optional[bool] = None) -> Dict[str, Any]:
        """Toggles or sets the enabled status of an addon."""
        async with self._lock:
            target = None
            for a in self._addons:
                if a.get("id") == addon_id:
                    target = a
                    break
            if not target:
                raise ValueError(f"Addon with id '{addon_id}' not found")

            if enabled is None:
                target["enabled"] = not target.get("enabled", True)
            else:
                target["enabled"] = bool(enabled)

            self._save()
            return copy.deepcopy(target)

    async def delete_addon(self, addon_id: str) -> bool:
        """Removes an addon by ID and persists."""
        async with self._lock:
            initial_len = len(self._addons)
            self._addons = [a for a in self._addons if a.get("id") != addon_id]
            if len(self._addons) < initial_len:
                self._save()
                return True
            return False

    async def search_catalogs(
        self,
        query: str,
        media_type: str = "all",
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 3.5
    ) -> List[Dict[str, Any]]:
        """
        ZERO GUESSING POLICY: Query string is passed directly (after strip) with zero alterations.
        Concurrently queries searchable catalogs across all enabled addons.
        Standardizes and deduplicates results across sources.
        """
        clean_q = query.strip()
        if not clean_q:
            return []

        target_requests = []
        seen_urls = set()

        for addon in self._addons:
            if not addon.get("enabled", True):
                continue
            transport_url = (addon.get("transport_url") or "").rstrip("/")
            if not transport_url:
                continue
            addon_name = addon.get("name") or addon.get("id") or "addon"
            catalogs = addon.get("catalogs", [])

            for cat in catalogs:
                c_type = cat.get("type", "movie")
                c_id = cat.get("id")
                if not c_id:
                    continue
                if media_type != "all" and c_type != media_type:
                    continue

                req_url = f"{transport_url}/catalog/{c_type}/{c_id}/search={urllib.parse.quote(clean_q)}.json"
                if req_url not in seen_urls:
                    seen_urls.add(req_url)
                    target_requests.append((addon_name, c_type, req_url))

        if not target_requests:
            return []

        async def _fetch_one(c: httpx.AsyncClient, name: str, m_type: str, url: str) -> List[Dict[str, Any]]:
            try:
                r = await c.get(url, headers=SEARCH_HEADERS, timeout=timeout)
                if r.status_code != 200:
                    return []
                payload = r.json()
                metas = payload.get("metas", [])
                if not isinstance(metas, list):
                    return []
                items = []
                for m in metas:
                    if not isinstance(m, dict):
                        continue
                    std_item = self._standardize_meta(m, name, m_type)
                    if std_item:
                        items.append(std_item)
                return items
            except Exception:
                return []

        if client is not None:
            tasks = [_fetch_one(client, name, m_type, url) for (name, m_type, url) in target_requests]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout + 1.0) as local_client:
                tasks = [_fetch_one(local_client, name, m_type, url) for (name, m_type, url) in target_requests]
                results = await asyncio.gather(*tasks, return_exceptions=True)

        collected_items: List[Dict[str, Any]] = []
        for res in results:
            if isinstance(res, list):
                collected_items.extend(res)

        return self._deduplicate_and_merge(collected_items)

    def _standardize_meta(self, m: dict, addon_name: str, fallback_type: str) -> Optional[Dict[str, Any]]:
        """Standardizes raw Stremio meta into Playro JEV unified catalog dictionary."""
        title = str(m.get("name") or "").strip()
        if not title:
            return None

        m_type = str(m.get("type") or fallback_type or "movie").strip().lower()
        item_id = str(m.get("id") or m.get("imdb_id") or f"{m_type}:{title}").strip()

        genres = m.get("genres")
        if genres and isinstance(genres, list) and len(genres) > 0 and str(genres[0]).strip():
            category = str(genres[0]).strip()
        else:
            category = m_type.title()

        rating = str(m.get("imdbRating") or m.get("rating") or "8.0").strip()

        year_raw = str(m.get("year") or m.get("releaseInfo") or "").strip()
        year_val = year_raw.split("–")[0].split("-")[0].strip()

        poster = str(m.get("poster") or "").strip()
        backdrop = str(m.get("background") or m.get("backdrop") or poster).strip()

        overview = str(m.get("description") or m.get("overview") or m.get("synopsis") or "").strip()

        badge = "4K / 1080P • STREAM" if m_type == "movie" else "COMPLETE SERIES • MULTI AUDIO"

        return {
            "id": item_id,
            "title": title,
            "year": year_val,
            "type": m_type,
            "category": category,
            "rating": rating,
            "poster": poster,
            "backdrop": backdrop,
            "overview": overview,
            "badge": badge,
            "source": addon_name
        }

    def _merge_two_items(self, existing: dict, incoming: dict):
        """
        Merges metadata from incoming into existing in-place:
        - Prioritizes IMDb 'tt...' ID over custom prefix IDs.
        - Combines distinct sources into 'sources' list and comma-separated 'source'.
        - Prioritizes non-empty/higher quality poster.
        - Prioritizes distinct backdrop over poster fallback.
        - Prioritizes non-empty year.
        - Prioritizes longer overview.
        """
        # 1. ID: Prefer IMDb 'tt...' ID
        inc_id = str(incoming.get("id") or "").strip()
        ext_id = str(existing.get("id") or "").strip()
        if inc_id.startswith("tt") and not ext_id.startswith("tt"):
            existing["id"] = inc_id

        # 2. Sources: combine sources
        ext_sources = existing.setdefault("sources", [])
        if not ext_sources and existing.get("source"):
            ext_sources.append(existing["source"])

        inc_sources = incoming.get("sources", [])
        if not inc_sources and incoming.get("source"):
            inc_sources = [incoming["source"]]

        for s in inc_sources:
            if s and s not in ext_sources:
                ext_sources.append(s)

        existing["source"] = ", ".join(ext_sources) if ext_sources else existing.get("source", "")

        # 3. Poster: Prioritize non-empty, avoid placeholders
        p_ext = str(existing.get("poster") or "").strip()
        p_inc = str(incoming.get("poster") or "").strip()
        if not p_ext and p_inc:
            existing["poster"] = p_inc
        elif p_ext and p_inc:
            if any(ph in p_ext.lower() for ph in ["placeholder", "no-poster", "noposter"]):
                existing["poster"] = p_inc
            elif ("amazon" in p_inc.lower() or "tmdb.org" in p_inc.lower()) and not ("amazon" in p_ext.lower() or "tmdb.org" in p_ext.lower()):
                existing["poster"] = p_inc

        # 4. Backdrop: Distinct backdrop over poster fallback
        b_ext = str(existing.get("backdrop") or "").strip()
        b_inc = str(incoming.get("backdrop") or "").strip()
        current_poster = str(existing.get("poster") or "").strip()

        if not b_ext and b_inc:
            existing["backdrop"] = b_inc
        elif b_inc and b_inc != p_inc and (not b_ext or b_ext == current_poster or b_ext == p_ext):
            existing["backdrop"] = b_inc

        # 5. Year: Prioritize non-empty year
        y_ext = str(existing.get("year") or "").strip()
        y_inc = str(incoming.get("year") or "").strip()
        if not y_ext and y_inc:
            existing["year"] = y_inc

        # 6. Overview: Longer synopsis
        desc_ext = str(existing.get("overview") or "").strip()
        desc_inc = str(incoming.get("overview") or "").strip()
        if len(desc_inc) > len(desc_ext):
            existing["overview"] = desc_inc

        # 7. Rating: Prioritize meaningful rating over default "8.0"
        r_ext = str(existing.get("rating") or "").strip()
        r_inc = str(incoming.get("rating") or "").strip()
        if (not r_ext or r_ext == "8.0") and r_inc and r_inc != "8.0":
            existing["rating"] = r_inc

        # 8. Category
        if not existing.get("category") and incoming.get("category"):
            existing["category"] = incoming["category"]

    def _deduplicate_and_merge(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicates items by unique ID, then merges items with identical (title, year)
        where one has an IMDb 'tt...' ID.
        """
        if not items:
            return []

        # Phase 1: Deduplicate by unique id
        merged_by_id: Dict[str, Dict[str, Any]] = {}
        for item in items:
            item_id = item.get("id")
            if not item_id:
                continue

            if "sources" not in item:
                item["sources"] = [item["source"]] if item.get("source") else []

            if item_id in merged_by_id:
                self._merge_two_items(merged_by_id[item_id], item)
            else:
                merged_by_id[item_id] = copy.deepcopy(item)

        # Phase 2: Merge items with identical (title.lower().strip(), year) where one has IMDb tt... ID
        title_year_map: Dict[tuple, Dict[str, Any]] = {}
        deduped_items: List[Dict[str, Any]] = []

        for it in merged_by_id.values():
            t_key = (it.get("title") or "").strip().lower()
            y_key = str(it.get("year") or "").strip()

            if not t_key:
                deduped_items.append(it)
                continue

            key = (t_key, y_key)
            if key in title_year_map:
                existing = title_year_map[key]
                has_imdb = existing.get("id", "").startswith("tt") or it.get("id", "").startswith("tt")
                same_type = existing.get("type") == it.get("type")
                if has_imdb or same_type:
                    self._merge_two_items(existing, it)
                    continue

            title_year_map[key] = it
            deduped_items.append(it)

        return deduped_items
