"""Schemoo semantic modeling frontend on the shared application server."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def install_schemoo_frontend(application: FastAPI) -> None:
    web = Path(__file__).resolve().parent / "web"
    application.mount("/schemoo-assets", StaticFiles(directory=web), name="schemoo-assets")

    @application.api_route("/schemoo", methods=("GET", "HEAD"), include_in_schema=False)
    async def model_page() -> FileResponse:
        return FileResponse(web / "index.html", media_type="text/html")
