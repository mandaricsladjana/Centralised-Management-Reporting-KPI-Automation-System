"""Azure Blob Storage: raw file upload and archive (blueprint §2, settings [storage]).

Only used with `python -m etl.pipeline --upload-blob`. Off by default because it
sends files to the cloud; local runs read data/raw directly.
Layout: <container>/<source_name>/<yyyy>/<mm>/<file_name>
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path


def blob_path(source_name: str, file_name: str, when: dt.date) -> str:
    return f"{source_name}/{when:%Y}/{when:%m}/{file_name}"


class BlobArchive:
    def __init__(self, settings: dict):
        from azure.storage.blob import BlobServiceClient          # lazy: only needed with --upload-blob
        conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
        if not conn:
            raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not set in .env")
        self.client = BlobServiceClient.from_connection_string(conn)
        self.raw = settings["storage"]["container_raw"]
        self.archive = settings["storage"]["container_archive"]

    def upload_raw(self, path: Path, source_name: str, when: dt.date) -> str:
        name = blob_path(source_name, path.name, when)
        with path.open("rb") as fh:
            self.client.get_blob_client(self.raw, name).upload_blob(fh, overwrite=True)
        return f"{self.raw}/{name}"

    def archive_loaded(self, source_name: str, file_name: str, when: dt.date) -> None:
        """Copy a loaded file to the archive container (kept forever: rebuild from raw)."""
        name = blob_path(source_name, file_name, when)
        src = self.client.get_blob_client(self.raw, name)
        self.client.get_blob_client(self.archive, name).start_copy_from_url(src.url)
