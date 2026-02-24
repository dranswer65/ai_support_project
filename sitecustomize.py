import sys
import asyncio

# Force Selector loop on Windows (required for some async drivers)
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

