import sys
import asyncio
import uvicorn

# MUST happen before uvicorn starts
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if __name__ == "__main__":
    uvicorn.run(
        "api_server:app",
        host="127.0.0.1",
        port=8010,
        reload=False,   # IMPORTANT: keep False on Windows during this test
        log_level="info",
    )