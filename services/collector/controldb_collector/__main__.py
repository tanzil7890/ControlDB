"""Entrypoint: ``python -m controldb_collector``."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("CONTROLDB_HOST", "0.0.0.0")
    port = int(os.environ.get("CONTROLDB_PORT", "8080"))
    uvicorn.run("controldb_collector.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
