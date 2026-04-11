"""Data ingestion pipeline for NYC Hackathon RAG system.

Downloads NYC Open Data datasets and ingests them into a local vector database
for retrieval-augmented generation. Designed to run on DGX Spark / RTX 4090.

Usage:
    python -m hackathon_nyc.ingest --datasets flood_events flood_sensors 311_current
    python -m hackathon_nyc.ingest --all
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

import aiohttp

from hackathon_nyc.tools.nyc_opendata import NYC_OPENDATA_BASE, DATASETS


DATA_DIR = Path(__file__).parent.parent.parent / "data"


async def download_dataset(dataset_key: str, limit: int = 10000) -> Path:
    """Download a dataset from NYC Open Data and save as JSON."""
    dataset_id = DATASETS.get(dataset_key)
    if not dataset_id:
        print(f"Unknown dataset: {dataset_key}")
        return None

    output_path = DATA_DIR / f"{dataset_key}.json"
    if output_path.exists():
        print(f"  Already exists: {output_path}")
        return output_path

    url = f"{NYC_OPENDATA_BASE}/{dataset_id}.json"
    params = {"$limit": str(limit)}

    print(f"  Downloading {dataset_key} ({dataset_id})...")
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                print(f"  ERROR: {resp.status} for {dataset_key}")
                return None
            data = await resp.json()
            output_path.write_text(json.dumps(data, indent=2, default=str))
            print(f"  Saved {len(data)} records to {output_path}")
            return output_path


def chunk_records(records: list[dict], chunk_size: int = 5) -> list[str]:
    """Convert dataset records into text chunks for embedding.

    Each chunk contains chunk_size records serialized as readable text.
    """
    chunks = []
    for i in range(0, len(records), chunk_size):
        batch = records[i : i + chunk_size]
        text_parts = []
        for record in batch:
            parts = [f"{k}: {v}" for k, v in record.items() if v]
            text_parts.append(" | ".join(parts))
        chunks.append("\n---\n".join(text_parts))
    return chunks


async def ingest_to_chromadb(dataset_key: str, records: list[dict]):
    """Ingest records into ChromaDB for RAG retrieval.

    Requires: pip install chromadb
    ChromaDB runs embedded (no server needed) - perfect for on-device hackathon.
    """
    try:
        import chromadb
    except ImportError:
        print("  ChromaDB not installed. Run: pip install chromadb")
        return

    client = chromadb.PersistentClient(path=str(DATA_DIR / "chromadb"))
    collection = client.get_or_create_collection(
        name=f"nyc_{dataset_key}",
        metadata={"description": f"NYC Open Data - {dataset_key}"},
    )

    chunks = chunk_records(records)
    ids = [f"{dataset_key}_{i}" for i in range(len(chunks))]

    # ChromaDB handles embedding internally with its default model
    collection.upsert(documents=chunks, ids=ids)
    print(f"  Ingested {len(chunks)} chunks into ChromaDB collection 'nyc_{dataset_key}'")


async def main():
    parser = argparse.ArgumentParser(description="Ingest NYC Open Data for RAG")
    parser.add_argument("--datasets", nargs="+", default=[], help="Dataset keys to ingest")
    parser.add_argument("--all", action="store_true", help="Ingest all datasets")
    parser.add_argument("--limit", type=int, default=10000, help="Max records per dataset")
    parser.add_argument("--skip-vectordb", action="store_true", help="Download only, skip ChromaDB")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    datasets = list(DATASETS.keys()) if args.all else args.datasets
    if not datasets:
        print("No datasets specified. Use --datasets or --all")
        print(f"Available: {list(DATASETS.keys())}")
        return

    print(f"Ingesting {len(datasets)} datasets...")
    for key in datasets:
        print(f"\n[{key}]")
        path = await download_dataset(key, limit=args.limit)
        if path and not args.skip_vectordb:
            data = json.loads(path.read_text())
            await ingest_to_chromadb(key, data)

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
