"""
Backtest Predictions: Validates whether historical 311 patterns can predict future incidents.

Training data: Jan 1 - Feb 28, 2026
Test data: Mar 1 - Mar 15, 2026

Uses NYC Open Data SODA API to fetch 311 complaints, builds a grid-based prediction
model, and measures hit rate against the test period.

Run: python backtest_predictions.py
"""

import asyncio
import aiohttp
import math
import sys
from collections import defaultdict
from datetime import datetime

# --- Configuration ---
SODA_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
BATCH_SIZE = 5000
GRID_LAT_SIZE = 0.005  # ~500m
GRID_LNG_SIZE = 0.005  # ~400m
MIN_PATTERN_COUNT = 2  # Minimum occurrences in training to count as a pattern
OUTPUT_FILE = r"C:\Users\cmcdo\Desktop\hackathon-nyc-v2-3d\backtest_results.txt"


def lat_lng_to_grid(lat: float, lng: float) -> tuple:
    """Convert lat/lng to a grid cell identifier."""
    grid_lat = round(math.floor(lat / GRID_LAT_SIZE) * GRID_LAT_SIZE, 4)
    grid_lng = round(math.floor(lng / GRID_LNG_SIZE) * GRID_LNG_SIZE, 4)
    return (grid_lat, grid_lng)


def get_neighboring_cells(cell: tuple) -> list:
    """Get a cell and its 8 immediate neighbors."""
    lat, lng = cell
    neighbors = []
    for dlat in [-GRID_LAT_SIZE, 0, GRID_LAT_SIZE]:
        for dlng in [-GRID_LNG_SIZE, 0, GRID_LNG_SIZE]:
            neighbors.append((round(lat + dlat, 4), round(lng + dlng, 4)))
    return neighbors


async def fetch_311_data(session: aiohttp.ClientSession, start_date: str, end_date: str, label: str) -> list:
    """Fetch 311 complaints from NYC Open Data in batches."""
    all_records = []
    offset = 0

    where_clause = (
        f"created_date >= '{start_date}' AND created_date < '{end_date}' "
        f"AND latitude IS NOT NULL"
    )

    print(f"  Fetching {label} data...")

    while True:
        params = {
            "$where": where_clause,
            "$select": "complaint_type, latitude, longitude, created_date",
            "$limit": BATCH_SIZE,
            "$offset": offset,
            "$order": "created_date ASC",
        }

        try:
            async with session.get(SODA_URL, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"  ERROR fetching at offset {offset}: {resp.status} - {text[:200]}")
                    break
                batch = await resp.json()
        except Exception as e:
            print(f"  ERROR fetching at offset {offset}: {e}")
            break

        if not batch:
            break

        all_records.extend(batch)
        offset += len(batch)
        print(f"    ... fetched {offset} records so far")

        if len(batch) < BATCH_SIZE:
            break

    print(f"  Total {label} records: {len(all_records)}")
    return all_records


def parse_record(record: dict) -> dict | None:
    """Parse a raw API record into a structured dict."""
    try:
        lat = float(record["latitude"])
        lng = float(record["longitude"])
        complaint_type = record.get("complaint_type", "Unknown")
        created = record.get("created_date", "")

        # Parse datetime
        dt = None
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(created[:26], fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None

        return {
            "complaint_type": complaint_type,
            "lat": lat,
            "lng": lng,
            "hour": dt.hour,
            "day_of_week": dt.weekday(),
            "grid_cell": lat_lng_to_grid(lat, lng),
            "datetime": dt,
        }
    except (KeyError, ValueError, TypeError):
        return None


def build_prediction_model(records: list) -> dict:
    """
    Build a grid-based prediction model.
    Structure: grid_cell -> complaint_type -> day_of_week -> hour -> count
    """
    model = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))

    parsed_count = 0
    for r in records:
        parsed = parse_record(r)
        if parsed is None:
            continue
        parsed_count += 1
        cell = parsed["grid_cell"]
        ctype = parsed["complaint_type"]
        dow = parsed["day_of_week"]
        hour = parsed["hour"]
        model[cell][ctype][dow][hour] += 1

    print(f"  Model built from {parsed_count} parsed records across {len(model)} grid cells")
    return model


def generate_predictions(model: dict) -> set:
    """
    Generate predictions: for each cell/type/dow/hour with count >= MIN_PATTERN_COUNT,
    predict that this combination will occur in the test period.
    Returns a set of (cell, complaint_type, day_of_week, hour) tuples.
    """
    predictions = set()
    for cell in model:
        for ctype in model[cell]:
            for dow in model[cell][ctype]:
                for hour in model[cell][ctype][dow]:
                    count = model[cell][ctype][dow][hour]
                    if count >= MIN_PATTERN_COUNT:
                        predictions.add((cell, ctype, dow, hour))
    return predictions


def evaluate_predictions(predictions: set, test_records: list, model: dict) -> dict:
    """
    Evaluate predictions against test data.
    A prediction is a hit if there was an actual incident in the same or neighboring cell
    for that complaint_type + day_of_week + hour.
    """
    # Parse test records and index by (cell, ctype, dow, hour)
    test_index = defaultdict(set)  # (ctype, dow, hour) -> set of cells
    test_parsed = []

    for r in test_records:
        parsed = parse_record(r)
        if parsed is None:
            continue
        test_parsed.append(parsed)
        key = (parsed["complaint_type"], parsed["day_of_week"], parsed["hour"])
        test_index[key].add(parsed["grid_cell"])

    print(f"  Test set: {len(test_parsed)} parsed records")

    # Check each prediction
    hits = 0
    misses = 0
    hits_by_type = defaultdict(int)
    predictions_by_type = defaultdict(int)
    hits_by_location = defaultdict(int)
    predictions_by_location = defaultdict(int)

    for cell, ctype, dow, hour in predictions:
        key = (ctype, dow, hour)
        predictions_by_type[ctype] += 1
        predictions_by_location[cell] += 1

        # Check if any test incident occurred in this cell or neighbors
        actual_cells = test_index.get(key, set())
        neighbors = get_neighboring_cells(cell)

        hit = False
        for neighbor in neighbors:
            if neighbor in actual_cells:
                hit = True
                break

        if hit:
            hits += 1
            hits_by_type[ctype] += 1
            hits_by_location[cell] += 1
        else:
            misses += 1

    # Count actual test incidents that were NOT predicted (for false negative analysis)
    test_combos = set()
    for parsed in test_parsed:
        test_combos.add((parsed["grid_cell"], parsed["complaint_type"], parsed["day_of_week"], parsed["hour"]))

    unpredicted = 0
    for cell, ctype, dow, hour in test_combos:
        neighbors = get_neighboring_cells(cell)
        was_predicted = False
        for neighbor in neighbors:
            if (neighbor, ctype, dow, hour) in predictions:
                was_predicted = True
                break
        if not was_predicted:
            unpredicted += 1

    return {
        "total_predictions": len(predictions),
        "hits": hits,
        "misses": misses,
        "hit_rate": hits / len(predictions) * 100 if predictions else 0,
        "false_positive_rate": misses / len(predictions) * 100 if predictions else 0,
        "hits_by_type": dict(hits_by_type),
        "predictions_by_type": dict(predictions_by_type),
        "hits_by_location": dict(hits_by_location),
        "predictions_by_location": dict(predictions_by_location),
        "total_test_incidents": len(test_parsed),
        "total_test_combos": len(test_combos),
        "unpredicted_combos": unpredicted,
    }


def format_results(results: dict, training_count: int, test_count: int) -> str:
    """Format results into a clear summary string."""
    lines = []
    lines.append("=" * 60)
    lines.append("      PREDICTION BACKTEST RESULTS")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Training period: Jan 1 - Feb 28, 2026 ({training_count:,} complaints)")
    lines.append(f"Testing period:  Mar 1 - Mar 15, 2026 ({test_count:,} complaints)")
    lines.append(f"Grid cell size:  {GRID_LAT_SIZE} lat x {GRID_LNG_SIZE} lng (~500m x 400m)")
    lines.append(f"Pattern threshold: >= {MIN_PATTERN_COUNT} occurrences in training")
    lines.append("")
    lines.append("-" * 60)
    lines.append("OVERALL METRICS")
    lines.append("-" * 60)
    lines.append(f"  Total predictions generated: {results['total_predictions']:,}")
    lines.append(f"  Predictions that matched (hits): {results['hits']:,}")
    lines.append(f"  Predictions that missed: {results['misses']:,}")
    lines.append(f"  Overall hit rate: {results['hit_rate']:.1f}%")
    lines.append(f"  False positive rate: {results['false_positive_rate']:.1f}%")
    lines.append(f"  Test incidents not predicted: {results['unpredicted_combos']:,} / {results['total_test_combos']:,} unique combos")
    lines.append("")

    # By complaint type
    lines.append("-" * 60)
    lines.append("HIT RATE BY COMPLAINT TYPE")
    lines.append("-" * 60)

    type_rates = []
    for ctype, pred_count in results["predictions_by_type"].items():
        hit_count = results["hits_by_type"].get(ctype, 0)
        rate = hit_count / pred_count * 100 if pred_count > 0 else 0
        type_rates.append((ctype, rate, hit_count, pred_count))

    # Sort by hit rate descending
    type_rates.sort(key=lambda x: x[1], reverse=True)

    lines.append("")
    lines.append("  Top 10 most predictable complaint types:")
    for i, (ctype, rate, hit_count, pred_count) in enumerate(type_rates[:10]):
        strength = "strongest" if i == 0 else ""
        lines.append(f"    {ctype}: {rate:.1f}% ({hit_count}/{pred_count} predictions hit) {strength}")

    if len(type_rates) > 10:
        lines.append("")
        lines.append("  Bottom 5 (least predictable):")
        for ctype, rate, hit_count, pred_count in type_rates[-5:]:
            lines.append(f"    {ctype}: {rate:.1f}% ({hit_count}/{pred_count})")

    lines.append("")

    # By location
    lines.append("-" * 60)
    lines.append("TOP 10 MOST PREDICTABLE LOCATIONS")
    lines.append("-" * 60)

    loc_rates = []
    for cell, pred_count in results["predictions_by_location"].items():
        if pred_count < 5:  # Only show locations with enough predictions
            continue
        hit_count = results["hits_by_location"].get(cell, 0)
        rate = hit_count / pred_count * 100 if pred_count > 0 else 0
        loc_rates.append((cell, rate, hit_count, pred_count))

    loc_rates.sort(key=lambda x: x[1], reverse=True)

    lines.append("")
    for cell, rate, hit_count, pred_count in loc_rates[:10]:
        lat, lng = cell
        lines.append(f"    [{lat:.4f}, {lng:.4f}]: {rate:.1f}% hit rate ({hit_count}/{pred_count} predictions)")

    lines.append("")
    lines.append("-" * 60)
    lines.append("KEY INSIGHTS")
    lines.append("-" * 60)
    lines.append("")

    if type_rates:
        best_type = type_rates[0]
        lines.append(f"  1. '{best_type[0]}' is the most predictable complaint type at {best_type[1]:.1f}% hit rate.")

    if len(type_rates) > 1:
        strong_types = [t for t in type_rates if t[1] > 50]
        lines.append(f"  2. {len(strong_types)} complaint types have >50% predictability from historical patterns.")

    if loc_rates:
        high_pred_locs = [l for l in loc_rates if l[1] > 70]
        lines.append(f"  3. {len(high_pred_locs)} grid cells show >70% prediction accuracy (hotspots).")

    lines.append(f"  4. The model generated {results['total_predictions']:,} actionable predictions for a 15-day test window.")
    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


async def main():
    print("=" * 60)
    print("  311 Prediction Backtest")
    print("  Training: Jan-Feb 2026 | Test: Mar 1-15, 2026")
    print("=" * 60)
    print()

    async with aiohttp.ClientSession() as session:
        # Step 1: Fetch training data (Jan-Feb 2026)
        print("[1/5] Fetching training data (Jan 1 - Feb 28, 2026)...")
        training_data = await fetch_311_data(session, "2026-01-01", "2026-03-01", "training")

        if not training_data:
            print("ERROR: No training data fetched. Check API connectivity.")
            sys.exit(1)

        # Step 2: Build prediction model
        print()
        print("[2/5] Building prediction model...")
        model = build_prediction_model(training_data)

        # Step 3: Generate predictions
        print()
        print("[3/5] Generating predictions...")
        predictions = generate_predictions(model)
        print(f"  Generated {len(predictions):,} predictions (cell+type+day+hour combos with >= {MIN_PATTERN_COUNT} training occurrences)")

        # Step 4: Fetch test data (Mar 1-15, 2026)
        print()
        print("[4/5] Fetching test data (Mar 1 - Mar 15, 2026)...")
        test_data = await fetch_311_data(session, "2026-03-01", "2026-03-16", "test")

        if not test_data:
            print("ERROR: No test data fetched. Check API connectivity.")
            sys.exit(1)

        # Step 5: Evaluate
        print()
        print("[5/5] Evaluating predictions against test data...")
        results = evaluate_predictions(predictions, test_data, model)

    # Format and print results
    output = format_results(results, len(training_data), len(test_data))
    print()
    print(output)

    # Save to file
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\nResults saved to: {OUTPUT_FILE}")
    except Exception as e:
        print(f"\nWARNING: Could not save results to file: {e}")


if __name__ == "__main__":
    asyncio.run(main())
