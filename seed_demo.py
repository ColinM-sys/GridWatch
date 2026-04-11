"""Seed the database with realistic demo incidents for GridWatch.
Run: PYTHONPATH=src python seed_demo.py
"""

import sys
sys.path.insert(0, "src")
from hackathon_nyc import db

DEMO_INCIDENTS = [
    # Flooding
    {"title": "Major flooding on Atlantic Ave", "category": "flooding", "severity": "high", "lat": 40.6862, "lng": -73.9776, "address": "Atlantic Ave & Flatbush Ave, Brooklyn", "borough": "BROOKLYN", "source": "citizen_discord", "confirmed": 1},
    {"title": "Flooding in Astoria", "category": "flooding", "severity": "high", "lat": 40.7721, "lng": -73.9301, "address": "Astoria, Queens", "borough": "QUEENS", "source": "dispatcher", "confirmed": 1},
    {"title": "Water main break at Lexington & 42nd", "category": "flooding", "severity": "critical", "lat": 40.7509, "lng": -73.9786, "address": "Lexington Ave & 42nd St, Manhattan", "borough": "MANHATTAN", "source": "citizen_sms", "confirmed": 1},
    {"title": "Street flooding near Coney Island", "category": "flooding", "severity": "medium", "lat": 40.5749, "lng": -73.9710, "address": "Coney Island, Brooklyn", "borough": "BROOKLYN", "source": "citizen_voice", "confirmed": 1},
    {"title": "Flooding at Rockaway Beach Blvd", "category": "flooding", "severity": "medium", "lat": 40.5799, "lng": -73.8370, "address": "Rockaway Beach Blvd, Queens", "borough": "QUEENS", "source": "citizen_discord", "confirmed": 1},

    # Sewer
    {"title": "Sewer backup on Canal St", "category": "sewer", "severity": "critical", "lat": 40.7177, "lng": -73.9999, "address": "Canal St, Manhattan", "borough": "MANHATTAN", "source": "dispatcher", "confirmed": 1},
    {"title": "Sewer overflow at Atlantic & Flatbush", "category": "sewer", "severity": "high", "lat": 40.6835, "lng": -73.9764, "address": "Atlantic Ave & Flatbush Ave, Brooklyn", "borough": "BROOKLYN", "source": "citizen_sms", "confirmed": 1},

    # Rodent
    {"title": "Rat infestation near subway entrance", "category": "rodent", "severity": "medium", "lat": 40.7484, "lng": -73.9856, "address": "34th St & Broadway, Manhattan", "borough": "MANHATTAN", "source": "citizen_discord", "confirmed": 1},
    {"title": "Rats in building at 200 Broadway", "category": "rodent", "severity": "medium", "lat": 40.7104, "lng": -74.0089, "address": "200 Broadway, Manhattan", "borough": "MANHATTAN", "source": "citizen_discord", "confirmed": 1},
    {"title": "Rodent activity at Grand Concourse", "category": "rodent", "severity": "medium", "lat": 40.8296, "lng": -73.9208, "address": "1000 Grand Concourse, Bronx", "borough": "BRONX", "source": "citizen_sms", "confirmed": 1},

    # Noise
    {"title": "Loud construction at 1 World Trade Center", "category": "noise", "severity": "medium", "lat": 40.7130, "lng": -74.0132, "address": "1 World Trade Center, Manhattan", "borough": "MANHATTAN", "source": "citizen_sms", "confirmed": 0},
    {"title": "Noise complaint at 125 E 14th St", "category": "noise", "severity": "medium", "lat": 40.7341, "lng": -73.9889, "address": "125 E 14th St, Manhattan", "borough": "MANHATTAN", "source": "citizen_discord", "confirmed": 1},
    {"title": "Loud music at 88 Delancey St", "category": "noise", "severity": "low", "lat": 40.7185, "lng": -73.9884, "address": "88 Delancey St, Manhattan", "borough": "MANHATTAN", "source": "citizen_sms", "confirmed": 0},

    # Heat
    {"title": "No heat at 1000 Grand Concourse", "category": "heat", "severity": "high", "lat": 40.8296, "lng": -73.9208, "address": "1000 Grand Concourse, Bronx", "borough": "BRONX", "source": "citizen_discord", "confirmed": 1},

    # Street condition
    {"title": "Pothole on Atlantic Ave & Court St", "category": "street_condition", "severity": "medium", "lat": 40.6895, "lng": -73.9921, "address": "Atlantic Ave & Court St, Brooklyn", "borough": "BROOKLYN", "source": "dispatcher", "confirmed": 0},
    {"title": "Dangerous pothole on FDR Drive", "category": "street_condition", "severity": "high", "lat": 40.7677, "lng": -73.9174, "address": "FDR Drive near 34th St, Manhattan", "borough": "MANHATTAN", "source": "citizen_sms", "confirmed": 1},

    # Tree
    {"title": "Tree fell on Queens Blvd", "category": "tree", "severity": "medium", "lat": 40.7220, "lng": -73.8468, "address": "75-20 Queens Blvd, Queens", "borough": "QUEENS", "source": "citizen_discord", "confirmed": 1},
    {"title": "Large branch blocking sidewalk", "category": "tree", "severity": "low", "lat": 40.7831, "lng": -73.9712, "address": "Central Park West & 79th St, Manhattan", "borough": "MANHATTAN", "source": "dispatcher", "confirmed": 0},

    # Health
    {"title": "Person collapsed near Penn Station", "category": "health", "severity": "critical", "lat": 40.7506, "lng": -73.9935, "address": "Penn Station, Manhattan", "borough": "MANHATTAN", "source": "citizen_photo", "confirmed": 1},
    {"title": "Unresponsive person at Union Square", "category": "health", "severity": "high", "lat": 40.7359, "lng": -73.9911, "address": "Union Square, Manhattan", "borough": "MANHATTAN", "source": "citizen_photo", "confirmed": 0},

    # Other / multi-source
    {"title": "Gas leak at 200 Park Ave", "category": "sewer", "severity": "critical", "lat": 40.7535, "lng": -73.9766, "address": "200 Park Ave, Manhattan", "borough": "MANHATTAN", "source": "citizen_voice", "confirmed": 1},
    {"title": "Fire hydrant broken at Bowery", "category": "water", "severity": "medium", "lat": 40.7251, "lng": -73.9939, "address": "200 Bowery, Manhattan", "borough": "MANHATTAN", "source": "citizen_sms", "confirmed": 1},
]

def seed():
    print(f"Seeding {len(DEMO_INCIDENTS)} demo incidents...")
    for inc in DEMO_INCIDENTS:
        result = db.create_incident(
            title=inc["title"],
            category=inc["category"],
            severity=inc["severity"],
            latitude=inc["lat"],
            longitude=inc["lng"],
            address=inc.get("address", ""),
            borough=inc.get("borough", ""),
            source=inc.get("source", "dispatcher"),
            description=f"Demo incident: {inc['title']}",
        )
        if inc.get("confirmed"):
            db.confirm_incident(result["id"], confirmed_by="demo_seed")
        print(f"  ✓ {inc['title'][:50]} ({inc['category']}, {inc['severity']})")

    stats = db.get_stats()
    print(f"\nDone. Total incidents: {stats['total']}")
    print(f"  Open: {stats['open']}, In Progress: {stats['in_progress']}")

if __name__ == "__main__":
    seed()
