"""Serve the packaged Schemii frontend from the unified application."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def install_schemii_frontend(application: FastAPI) -> None:
    """Mount Schemii's buildless web assets and root document."""
    web_directory = Path(__file__).resolve().parent / "web"
    index_file = web_directory / "index.html"
    assets_directory = web_directory / "assets"
    if not index_file.is_file() or not assets_directory.is_dir():
        raise RuntimeError("Packaged Schemii frontend assets are unavailable")

    application.mount(
        "/assets",
        StaticFiles(directory=assets_directory),
        name="schemii-assets",
    )

    @application.api_route("/", methods=("GET", "HEAD"), include_in_schema=False)
    async def schemii_frontend() -> FileResponse:
        return FileResponse(index_file, media_type="text/html")
