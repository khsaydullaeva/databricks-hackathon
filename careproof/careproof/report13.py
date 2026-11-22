"""Reproduce final evaluation metrics and figures from local evidence artifacts."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "reports"
INPUTS = ("02_profile.json", "real_data_capture.json",
          "location_resolution_live_before_after.json", "11_evaluation.json")


def metrics():
    files = {name: (ROOT / name).read_bytes() for name in INPUTS}
    profile, capture, audit, evaluation = (json.loads(files[name]) for name in INPUTS)
    rows = capture["rows"]
    dimensions = ("claim_type", "evidence_status", "source_field", "source_attribution_status",
                  "geo_status", "admin_status", "verification_reason", "pin_status")
    services = {}
    for service in sorted({case["query"]["service"] for case in audit["searches"]}):
        searches = [case["after"] for case in audit["searches"] if case["query"]["service"] == service]
        services[service] = {
            "capture_evidence_states": dict(Counter(row["evidence_status"] for row in rows
                                                     if row["service_key"] == service)),
            **{key: sum(case[key] for case in searches) for key in (
                "executions", "ranked", "abstained", "recommendation_placements",
                "verification_required_placements")},
        }
    checks = [check for case in audit["searches"] for check in case["ranked_checks"]]
    return {
        "inputs_sha256": {name: sha256(content).hexdigest() for name, content in files.items()},
        "bronze": {key: profile[key] for key in ("total_rows", "column_count", "generated_at_utc",
                    "null_counts", "geography", "duplicate_ids", "json_parse_failures")},
        "capture": {"rows": len(rows), "collected_at_utc": capture["collected_at_utc"],
                    **{field: dict(Counter(row[field] for row in rows)) for field in dimensions}},
        "location_audit": {key: audit[key] for key in ("before", "after", "normalized_city_only",
                             "after_execution_mode", "after_source_collected_at_utc", "postal_pins_loaded")}
                         | {"ranked_checks": len(checks),
                            "ranked_exact_record_matches": sum(c["exact_own_record_citation"] for c in checks),
                            "ranked_address_checks_passed": sum(c["address_consistent"] for c in checks),
                            "failed_checks": len(audit["checks_failed"])},
        "service_breakdown": services,
        "synthetic": evaluation["summary"],
    }


def figure(data):
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 10, "svg.hashsalt": "careproof-stage13"})
    fig, (missing, claims, searches) = plt.subplots(1, 3, figsize=(15, 4.5), layout="constrained")
    bronze = data["bronze"]
    fields = {"City": "address_city", "Description": "description", "Source URLs": "source_urls",
              "Capability": "capability", "Procedure": "procedure", "Equipment": "equipment"}
    values = [bronze["null_counts"][field] for field in fields.values()]
    bars = missing.barh(list(fields), values, color="#176b68")
    missing.bar_label(bars, padding=3)
    missing.set(xlim=(0, max(values) * 1.15), xlabel="Missing-like rows",
                title=f"Bronze missingness · n={bronze['total_rows']:,}")
    states = data["capture"]["evidence_status"]
    labels = {"Supported claim": "supported_claim", "Uncertain": "uncertain",
              "Conflicting": "conflicting", "Rejected": "rejected"}
    bars = claims.barh(list(labels), [states[key] for key in labels.values()],
                       color=["#176b68", "#d5a247", "#ab5747", "#787b81"])
    claims.bar_label(bars, padding=3)
    claims.set(xlim=(0, max(states.values()) * 1.15), xlabel="Captured facility–service rows",
               title=f"Gold export states · n={data['capture']['rows']:,}")
    labels = {"Dialysis": "dialysis", "MRI": "mri", "NICU": "nicu", "Emergency C-section": "emergency_c_section",
              "Cardiac surgery": "cardiac_surgery", "Trauma/emergency": "trauma_emergency_care"}
    breakdown = [data["service_breakdown"][key] for key in labels.values()]
    ranked = [row["ranked"] for row in breakdown]
    searches.barh(list(labels), ranked, label="Ranked", color="#176b68")
    searches.barh(list(labels), [row["abstained"] for row in breakdown], left=ranked,
                  label="Abstained", color="#dcc7a0")
    searches.set(xlim=(0, max(row["executions"] for row in breakdown)),
                 xlabel="Queries per service", title="Fresh-export audit")
    searches.legend(loc="lower right", bbox_to_anchor=(1, -0.29), ncols=2, frameon=False)
    for ax in (missing, claims, searches):
        ax.invert_yaxis()
        ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(ROOT / "stage13_figures.svg", metadata={"Date": None})
    plt.close(fig)


def main():
    data = metrics()
    (ROOT / "stage13_metrics.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    figure(data)


if __name__ == "__main__":
    main()
