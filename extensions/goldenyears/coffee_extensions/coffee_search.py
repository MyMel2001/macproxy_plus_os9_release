"""
Coffee Extension: Web Search
Maps Google/Yahoo/Bing/Altavista/DuckDuckGo search functionality to real web search
via the Wiby search API (retro web) and DuckDuckGo instant answers.

handle_action_data() returns structured data that goldenyears applies to the
archived page fetched from the form's original action URL.
"""

import requests
import json
import urllib.parse

DOMAIN = "search.goldenyears.local"
DESCRIPTION = "Web search backend. Maps Google/Yahoo/Bing/Altavista search to real search via Wiby (retro web) and DuckDuckGo."

ACTION_ROUTES = {
    "google.com/search": "search",
    "google.com/": "search",
    "search.yahoo.com": "search",
    "bing.com/search": "search",
    "bing.com/": "search",
    "altavista.com": "search",
    "search": "search",
    "duckduckgo.com": "search",
    "ask.com": "search",
    "webcrawler.com": "search",
    "lycos.com": "search",
    "hotbot.com": "search",
    "dogpile.com": "search",
}


def _search_wiby(query):
    """Search via Wiby - a search engine for the retro web."""
    try:
        resp = requests.get(
            "https://wiby.me/api/",
            params={"q": query},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
    except Exception as e:
        print(f"[Coffee:Search] Wiby error: {e}")
    return None


def _search_duckduckgo(query):
    """Get DuckDuckGo instant answer."""
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[Coffee:Search] DDG error: {e}")
    return None


def _search_wikipedia(query):
    """Search Wikipedia API."""
    try:
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": 10,
                "format": "json"
            },
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[Coffee:Search] Wikipedia error: {e}")
    return None


def handle_action_data(action, params, year):
    """Handle a search action and return structured data for applying to the archived page.
    Returns a dict with keys: type, title, content, items, is_payment"""
    query = params.get("q") or params.get("query") or params.get("search") or params.get("p") or ""

    if not query:
        return {
            "type": "search_form",
            "title": "Web Search",
            "content": "Enter a search query to find pages on the web.",
            "items": [],
            "is_payment": False
        }

    # Perform the search
    print(f'[Coffee:Search] Searching for: {query}')

    wiby_results = _search_wiby(query)
    ddg_result = _search_duckduckgo(query)
    wiki_results = _search_wikipedia(query)

    items = []

    # DuckDuckGo instant answer
    if ddg_result and ddg_result.get("AbstractText"):
        abstract = ddg_result.get("AbstractText", "")
        source = ddg_result.get("AbstractSource", "")
        url = ddg_result.get("AbstractURL", "")
        if abstract:
            items.append({
                "type": "instant_answer",
                "title": "Instant Answer",
                "description": abstract,
                "source": source,
                "url": url
            })

    # Wiby results
    if wiby_results:
        for result in wiby_results[:10]:
            title = result.get("title", "Untitled")
            url = result.get("url", "")
            description = result.get("description", "")
            items.append({
                "type": "result",
                "title": title,
                "url": url,
                "description": description
            })

    # Wikipedia results as fallback
    if not items and wiki_results and len(wiki_results) > 1:
        terms = wiki_results[1]
        urls = wiki_results[3] if len(wiki_results) > 3 else []
        for i, term in enumerate(terms[:5]):
            url = urls[i] if i < len(urls) else f"https://en.wikipedia.org/wiki/{urllib.parse.quote(term)}"
            items.append({
                "type": "result",
                "title": term,
                "url": url,
                "description": "Wikipedia article"
            })

    if not items:
        return {
            "type": "search_results",
            "title": f"No results for '{query}'",
            "content": f"No results found for '{query}'. Try different keywords.",
            "items": [],
            "is_payment": False
        }

    return {
        "type": "search_results",
        "title": f"Search Results: {query}",
        "content": f"Found {len(items)} result(s) for '{query}'",
        "items": items,
        "is_payment": False
    }


def handle_action(action, params, year):
    """Legacy handler - kept for compatibility. Returns structured data via handle_action_data."""
    return handle_action_data(action, params, year)
