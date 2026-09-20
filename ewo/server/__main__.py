"""``python -m ewo.server`` — run the server with host/port from config.

uvicorn is imported lazily here and only here: it is deliberately NOT a
package dependency (installed in the Docker image, or manually in a dev venv).
"""

from __future__ import annotations

from ewo.config import load_config


def main() -> None:
    import uvicorn

    config = load_config()
    uvicorn.run(
        "ewo.server.app:create_app",
        factory=True,
        host=config.server.host,
        port=config.server.port,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
