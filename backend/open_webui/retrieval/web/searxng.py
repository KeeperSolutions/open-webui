from __future__ import annotations
<<<<<<< HEAD

import logging

=======

import logging
import ssl
from functools import lru_cache

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, SEARXNG_CLIENT_CERT_FILE, SEARXNG_CLIENT_KEY_FILE
>>>>>>> v0.11.0
from open_webui.retrieval.web.main import SearchResult, get_filtered_results
from open_webui.utils.session_pool import get_session

log = logging.getLogger(__name__)

# SearXNG request headers — identifies the bot to instance operators.
_SEARXNG_HEADERS = {
    'User-Agent': 'Open WebUI (https://github.com/open-webui/open-webui) RAG Bot',
    'Accept': 'text/html',
    'Accept-Encoding': 'gzip, deflate',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
}


<<<<<<< HEAD
=======
@lru_cache
def _get_ssl_context() -> bool | ssl.SSLContext:
    if not SEARXNG_CLIENT_CERT_FILE:
        return AIOHTTP_CLIENT_SESSION_SSL

    ssl_context = ssl.create_default_context()
    ssl_context.load_cert_chain(
        certfile=SEARXNG_CLIENT_CERT_FILE,
        keyfile=SEARXNG_CLIENT_KEY_FILE or None,
    )
    return ssl_context


>>>>>>> v0.11.0
async def search_searxng(
    query_url: str,
    query: str,
    count: int,
    filter_list: list[str | None] | None = None,
    **kwargs,
) -> list[SearchResult]:
    """Query a SearXNG instance and return results sorted by relevance score.

    Optional keyword arguments (language, safesearch, time_range, categories)
    are forwarded directly as SearXNG query parameters.
    """
    # Normalise legacy ``<query>``-style URLs by stripping any query string.
    if '<query>' in query_url:
        query_url = query_url.split('?')[0]

    params = {
        'q': query,
        'format': 'json',
        'pageno': 1,
        'safesearch': kwargs.get('safesearch', '1'),
        'language': kwargs.get('language', 'all').strip().rstrip(','),
        'time_range': kwargs.get('time_range', ''),
        'categories': ''.join(kwargs.get('categories', [])),
        'theme': 'simple',
        'image_proxy': 0,
    }

    log.debug('searching %s', query_url)

    session = await get_session()
<<<<<<< HEAD
    async with session.get(query_url, headers=_SEARXNG_HEADERS, params=params) as response:
=======
    async with session.get(
        query_url,
        headers=_SEARXNG_HEADERS,
        params=params,
        ssl=_get_ssl_context(),
    ) as response:
>>>>>>> v0.11.0
        response.raise_for_status()
        payload = await response.json()

    results = sorted(payload.get('results', []), key=lambda x: x.get('score', 0), reverse=True)
    if filter_list:
        results = get_filtered_results(results, filter_list)

    return [
        SearchResult(
            link=item.get('url', ''),
            title=item.get('title'),
            snippet=item.get('content'),
        )
        for item in results[:count]
    ]
