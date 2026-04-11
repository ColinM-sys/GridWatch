import asyncio, sys
sys.path.insert(0, "src")
from hackathon_nyc.tools.historical_lookup import historical_lookup

async def main():
    r = await historical_lookup("rat complaints bushwick", k=3)
    print(len(r.get("results", [])), "results")
    for c in r.get("results", [])[:5]:
        print(c["collection"], "-", c["text"][:200])

asyncio.run(main())
