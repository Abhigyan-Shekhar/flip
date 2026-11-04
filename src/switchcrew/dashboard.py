"""Self-contained same-origin dashboard; no templates need remote services."""

from importlib.resources import files

DASHBOARD_PAGE = (
    files("switchcrew").joinpath("templates/dashboard.html").read_text(encoding="utf-8")
)
