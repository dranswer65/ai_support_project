# run_dev.py (Windows-safe for psycopg async)
import sys
import asyncio

if sys.platform.startswith("win"):
    import selectors
    # Force selector loop (psycopg async requires this on Windows)
    selector = selectors.SelectSelector()
    loop = asyncio.SelectorEventLoop(selector)
    asyncio.set_event_loop(loop)

import uvicorn


def main():
    config = uvicorn.Config(
        "api_server:app",
        host="127.0.0.1",
        port=8010,
        reload=False,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(server.serve())


if __name__ == "__main__":
    main()