"""Replay Jaipur and equalize two field weights offline."""
import argparse
import json
from pathlib import Path

from app.decision_trace import equal_weight_sensitivity
from careproof.recommend import recommend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=Path("reports/real_data_capture.json"))
    args = parser.parse_args()
    rows = json.loads(args.capture.read_text())["rows"]
    query = {"service": "dialysis", "location": {"city": "Jaipur"}}
    audit = {}
    result = recommend(query, rows, audit=audit)
    if result["status"] != "ranked":
        parser.error(f"Capture produced no ranked data: {result['reason']}")
    print(json.dumps({
        "query": query, "scoring": result["scoring"],
        "funnel": {key: value for key, value in audit.items() if key != "screenings"},
        "production_order": [candidate["name"] for candidate in result["recommendations"]],
        "verification_required": len(result["verification_required"]),
        "equal_weights": equal_weight_sensitivity(result["recommendations"], result["scoring"]),
    }, indent=2))


if __name__ == "__main__":
    main()
