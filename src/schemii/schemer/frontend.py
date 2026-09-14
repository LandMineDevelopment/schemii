"""Schemer report studio on the unified application origin."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

SCHEMER_IMPORT_MAP = '{"imports":{"#common/":"/assets/common/","#model/":"/schemoo-assets/"}}'


def install_schemer_frontend(application: FastAPI) -> None:
    web = Path(__file__).resolve().parent / "web"
    application.mount("/schemer-assets", StaticFiles(directory=web), name="schemer-assets")

    @application.api_route("/schemer", methods=("GET", "HEAD"), include_in_schema=False)
    async def report_page() -> FileResponse:
        return FileResponse(web / "index.html", media_type="text/html")
