"""
Test suite for Multi-Provider Embed Fallback Engine.
Tests all 5 providers (filmu, cinezo, vidbolt, vidy, vidrift) for movies,
TV series, ID normalization, provider fallback selection, and start time parameters.
"""

import sys
from pathlib import Path
import pytest

# Ensure parent directory is in Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embed_engine import EmbedEngine


class TestEmbedEngine:
    """Tests for EmbedEngine functionality."""

    def test_providers_list(self):
        """Verify all 5 required providers are defined in the exact order/names."""
        assert EmbedEngine.PROVIDERS == ["filmu", "cinezo", "vidbolt", "vidy", "vidrift"]

    def test_movie_url_generation_all_providers(self):
        """Test Movie URL generation for all 5 providers with tt0137523 (Fight Club)."""
        item_id = "tt0137523"
        urls = EmbedEngine.get_embed_urls("movie", item_id)

        assert len(urls) == 5
        assert urls["filmu"] == "https://embed.filmu.in/movie/tt0137523"
        assert urls["cinezo"] == "https://cinezo.net/embed/movie/tt0137523"
        assert urls["vidbolt"] == "https://vidbolt.org/embed/movie/tt0137523"
        assert urls["vidy"] == "https://vidsrc.pm/embed/movie/tt0137523"
        assert urls["vidrift"] == "https://vidrift.com/embed/movie/tt0137523"

    def test_tv_series_url_generation_all_providers(self):
        """Test TV Series URL generation for all 5 providers with tt0944947 (Game of Thrones), S01E01."""
        item_id = "tt0944947"
        urls = EmbedEngine.get_embed_urls("tv", item_id, season=1, episode=1)

        assert len(urls) == 5
        assert urls["filmu"] == "https://embed.filmu.in/tv/tt0944947/1/1"
        assert urls["cinezo"] == "https://cinezo.net/embed/tv/tt0944947/1/1"
        assert urls["vidbolt"] == "https://vidbolt.org/embed/tv/tt0944947/1/1"
        assert urls["vidy"] == "https://vidsrc.pm/embed/tv/tt0944947/1/1"
        assert urls["vidrift"] == "https://vidrift.com/embed/tv/tt0944947/1/1"

    def test_tv_series_type_alias_series(self):
        """Verify 'series' media_type functions identically to 'tv'."""
        urls = EmbedEngine.get_embed_urls("series", "tt0944947", season=2, episode=5)
        assert urls["filmu"] == "https://embed.filmu.in/tv/tt0944947/2/5"
        assert urls["vidy"] == "https://vidsrc.pm/embed/tv/tt0944947/2/5"

    def test_id_normalization_whitespace(self):
        """Test ID normalization strips leading and trailing spaces."""
        assert EmbedEngine.normalize_id("  tt0137523  ") == "tt0137523"
        assert EmbedEngine.normalize_id("\ttt0944947\n") == "tt0944947"
        assert EmbedEngine.normalize_id("   550   ") == "550"

    def test_id_normalization_numeric_tmdb(self):
        """Test ID normalization handles numeric TMDB IDs."""
        assert EmbedEngine.normalize_id("550") == "550"
        assert EmbedEngine.normalize_id(550) == "550"
        assert EmbedEngine.normalize_id("tmdb:550") == "550"
        assert EmbedEngine.normalize_id("  tmdb: 12345 ") == "12345"

    def test_id_normalization_alphanumeric_imdb(self):
        """Test ID normalization handles alphanumeric IMDb IDs."""
        assert EmbedEngine.normalize_id("tt0137523") == "tt0137523"
        assert EmbedEngine.normalize_id("TT0137523") == "tt0137523"
        assert EmbedEngine.normalize_id("imdb:tt0137523") == "tt0137523"
        assert EmbedEngine.normalize_id("IMDB:TT0944947") == "tt0944947"

    def test_id_normalization_edge_cases(self):
        """Test ID normalization with empty or None values."""
        assert EmbedEngine.normalize_id("") == ""
        assert EmbedEngine.normalize_id(None) == ""

    def test_preferred_embed_default(self):
        """Test preferred embed defaults to filmu."""
        result = EmbedEngine.get_preferred_embed("movie", "tt0137523")
        assert result["provider"] == "filmu"
        assert result["embed_url"] == "https://embed.filmu.in/movie/tt0137523"
        assert "all_providers" in result
        assert len(result["all_providers"]) == 5
        assert result["all_providers"]["filmu"] == "https://embed.filmu.in/movie/tt0137523"

    def test_preferred_embed_selection(self):
        """Test preferred embed selection for explicit providers."""
        res_cinezo = EmbedEngine.get_preferred_embed("movie", "tt0137523", preferred="cinezo")
        assert res_cinezo["provider"] == "cinezo"
        assert res_cinezo["embed_url"] == "https://cinezo.net/embed/movie/tt0137523"

        res_vidbolt = EmbedEngine.get_preferred_embed("tv", "tt0944947", season=1, episode=1, preferred="vidbolt")
        assert res_vidbolt["provider"] == "vidbolt"
        assert res_vidbolt["embed_url"] == "https://vidbolt.org/embed/tv/tt0944947/1/1"

    def test_preferred_embed_alias_vidsrc(self):
        """Test preferred embed resolves alias 'vidsrc' to 'vidy'."""
        result = EmbedEngine.get_preferred_embed("movie", "tt0137523", preferred="vidsrc")
        assert result["provider"] == "vidy"
        assert result["embed_url"] == "https://vidsrc.pm/embed/movie/tt0137523"

    def test_preferred_embed_fallback_on_unknown(self):
        """Test fallback provider selection when an unknown provider is passed."""
        result = EmbedEngine.get_preferred_embed("movie", "tt0137523", preferred="nonexistent_streamer")
        assert result["provider"] == "filmu"
        assert result["embed_url"] == "https://embed.filmu.in/movie/tt0137523"

    def test_start_time_parameter_query(self):
        """Test start time parameter generation with query string ?start={sec}."""
        urls = EmbedEngine.get_embed_urls("movie", "tt0137523", start_time=120)
        assert urls["filmu"] == "https://embed.filmu.in/movie/tt0137523?start=120"
        assert urls["cinezo"] == "https://cinezo.net/embed/movie/tt0137523?start=120"
        assert urls["vidbolt"] == "https://vidbolt.org/embed/movie/tt0137523?start=120"
        assert urls["vidy"] == "https://vidsrc.pm/embed/movie/tt0137523?start=120"
        assert urls["vidrift"] == "https://vidrift.com/embed/movie/tt0137523?start=120"

    def test_start_time_parameter_hash(self):
        """Test start time parameter generation with hash fragment #t={sec}."""
        urls = EmbedEngine.get_embed_urls("tv", "tt0944947", season=1, episode=1, start_time=300, time_format="hash")
        assert urls["filmu"] == "https://embed.filmu.in/tv/tt0944947/1/1#t=300"
        assert urls["cinezo"] == "https://cinezo.net/embed/tv/tt0944947/1/1#t=300"
        assert urls["vidbolt"] == "https://vidbolt.org/embed/tv/tt0944947/1/1#t=300"
        assert urls["vidy"] == "https://vidsrc.pm/embed/tv/tt0944947/1/1#t=300"
        assert urls["vidrift"] == "https://vidrift.com/embed/tv/tt0944947/1/1#t=300"

    def test_start_time_via_kwargs(self):
        """Test passing start time via kwargs (start=...) into get_preferred_embed."""
        res = EmbedEngine.get_preferred_embed("movie", "tt0137523", preferred="vidrift", start=45)
        assert res["provider"] == "vidrift"
        assert res["embed_url"] == "https://vidrift.com/embed/movie/tt0137523?start=45"
