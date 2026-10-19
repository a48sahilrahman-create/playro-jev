import pytest
import httpx
from unittest.mock import AsyncMock
from fast_scrapers import FastScrapers

@pytest.mark.asyncio
async def test_search_4khdhub_episode_isolation():
    fs = FastScrapers()
    sample_4khd_html = """
    <div class="episode-download-item">
        <div class="episode-file-title">The Boys S01E01 1080p</div>
        <span class="badge-psa">Episode 1</span>
        <a href="https://greenmotors.club/?id=123">Download HubCloud</a>
    </div>
    <div class="episode-download-item">
        <div class="episode-file-title">The Boys S01E02 1080p</div>
        <span class="badge-psa">Episode 2</span>
        <a href="https://greenmotors.club/?id=456">Download HubCloud</a>
    </div>
    """
    sample_search_html = """
    <a href="https://4khdhub.one/the-boys-season-1-2019-series/">
        <div class="post-title">The Boys Season 1</div>
    </a>
    """
    async def mock_get(url, **kwargs):
        if "?s=" in str(url):
            return httpx.Response(200, text=sample_search_html)
        return httpx.Response(200, text=sample_4khd_html)

    client = AsyncMock()
    client.get = mock_get

    res = await fs.search_4khdhub(client, "The Boys", season=1, episode=1)
    assert len(res) == 1
    assert res[0]["platform"] == "4KHDHub"
    assert res[0]["episode_tag"] == "S01E01"
    assert res[0]["is_batch"] is False
    assert "https://greenmotors.club" in res[0]["target_url"]

@pytest.mark.asyncio
async def test_search_hdhub4u_dual_mode_episodes():
    fs = FastScrapers()
    sample_typesense = {
        "hits": [{
            "document": {
                "post_title": "The Boys Season 1 (2019) Dual Audio",
                "permalink": "https://hdhub4u.ms/the-boys-season-1/"
            }
        }]
    }

    # Mode A: kp-header / kp-hc
    sample_html_mode_a = """
    <div class="kp-header">
        <p>EPiSODE 1 1080p [1.2GB/E]</p>
        <a href="https://hubcloud.one/drive/s1e1">HubCloud Direct</a>
    </div>
    <div class="kp-header">
        <p>EPiSODE 2 1080p [1.2GB/E]</p>
        <a href="https://hubcloud.one/drive/s1e2">HubCloud Direct</a>
    </div>
    """
    async def mock_hdh_a(url, **kwargs):
        if "search.pingora.fyi" in str(url):
            return httpx.Response(200, json=sample_typesense)
        return httpx.Response(200, text=sample_html_mode_a)

    client_a = AsyncMock()
    client_a.get = mock_hdh_a

    res_a = await fs.search_hdhub4u(client_a, "The Boys", season=1, episode=1)
    assert len(res_a) == 1
    assert res_a[0]["platform"] == "HDHub4u"
    assert res_a[0]["episode_tag"] == "S01E01"
    assert res_a[0]["is_batch"] is False
    assert res_a[0]["source_id"] == "hdh_mode_a"

    # Mode B: Heading sequences
    sample_html_mode_b = """
    <h4>EPiSODE 1</h4>
    <p>
        <a href="https://hubdrive.pics/s1e1">Drive</a>
        <a href="https://greenmotors.club/s1e1">Watch</a>
    </p>
    <h4>EPiSODE 2</h4>
    <p>
        <a href="https://hubdrive.pics/s1e2">Drive</a>
    </p>
    """
    async def mock_hdh_b(url, **kwargs):
        if "search.pingora.fyi" in str(url):
            return httpx.Response(200, json=sample_typesense)
        return httpx.Response(200, text=sample_html_mode_b)

    client_b = AsyncMock()
    client_b.get = mock_hdh_b

    res_b = await fs.search_hdhub4u(client_b, "The Boys", season=1, episode=1)
    assert len(res_b) == 2
    for item in res_b:
        assert item["episode_tag"] == "S01E01"
        assert item["is_batch"] is False
        assert item["source_id"] == "hdh_mode_b"

@pytest.mark.asyncio
async def test_search_olamovies_discrete_episodes():
    fs = FastScrapers()
    sample_ola_search = '<a href="/tv-series/the-boys-season-1">The Boys Season 1</a>'
    sample_ola_html = r'''
    <script>self.__next_f.push([1,"{\"id\":\"itm_1\",\"btnId\":\"b1\",\"url\":\"https://links.olamovies.to/abc\",\"label\":\"Episode 01\",\"filename\":\"The.Boys.S01E01.1080p.mkv\"}"]) </script>
    <script>self.__next_f.push([1,"{\"id\":\"itm_2\",\"btnId\":\"b2\",\"url\":\"https://links.olamovies.to/def\",\"label\":\"Episode 02\",\"filename\":\"The.Boys.S01E02.1080p.mkv\"}"]) </script>
    '''
    async def mock_ola(url, **kwargs):
        if "search?q=" in str(url):
            return httpx.Response(200, text=sample_ola_search)
        return httpx.Response(200, text=sample_ola_html)

    client = AsyncMock()
    client.get = mock_ola

    res = await fs.search_olamovies(client, "The Boys", season=1, episode=1)
    assert len(res) == 1
    assert res[0]["platform"] == "OlaMovies"
    assert res[0]["episode_tag"] == "S01E01"
    assert res[0]["is_batch"] is False
