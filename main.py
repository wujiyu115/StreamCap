import argparse
import os

import uvicorn
from dotenv import load_dotenv

from app.server.app import create_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6006


def main() -> None:
    load_dotenv()

    default_host = os.getenv("HOST", DEFAULT_HOST)
    default_port = int(os.getenv("PORT", DEFAULT_PORT))

    parser = argparse.ArgumentParser(description="Run the StreamCap server.")
    parser.add_argument("--host", type=str, default=default_host, help=f"Host address (default: {default_host})")
    parser.add_argument("--port", type=int, default=default_port, help=f"Port number (default: {default_port})")
    args = parser.parse_args()

    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
