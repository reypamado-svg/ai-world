"""Read-only observer support. The simulation engine never imports this package."""

TOKEN_ENV = "SOVEREIGN_WORLD_OBSERVER_TOKEN"
"""Where the observer's own environment may give its token (never passed to a runner)."""
VIEWER_TOKEN_ENV = "SOVEREIGN_WORLD_VIEWER_TOKEN"
"""Where a public observer's environment may give the viewers' token (never passed to a
runner)."""
