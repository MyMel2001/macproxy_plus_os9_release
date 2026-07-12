"""
Coffee Extension: Bluesky Social
Maps Twitter/X/TweetDeck functionality to Bluesky's AT Protocol API.
Supports custom PDS (Personal Data Server) endpoints.

handle_action_data() returns structured data that goldenyears applies to the
archived page fetched from the form's original action URL.
"""

import requests
import json
import urllib.parse
import datetime

DOMAIN = "bluesky.goldenyears.yay"
DESCRIPTION = "Social media backend using Bluesky's AT Protocol. Maps Twitter/X features to Bluesky. Supports custom PDS endpoints."

ACTION_ROUTES = {
    "twitter.com/login": "login",
    "twitter.com/session": "login",
    "twitter.com/share": "share",
    "twitter.com/intent/tweet": "share",
    "twitter.com/search": "search",
    "twitter.com/": "timeline",
    "x.com/login": "login",
    "x.com/share": "share",
    "x.com/": "timeline",
    "facebook.com/login": "login",
    "facebook.com/share": "share",
    "facebook.com/": "timeline",
}

# Simple in-memory session for the Bluesky PDS
_bsky_session = None
_bsky_handle = None
_bsky_pds_url = None


def _get_pds_url():
    """Get the configured PDS URL, defaulting to bsky.social."""
    global _bsky_pds_url
    if _bsky_pds_url:
        return _bsky_pds_url
    try:
        import config
        if hasattr(config, 'BLUESKY_PDS_URL') and config.BLUESKY_PDS_URL:
            _bsky_pds_url = config.BLUESKY_PDS_URL.rstrip('/')
            return _bsky_pds_url
    except Exception:
        pass
    return "https://bsky.social"


def _get_bsky_credentials():
    """Get Bluesky credentials from config if available."""
    try:
        import config
        if hasattr(config, 'BLUESKY_HANDLE') and hasattr(config, 'BLUESKY_APP_PASSWORD'):
            return config.BLUESKY_HANDLE, config.BLUESKY_APP_PASSWORD
    except Exception:
        pass
    return None, None


def _bsky_login(handle, password):
    """Authenticate with Bluesky PDS."""
    global _bsky_session, _bsky_handle
    pds_url = _get_pds_url()
    try:
        resp = requests.post(
            f"{pds_url}/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": password},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            _bsky_session = data.get("accessJwt")
            _bsky_handle = data.get("handle")
            print(f"[Coffee:Bluesky] Logged in as @{_bsky_handle} via PDS: {pds_url}")
            return True
        else:
            print(f"[Coffee:Bluesky] Login failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Coffee:Bluesky] Login error: {e}")
    return False


def _bsky_get_timeline(limit=20):
    """Fetch the Bluesky home timeline."""
    if not _bsky_session:
        return None
    pds_url = _get_pds_url()
    try:
        resp = requests.get(
            f"{pds_url}/xrpc/app.bsky.feed.getTimeline",
            headers={"Authorization": f"Bearer {_bsky_session}"},
            params={"limit": limit},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("feed", [])
    except Exception as e:
        print(f"[Coffee:Bluesky] Timeline error: {e}")
    return None


def _bsky_search(query, limit=10):
    """Search Bluesky for posts."""
    if not _bsky_session:
        return None
    pds_url = _get_pds_url()
    try:
        resp = requests.get(
            f"{pds_url}/xrpc/app.bsky.feed.searchPosts",
            headers={"Authorization": f"Bearer {_bsky_session}"},
            params={"q": query, "limit": limit},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("posts", [])
    except Exception as e:
        print(f"[Coffee:Bluesky] Search error: {e}")
    return None


def _bsky_get_author_feed(author, limit=10):
    """Fetch posts from a specific author."""
    if not _bsky_session:
        return None
    pds_url = _get_pds_url()
    try:
        resp = requests.get(
            f"{pds_url}/xrpc/app.bsky.feed.getAuthorFeed",
            headers={"Authorization": f"Bearer {_bsky_session}"},
            params={"actor": author, "limit": limit},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("feed", [])
    except Exception as e:
        print(f"[Coffee:Bluesky] Author feed error: {e}")
    return None


def _bsky_get_profile(actor):
    """Get a user's profile."""
    if not _bsky_session:
        return None
    pds_url = _get_pds_url()
    try:
        resp = requests.get(
            f"{pds_url}/xrpc/app.bsky.actor.getProfile",
            headers={"Authorization": f"Bearer {_bsky_session}"},
            params={"actor": actor},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[Coffee:Bluesky] Profile error: {e}")
    return None


def _bsky_post(text):
    """Create a post on Bluesky."""
    if not _bsky_session:
        return False
    pds_url = _get_pds_url()
    try:
        resp = requests.post(
            f"{pds_url}/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {_bsky_session}"},
            json={
                "repo": _bsky_handle,
                "collection": "app.bsky.feed.post",
                "record": {
                    "$type": "app.bsky.feed.post",
                    "text": text,
                    "createdAt": __import__('datetime').datetime.now().isoformat() + "Z"
                }
            },
            timeout=10
        )
        if resp.status_code == 200:
            print(f"[Coffee:Bluesky] Post created successfully")
            return True
        else:
            print(f"[Coffee:Bluesky] Post error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Coffee:Bluesky] Post error: {e}")
    return False


def handle_action_data(action, params, year):
    """Handle a social media action via Bluesky and return structured data for applying to the archived page.
    Returns a dict with keys: type, title, content, items, is_payment"""
    handle, password = _get_bsky_credentials()
    pds_url = _get_pds_url()

    if action == "login":
        form_handle = params.get("username") or params.get("email") or params.get("handle") or handle
        form_password = params.get("password") or params.get("pass") or password

        if form_handle and form_password:
            if _bsky_login(form_handle, form_password):
                return {
                    "type": "login_result",
                    "title": "Signed In",
                    "content": f"Signed in to Bluesky as @{_bsky_handle}",
                    "items": [],
                    "is_payment": False
                }
            else:
                return {
                    "type": "login_result",
                    "title": "Login Failed",
                    "content": "Could not sign in. Check your credentials.",
                    "items": [],
                    "is_payment": False
                }
        else:
            return {
                "type": "login_form",
                "title": "Sign In to Bluesky",
                "content": f"PDS: {pds_url}",
                "items": [],
                "is_payment": False
            }

    elif action == "timeline":
        if not _bsky_session:
            handle, password = _get_bsky_credentials()
            if handle and password:
                _bsky_login(handle, password)

        posts = _bsky_get_timeline()
        if posts:
            items = []
            for item in posts[:20]:
                post = item.get("post", {})
                author = post.get("author", {}).get("displayName", "unknown")
                author_handle = post.get("author", {}).get("handle", "unknown")
                text = post.get("record", {}).get("text", "")
                like_count = post.get("likeCount", 0)
                reply_count = post.get("replyCount", 0)
                repost_count = post.get("repostCount", 0)
                items.append({
                    "author": author,
                    "handle": author_handle,
                    "text": text,
                    "likes": like_count,
                    "replies": reply_count,
                    "reposts": repost_count
                })

            return {
                "type": "timeline",
                "title": "Bluesky Timeline",
                "content": f"@{_bsky_handle}",
                "items": items,
                "is_payment": False
            }
        else:
            return {
                "type": "timeline",
                "title": "Timeline",
                "content": "Could not load timeline. Sign in first.",
                "items": [],
                "is_payment": False
            }

    elif action == "share":
        text = params.get("text") or params.get("status") or ""
        if text:
            if _bsky_post(text):
                return {
                    "type": "post_result",
                    "title": "Posted!",
                    "content": "Your message has been posted to Bluesky.",
                    "items": [],
                    "is_payment": False
                }
            else:
                return {
                    "type": "post_result",
                    "title": "Error",
                    "content": "Could not post. Are you signed in?",
                    "items": [],
                    "is_payment": False
                }
        else:
            return {
                "type": "share_form",
                "title": "New Post",
                "content": "Share your thoughts on Bluesky",
                "items": [],
                "is_payment": False
            }

    elif action == "search":
        query = params.get("q") or params.get("query") or ""
        if query:
            results = _bsky_search(query)
            if results:
                items = []
                for post in results[:10]:
                    author = post.get("author", {}).get("displayName", "unknown")
                    author_handle = post.get("author", {}).get("handle", "unknown")
                    text = post.get("record", {}).get("text", "")
                    items.append({
                        "author": author,
                        "handle": author_handle,
                        "text": text
                    })

                return {
                    "type": "search_results",
                    "title": f"Search: {query}",
                    "content": f"Found {len(results)} result(s)",
                    "items": items,
                    "is_payment": False
                }
            else:
                return {
                    "type": "search_results",
                    "title": f"No results for '{query}'",
                    "content": "Try a different search term.",
                    "items": [],
                    "is_payment": False
                }
        else:
            return {
                "type": "search_form",
                "title": "Search Bluesky",
                "content": "Search for posts and users",
                "items": [],
                "is_payment": False
            }

    # Default
    return {
        "type": "default",
        "title": "Bluesky Social",
        "content": f"PDS: {pds_url}",
        "items": [],
        "is_payment": False
    }


def handle_action(action, params, year):
    """Legacy handler - kept for compatibility. Returns structured data via handle_action_data."""
    return handle_action_data(action, params, year)
