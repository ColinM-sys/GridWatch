"""
NYC Incident Correlation Analysis

Analyzes spatial correlations between different incident types:
  - Potholes vs Vehicle Crashes
  - Rodent Activity vs Housing Violations (Class C)
  - Noise Complaints vs Rodent Activity
  - Potholes vs Noise Complaints
  - Flooding/Sewer vs Housing Violations
  - Flooding/Sewer vs Rodent Activity

Uses haversine distance and compares actual proximity against a random baseline
to determine statistically meaningful correlations.

Run: python correlation_analysis.py
"""

import asyncio
import aiohttp
import math
import random
import sys
from collections import defaultdict

# --- Configuration ---
OUTPUT_FILE = r"C:\Users\cmcdo\Desktop\hackathon-nyc-v2-3d\correlation_results.txt"
RADII_KM = [0.1, 0.25, 0.5, 1.0]
NUM_RANDOM_POINTS = 500  # Random baseline sample size
RANDOM_SEED = 42

# NYC bounding box for random point generation
NYC_LAT_MIN = 40.49
NYC_LAT_MAX = 40.92
NYC_LNG_MIN = -74.27
NYC_LNG_MAX = -73.68

# --- Data Sources ---
DATASETS = {
    "Potholes": {
        "url": "https://data.cityofnewyork.us/resource/x9wy-ing4.json",
        "params": {"$limit": 2000, "$order": "rptdate DESC"},
        "lat_field": None,  # Special handling: the_geom
        "lng_field": None,
        "geom_field": "the_geom",
    },
    "Vehicle Crashes": {
        "url": "https://data.cityofnewyork.us/resource/h9gi-nx95.json",
        "params": {
            "$limit": 2000,
            "$order": "crash_date DESC",
            "$where": "crash_date>='2025-01-01' AND latitude IS NOT NULL",
        },
        "lat_field": "latitude",
        "lng_field": "longitude",
    },
    "Rodent Activity": {
        "url": "https://data.cityofnewyork.us/resource/p937-wjvj.json",
        "params": {
            "$limit": 2000,
            "$order": "inspection_date DESC",
            "$where": "result in('Rat Activity') AND latitude IS NOT NULL",
        },
        "lat_field": "latitude",
        "lng_field": "longitude",
    },
    "Housing Violations (Class C)": {
        "url": "https://data.cityofnewyork.us/resource/wvxf-dwi5.json",
        "params": {
            "$limit": 2000,
            "$order": "inspectiondate DESC",
            "$where": "class='C' AND latitude IS NOT NULL",
        },
        "lat_field": "latitude",
        "lng_field": "longitude",
    },
    "Noise (Residential)": {
        "url": "https://data.cityofnewyork.us/resource/erm2-nwe9.json",
        "params": {
            "$limit": 2000,
            "$order": "created_date DESC",
            "$where": "complaint_type='Noise - Residential' AND latitude IS NOT NULL",
        },
        "lat_field": "latitude",
        "lng_field": "longitude",
    },
    "Sewer/Flooding": {
        "url": "https://data.cityofnewyork.us/resource/erm2-nwe9.json",
        "params": {
            "$limit": 2000,
            "$order": "created_date DESC",
            "$where": "complaint_type='Sewer' AND latitude IS NOT NULL",
        },
        "lat_field": "latitude",
        "lng_field": "longitude",
    },
}

# Which pairs to analyze
CORRELATION_PAIRS = [
    ("Potholes", "Vehicle Crashes"),
    ("Rodent Activity", "Housing Violations (Class C)"),
    ("Noise (Residential)", "Rodent Activity"),
    ("Potholes", "Noise (Residential)"),
    ("Sewer/Flooding", "Housing Violations (Class C)"),
    ("Sewer/Flooding", "Rodent Activity"),
    ("Noise (Residential)", "Housing Violations (Class C)"),
    ("Potholes", "Sewer/Flooding"),
    ("Vehicle Crashes", "Noise (Residential)"),
    ("Vehicle Crashes", "Rodent Activity"),
]

# Dispatcher insights templates
INSIGHT_TEMPLATES = {
    ("Potholes", "Vehicle Crashes"): "Areas with pothole clusters should be flagged for traffic safety — crashes concentrate near road damage.",
    ("Rodent Activity", "Housing Violations (Class C)"): "Rat activity clusters near buildings with serious housing violations — coordinated inspections recommended.",
    ("Noise (Residential)", "Rodent Activity"): "Noise complaint hotspots overlap with rodent activity — may indicate building maintenance issues.",
    ("Potholes", "Noise (Residential)"): "Pothole-heavy areas also generate noise complaints — possible construction or traffic congestion link.",
    ("Sewer/Flooding", "Housing Violations (Class C)"): "Sewer/flooding reports near housing violations suggest infrastructure-linked building deterioration.",
    ("Sewer/Flooding", "Rodent Activity"): "Sewer issues attract rodent activity — fixing drainage may reduce pest problems.",
    ("Noise (Residential)", "Housing Violations (Class C)"): "Noise complaints cluster near Class C housing violations — both signal distressed buildings.",
    ("Potholes", "Sewer/Flooding"): "Potholes and sewer issues co-occur — shared root cause of aging infrastructure.",
    ("Vehicle Crashes", "Noise (Residential)"): "Crash-prone areas generate noise complaints — likely high-traffic, high-density zones.",
    ("Vehicle Crashes", "Rodent Activity"): "Crashes and rodent sightings cluster in the same neighborhoods — dense urban corridors.",
}


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate the haversine distance between two points in kilometers."""
    R = 6371.0  # Earth's radius in km
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def extract_coordinates(record: dict, config: dict) -> tuple | None:
    """Extract (lat, lng) from a record based on dataset config."""
    # Special handling for pothole data with the_geom (MultiLineString)
    geom_field = config.get("geom_field")
    if geom_field:
        try:
            geom = record.get(geom_field)
            if geom and "coordinates" in geom:
                coords = geom["coordinates"]
                # MultiLineString: coordinates[line_index][point_index] = [lng, lat]
                if isinstance(coords, list) and len(coords) > 0:
                    line = coords[0]
                    if isinstance(line, list) and len(line) > 0:
                        point = line[0]
                        if isinstance(point, list) and len(point) >= 2:
                            lng, lat = float(point[0]), float(point[1])
                            if NYC_LAT_MIN <= lat <= NYC_LAT_MAX and NYC_LNG_MIN <= lng <= NYC_LNG_MAX:
                                return (lat, lng)
            return None
        except (TypeError, ValueError, IndexError):
            return None

    # Standard lat/lng fields
    lat_field = config.get("lat_field")
    lng_field = config.get("lng_field")
    if not lat_field or not lng_field:
        return None

    try:
        lat = float(record[lat_field])
        lng = float(record[lng_field])
        if NYC_LAT_MIN <= lat <= NYC_LAT_MAX and NYC_LNG_MIN <= lng <= NYC_LNG_MAX:
            return (lat, lng)
        return None
    except (KeyError, ValueError, TypeError):
        return None


async def fetch_dataset(session: aiohttp.ClientSession, name: str, config: dict) -> list:
    """Fetch a dataset and return list of (lat, lng) tuples."""
    url = config["url"]
    params = config.get("params", {})

    print(f"  Fetching {name}...")

    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"    ERROR: {resp.status} - {text[:200]}")
                return []
            records = await resp.json()
    except Exception as e:
        print(f"    ERROR fetching {name}: {e}")
        return []

    points = []
    for r in records:
        coord = extract_coordinates(r, config)
        if coord:
            points.append(coord)

    print(f"    Got {len(points)} valid points (from {len(records)} records)")
    return points


def generate_random_nyc_points(n: int) -> list:
    """Generate random points within NYC bounding box."""
    rng = random.Random(RANDOM_SEED)
    points = []
    for _ in range(n):
        lat = rng.uniform(NYC_LAT_MIN, NYC_LAT_MAX)
        lng = rng.uniform(NYC_LNG_MIN, NYC_LNG_MAX)
        points.append((lat, lng))
    return points


def count_nearby(point: tuple, target_points: list, radius_km: float) -> int:
    """Count how many target points are within radius_km of the given point."""
    lat1, lng1 = point
    count = 0
    for lat2, lng2 in target_points:
        if haversine_km(lat1, lng1, lat2, lng2) <= radius_km:
            count += 1
    return count


def analyze_correlation(
    name_a: str,
    points_a: list,
    name_b: str,
    points_b: list,
    random_points: list,
) -> dict:
    """
    Analyze spatial correlation between two incident types.
    For each point in A, count nearby points in B at various radii.
    Compare against random baseline.
    """
    # Use a sample if there are too many points (for performance)
    sample_a = points_a if len(points_a) <= 500 else random.sample(points_a, 500)
    sample_random = random_points if len(random_points) <= 500 else random.sample(random_points, 500)

    results_by_radius = {}

    for radius in RADII_KM:
        # Actual: count B near each A
        actual_counts = [count_nearby(p, points_b, radius) for p in sample_a]
        actual_avg = sum(actual_counts) / len(actual_counts) if actual_counts else 0

        # Random baseline: count B near random points
        random_counts = [count_nearby(p, points_b, radius) for p in sample_random]
        random_avg = sum(random_counts) / len(random_counts) if random_counts else 0

        ratio = actual_avg / random_avg if random_avg > 0 else (float("inf") if actual_avg > 0 else 1.0)

        results_by_radius[radius] = {
            "actual_avg": actual_avg,
            "random_avg": random_avg,
            "ratio": ratio,
            "actual_max": max(actual_counts) if actual_counts else 0,
        }

    # Use 0.25 km as the "headline" radius
    headline = results_by_radius.get(0.25, results_by_radius.get(0.5, {}))

    return {
        "name_a": name_a,
        "name_b": name_b,
        "count_a": len(points_a),
        "count_b": len(points_b),
        "by_radius": results_by_radius,
        "headline_ratio": headline.get("ratio", 1.0),
        "headline_actual": headline.get("actual_avg", 0),
        "headline_random": headline.get("random_avg", 0),
    }


def classify_correlation(ratio: float) -> tuple:
    """Classify correlation strength. Returns (label, emoji)."""
    if ratio >= 3.0:
        return ("VERY STRONG", "[!!!]")
    elif ratio >= 2.0:
        return ("STRONG", "[!!]")
    elif ratio >= 1.5:
        return ("MODERATE", "[!]")
    else:
        return ("NO SIGNIFICANT", "[ ]")


def format_results(all_results: list) -> str:
    """Format all correlation results into a clear summary."""
    lines = []
    lines.append("=" * 70)
    lines.append("       NYC INCIDENT CORRELATION ANALYSIS")
    lines.append("=" * 70)
    lines.append("")

    # Sort by headline ratio descending
    all_results.sort(key=lambda x: x["headline_ratio"], reverse=True)

    # Group by strength
    strong = [r for r in all_results if r["headline_ratio"] >= 2.0]
    moderate = [r for r in all_results if 1.5 <= r["headline_ratio"] < 2.0]
    weak = [r for r in all_results if r["headline_ratio"] < 1.5]

    # Strong correlations
    if strong:
        lines.append("-" * 70)
        lines.append("[!!] STRONG CORRELATIONS (ratio > 2.0x)")
        lines.append("-" * 70)
        lines.append("")
        for r in strong:
            label, _ = classify_correlation(r["headline_ratio"])
            lines.append(f"  {r['name_a']} <-> {r['name_b']}: {r['headline_ratio']:.1f}x correlation")
            lines.append(f"    Data: {r['count_a']} {r['name_a']} points, {r['count_b']} {r['name_b']} points")
            lines.append(f"    Avg {r['name_b']} within 0.25 km of {r['name_a']}: {r['headline_actual']:.2f}")
            lines.append(f"    Avg {r['name_b']} within 0.25 km of random point: {r['headline_random']:.2f}")

            # Show all radii
            lines.append(f"    By distance:")
            for radius in RADII_KM:
                rd = r["by_radius"][radius]
                lines.append(f"      {radius} km: actual={rd['actual_avg']:.2f}, random={rd['random_avg']:.2f}, ratio={rd['ratio']:.1f}x")

            # Insight
            pair_key = (r["name_a"], r["name_b"])
            reverse_key = (r["name_b"], r["name_a"])
            insight = INSIGHT_TEMPLATES.get(pair_key) or INSIGHT_TEMPLATES.get(reverse_key, "Spatial clustering detected between these incident types.")
            lines.append(f"    -> FINDING: {insight}")
            lines.append("")

    # Moderate correlations
    if moderate:
        lines.append("-" * 70)
        lines.append("[!] MODERATE CORRELATIONS (ratio 1.5x - 2.0x)")
        lines.append("-" * 70)
        lines.append("")
        for r in moderate:
            lines.append(f"  {r['name_a']} <-> {r['name_b']}: {r['headline_ratio']:.1f}x correlation")
            lines.append(f"    Avg {r['name_b']} within 0.25 km of {r['name_a']}: {r['headline_actual']:.2f}")
            lines.append(f"    Avg {r['name_b']} within 0.25 km of random point: {r['headline_random']:.2f}")

            pair_key = (r["name_a"], r["name_b"])
            reverse_key = (r["name_b"], r["name_a"])
            insight = INSIGHT_TEMPLATES.get(pair_key) or INSIGHT_TEMPLATES.get(reverse_key, "Moderate spatial clustering detected.")
            lines.append(f"    -> FINDING: {insight}")
            lines.append("")

    # Weak / no correlation
    if weak:
        lines.append("-" * 70)
        lines.append("[ ] NO SIGNIFICANT CORRELATION (ratio < 1.5x)")
        lines.append("-" * 70)
        lines.append("")
        for r in weak:
            lines.append(f"  {r['name_a']} <-> {r['name_b']}: {r['headline_ratio']:.1f}x (near random)")
        lines.append("")

    # Key insights for dispatchers
    lines.append("=" * 70)
    lines.append("  KEY INSIGHTS FOR DISPATCHERS")
    lines.append("=" * 70)
    lines.append("")

    insight_num = 1
    for r in strong:
        pair_key = (r["name_a"], r["name_b"])
        reverse_key = (r["name_b"], r["name_a"])
        insight = INSIGHT_TEMPLATES.get(pair_key) or INSIGHT_TEMPLATES.get(reverse_key, "")
        if insight:
            lines.append(f"  {insight_num}. {insight}")
            insight_num += 1

    for r in moderate:
        pair_key = (r["name_a"], r["name_b"])
        reverse_key = (r["name_b"], r["name_a"])
        insight = INSIGHT_TEMPLATES.get(pair_key) or INSIGHT_TEMPLATES.get(reverse_key, "")
        if insight:
            lines.append(f"  {insight_num}. {insight}")
            insight_num += 1

    if not strong and not moderate:
        lines.append("  No strong spatial correlations detected in this dataset.")

    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  Analysis complete. {len(all_results)} pairs evaluated.")
    lines.append("=" * 70)

    return "\n".join(lines)


async def main():
    print("=" * 70)
    print("  NYC Incident Correlation Analysis")
    print("  Analyzing spatial relationships between incident types")
    print("=" * 70)
    print()

    async with aiohttp.ClientSession() as session:
        # Step 1: Fetch all datasets
        print("[1/3] Fetching datasets from NYC Open Data...")
        all_points = {}
        for name, config in DATASETS.items():
            points = await fetch_dataset(session, name, config)
            all_points[name] = points

    # Verify we have data
    empty = [name for name, pts in all_points.items() if not pts]
    if empty:
        print(f"\n  WARNING: No data for: {', '.join(empty)}")
    if all(len(pts) == 0 for pts in all_points.values()):
        print("ERROR: No data fetched for any dataset. Exiting.")
        sys.exit(1)

    # Step 2: Generate random baseline
    print()
    print("[2/3] Generating random NYC baseline points...")
    random_points = generate_random_nyc_points(NUM_RANDOM_POINTS)
    print(f"  Generated {NUM_RANDOM_POINTS} random points within NYC bounds")

    # Step 3: Analyze correlations
    print()
    print("[3/3] Analyzing spatial correlations (this may take a few minutes)...")
    all_results = []

    for name_a, name_b in CORRELATION_PAIRS:
        points_a = all_points.get(name_a, [])
        points_b = all_points.get(name_b, [])

        if not points_a or not points_b:
            print(f"  Skipping {name_a} <-> {name_b} (missing data)")
            continue

        print(f"  Analyzing: {name_a} <-> {name_b} ({len(points_a)} x {len(points_b)} points)...")
        result = analyze_correlation(name_a, points_a, name_b, points_b, random_points)
        all_results.append(result)
        label, marker = classify_correlation(result["headline_ratio"])
        print(f"    Result: {result['headline_ratio']:.1f}x ({label})")

    # Format and print results
    print()
    output = format_results(all_results)
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
