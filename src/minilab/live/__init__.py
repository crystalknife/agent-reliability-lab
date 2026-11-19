"""Live run observability: event sink -> sanitized SQLite store -> local HTTP/SSE API.

Standard library only. Local use only: the server binds to loopback and has
no authentication. See docs/live-dashboard.md.
"""
