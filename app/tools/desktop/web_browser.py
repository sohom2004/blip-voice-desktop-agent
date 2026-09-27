"""Web browser navigation and search utilities."""

from __future__ import annotations

import webbrowser


def open_url_or_search(query_or_url: str) -> str:
    """Open a website URL or perform a web search in the user's default browser."""
    query_clean = query_or_url.strip()
    if query_clean.startswith("http://") or query_clean.startswith("https://"):
        webbrowser.open(query_clean)
        return f"Opened web URL: {query_clean}"
    elif "." in query_clean and " " not in query_clean:
        url = "https://" + query_clean
        webbrowser.open(url)
        return f"Opened web URL: {url}"
    else:
        url = f"https://www.google.com/search?q={query_clean.replace(' ', '+')}"
        webbrowser.open(url)
        return f"Searched the web for: '{query_clean}'"
