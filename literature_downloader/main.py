"""CLI entry point for starting the local API service."""

from .api import app


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)


if __name__ == "__main__":
    main()
