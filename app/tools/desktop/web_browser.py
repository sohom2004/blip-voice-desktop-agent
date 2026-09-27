"""Web browser navigation and search utilities."""

from __future__ import annotations

import webbrowser


def open_url_or_search(query_or_url: str) -> str:
    """Open a website URL or perform a web search in the user's default browser."""
    import time
    from app.tools.desktop.window_manager import window_manager

    query_clean = query_or_url.strip()
    if query_clean.startswith("http://") or query_clean.startswith("https://"):
        target_url = query_clean
        action_msg = f"Opened web URL: {query_clean}"
    elif "." in query_clean and " " not in query_clean:
        target_url = "https://" + query_clean
        action_msg = f"Opened web URL: {target_url}"
    else:
        target_url = f"https://www.google.com/search?q={query_clean.replace(' ', '+')}"
        action_msg = f"Searched the web for: '{query_clean}'"

    webbrowser.open(target_url)

    # Allow browser to process URL and ensure it's in foreground
    time.sleep(0.6)
    for b in ("chrome", "brave", "msedge", "edge", "firefox"):
        win = window_manager.find_window(b)
        if win:
            window_manager.bring_to_front(win.hwnd)
            break

    return action_msg

