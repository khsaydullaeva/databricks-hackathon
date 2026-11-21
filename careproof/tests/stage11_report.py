"""Emit reproducible synthetic red-team evaluation input to stdout."""

import json
import sys

from stage11_fixtures import cases


if __name__ == "__main__":
    json.dump({
        "evidence_basis": (
            "Manually labeled synthetic Gold-shaped cases. Measures ranker behavior under "
            "controlled inputs. Live source evaluation and human source review are pending."
        ),
        "cases": list(cases()),
    }, sys.stdout, indent=2)
    print()
