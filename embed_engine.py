"""
Embed Engine for Playro JEV.

Provides unified embed fallback providers modeled after Bingr:
1. Filmu: https://embed.filmu.in/movie/{imdb_id} | https://embed.filmu.in/tv/{imdb_id}/{season}/{episode}
2. Cinezo: https://cinezo.net/embed/movie/{imdb_id} | https://cinezo.net/embed/tv/{imdb_id}/{season}/{episode}
3. Vidbolt: https://vidbolt.org/embed/movie/{imdb_id} | https://vidbolt.org/embed/tv/{imdb_id}/{season}/{episode}
4. Vidy / VidSrc: https://vidsrc.pm/embed/movie/{imdb_id} | https://vidsrc.pm/embed/tv/{imdb_id}/{season}/{episode}
5. Vidrift: https://vidrift.com/embed/movie/{imdb_id} | https://vidrift.com/embed/tv/{imdb_id}/{season}/{episode}

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
        title: str = "",
        year: str = "",
        season: Optional[int] = None,
        episode: Optional[int] = None,
        imdb_id: str = "",
        media_type: Optional[str] = None,
        item_id: Optional[str] = None,
        start_time: Optional[Union[int, float, str]] = None,
        **kwargs: Any,
    ) -> Dict[str, str]:
        """Returns a dict mapping provider name to embed URL for all 5 providers.

        Supports both:
        - get_embed_urls(title: str, year: str = "", season: int = None, episode: int = None, imdb_id: str = "")
        - get_embed_urls(media_type: str, item_id: str, season: int = None, episode: int = None, ...)
        """
        # Resolve aliases from kwargs
        if not media_type and "type" in kwargs:
            media_type = kwargs["type"]
        if not item_id and "id" in kwargs:
            item_id = kwargs["id"]

        # Determine raw identifier
        raw_id = imdb_id or item_id
        norm_type = str(media_type).strip().lower() if media_type else ""

        # Positional compatibility: if title is 'movie'/'tv'/'series' and year is the ID
        if title and str(title).strip().lower() in ("movie", "tv", "series", "show", "episode"):
            if not norm_type:
                norm_type = str(title).strip().lower()
            if not raw_id and year:
                raw_id = year
        elif not raw_id:
            # Check if title itself is an ID (e.g. tt...)
            if title and (str(title).strip().lower().startswith("tt") or str(title).strip().isdigit()):
                raw_id = title
            elif year and (str(year).strip().lower().startswith("tt") or str(year).strip().isdigit()):
                raw_id = year
            else:
                raw_id = title or ""

        clean_id = cls.normalize_id(raw_id)

        # Determine media type (movie vs tv)
        if not norm_type:
            if season is not None or episode is not None:
                norm_type = "tv"
            else:
                norm_type = "movie"

        is_tv = norm_type in ("tv", "series", "show", "episode")

        # Resolve start_time
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
        title: str = "",
        year: str = "",
        season: Optional[int] = None,
        episode: Optional[int] = None,
        imdb_id: str = "",
        preferred: str = "filmu",
        start_time: Optional[Union[int, float, str]] = None,
        media_type: Optional[str] = None,
        item_id: Optional[str] = None,
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
        # Handle provider passed as 5th positional argument or in kwargs
        if "preferred" in kwargs:
            preferred = kwargs.pop("preferred")
        elif imdb_id and (imdb_id.lower() in cls.PROVIDERS or imdb_id.lower() in cls.PROVIDER_ALIASES):
            preferred = imdb_id
            imdb_id = ""

        all_urls = cls.get_embed_urls(
            title=title,
            year=year,
            season=season,
            episode=episode,
            imdb_id=imdb_id,
            media_type=media_type,
            item_id=item_id,
            start_time=start_time,
            **kwargs,
        )

        pref_key = (preferred or "filmu").strip().lower()
        if pref_key in all_urls:
            selected_provider = pref_key
        elif pref_key in cls.PROVIDER_ALIASES and cls.PROVIDER_ALIASES[pref_key] in all_urls:
            selected_provider = cls.PROVIDER_ALIASES[pref_key]
        else:
            selected_provider = "filmu" if "filmu" in all_urls else (next(iter(all_urls.keys())) if all_urls else "")

        selected_url = all_urls.get(selected_provider, "")

        return {
            "provider": selected_provider,
            "embed_url": selected_url,
            "all_providers": all_urls,
        }
