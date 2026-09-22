"""Install frontend primitives shared by every product."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


# This exact document is repeated in the static HTML entrypoints. Tests verify
# equality so the strict CSP can authorize only this import map by its digest.
COMMON_IMPORT_MAP = '{"imports":{"#common/":"/assets/common/"}}'


def install_common_frontend(application: FastAPI) -> None:
    """Mount before product assets so the more specific shared prefix wins."""
    assets = Path(__file__).resolve().parent / "web" / "assets"
    if not assets.is_dir():
        raise RuntimeError("Packaged common frontend assets are unavailable")
    application.mount("/assets/common", StaticFiles(directory=assets), name="common-assets")

    async def account_page() -> FileResponse:
        return FileResponse(assets / "accounts.html", headers={"Cache-Control": "no-store"})

    for route in ("/login", "/account", "/admin"):
        application.add_api_route(route, account_page, methods=["GET", "HEAD"], include_in_schema=False)
