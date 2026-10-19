"""
Embed Engine for Playro JEV.

Provides unified embed fallback providers modeled after Bingr:
1. Filmu
2. Cinezo
3. Vidbolt
4. Vidy / VidSrc
5. Vidrift

Supports movies, TV series, IMDb/TMDB ID normalization, preferred provider selection,
and optional start time parameter generation.
"""

from typing import Dict, List, Optional, Union, Any
import re
import urllib.parse


class EmbedEngine:
    """Multi-provider embed streaming endpoint generator and fallback engine."""

    PROVIDERS: List[str] = ["filmu", "cinezo", "vidbolt", "vidy", "vidrift"]

    PROVIDER_ALIASES: Dict[str, str] = {
        "vidsrc": "vidy",
        "vidsrc.pm": "vidy",
        "filmu.in": "filmu",
        "cinezo.net": "cinezo",
        "vidbolt.org": "vidbolt",
        "vidrift.com": "vidrift",
    }

    PROVIDER_TEMPLATES: Dict[str, Dict[str, str]] = {
        "filmu": {
            "movie": "https://embed.filmu.in/movie/{clean_id}",
            "tv": "https://embed.filmu.in/tv/{clean_id}/{season}/{episode}",
        },
        "cinezo": {
            "movie": "https://cinezo.net/embed/movie/{clean_id}",
            "tv": "https://cinezo.net/embed/tv/{clean_id}/{season}/{episode}",
        },
        "vidbolt": {
            "movie": "https://vidbolt.org/embed/movie/{clean_id}",
            "tv": "https://vidbolt.org/embed/tv/{clean_id}/{season}/{episode}",
        },
        "vidy": {
            "movie": "https://vidsrc.pm/embed/movie/{clean_id}",
            "tv": "https://vidsrc.pm/embed/tv/{clean_id}/{season}/{episode}",
        },
        "vidrift": {
            "movie": "https://vidrift.com/embed/movie/{clean_id}",
            "tv": "https://vidrift.com/embed/tv/{clean_id}/{season}/{episode}",
        },
    }

    @staticmethod
    def normalize_id(item_id: Any) -> str:
        """Normalizes IMDb IDs (e.g. tt1234567) or TMDB numeric IDs (e.g. 550).

        Strips whitespace, handles 'imdb:' or 'tmdb:' prefixes, and converts
        alphanumeric IMDb IDs to lowercase.
        """
        if item_id is None:
            return ""

        clean = str(item_id).strip()

        # Handle explicit prefix forms: imdb:tt1234567, tmdb:12345
        lower = clean.lower()
        if lower.startswith("imdb:"):
            clean = clean[5:].strip()
        elif lower.startswith("tmdb:"):
            clean = clean[5:].strip()

        # Lowercase IMDb tt... IDs
        if clean.lower().startswith("tt"):
            clean = clean.lower()

        return clean

    @classmethod
    def _apply_start_time(
        cls,
        url: str,
        start_time: Optional[Union[int, float, str]],
        time_format: str = "query",
    ) -> str:
        """Appends optional start time parameter to URL.

        Supports query parameter (?start={sec}) or URL fragment (#t={sec}).
        """
        if start_time is None:
            return url

        st_str = str(start_time).strip()
        if not st_str:
            return url

        # Direct parameter pass-through if caller already prefixed
        if st_str.startswith("?") or st_str.startswith("#") or st_str.startswith("&"):
            return f"{url}{st_str}"

        # Parse numeric seconds
        try:
            clean_num = st_str.rstrip("sS")
            sec: Union[int, str] = int(float(clean_num))
        except (ValueError, TypeError):
            sec = st_str

        if time_format in ("hash", "fragment") or str(time_format).startswith("#"):
            return f"{url}#t={sec}"
        else:
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}start={sec}"

    @classmethod
    def get_embed_urls(
        cls,
        media_type: str,
        item_id: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        start_time: Optional[Union[int, float, str]] = None,
        **kwargs: Any,
    ) -> Dict[str, str]:
        """Returns a dict mapping provider name to embed URL for all supported providers.

        Args:
            media_type: 'movie', 'tv', or 'series'.
            item_id: IMDb ID (e.g. 'tt0137523') or TMDB ID (e.g. '550').
            season: TV Season number (default 1 if media_type is TV).
            episode: TV Episode number (default 1 if media_type is TV).
            start_time: Optional start time in seconds.
            **kwargs: Optional time_format ('query' or 'hash'), start parameter alias.
        """
        clean_id = cls.normalize_id(item_id)
        norm_type = (media_type or "").strip().lower()
        is_tv = norm_type in ("tv", "series", "show", "episode")

        # Resolve start_time from kwargs if not directly passed
        effective_start = start_time if start_time is not None else kwargs.get("start")
        time_format = kwargs.get("time_format", "query")

        s = int(season) if season is not None else 1
        e = int(episode) if episode is not None else 1

        urls: Dict[str, str] = {}
        target_kind = "tv" if is_tv else "movie"

        for provider in cls.PROVIDERS:
            tmpl = cls.PROVIDER_TEMPLATES.get(provider, {}).get(target_kind)
            if not tmpl:
                continue

            if is_tv:
                base_url = tmpl.format(clean_id=clean_id, season=s, episode=e)
            else:
                base_url = tmpl.format(clean_id=clean_id)

            urls[provider] = cls._apply_start_time(base_url, effective_start, time_format=time_format)

        return urls

    @classmethod
    def get_preferred_embed(
        cls,
        media_type: str,
        item_id: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        preferred: str = "filmu",
        start_time: Optional[Union[int, float, str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Returns preferred embed along with full provider fallback dict.

        Format:
        {
            "provider": preferred_name,
            "embed_url": selected_url,
            "all_providers": { ... }
        }
        """
        all_urls = cls.get_embed_urls(
            media_type=media_type,
            item_id=item_id,
            season=season,
            episode=episode,
            start_time=start_time,
            **kwargs,
        )

        pref_key = (preferred or "filmu").strip().lower()
        if pref_key in all_urls:
            selected_provider = pref_key
        elif pref_key in cls.PROVIDER_ALIASES and cls.PROVIDER_ALIASES[pref_key] in all_urls:
            selected_provider = cls.PROVIDER_ALIASES[pref_key]
        else:
            # Fallback to filmu or first available provider
            selected_provider = "filmu" if "filmu" in all_urls else (next(iter(all_urls.keys())) if all_urls else "")

        selected_url = all_urls.get(selected_provider, "")

        return {
            "provider": selected_provider,
            "embed_url": selected_url,
            "all_providers": all_urls,
        }
