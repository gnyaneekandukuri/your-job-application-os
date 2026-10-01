"""Deterministic evidence coverage, not eligibility or hiring probability.

Recomputed from validated report rows; never an additional model call.
Weights are a transparent product rubric, not an industry/ATS standard.
"""
VERSION = "documented-fit-v2"
# Numeric scoring enabled after user review of the alignment rubric.
ENABLED = True
WEIGHTS = {"requirement": 2, "responsibility": 2, "preference": 1, "unclear": 0}
CREDIT = {"strong": 1, "partial": 0.5, "insufficient_evidence": 0}


def alignment_score(report):
    if report is None:
        return None
    # Do not reinterpret old support labels as the new alignment rubric.
    if any(not m["judgment"].get("assessment") for m in report["results"].values()):
        return None
    rows = []
    for requirement in report["requirements"]:
        match = report["results"].get(requirement["id"])
        # Defense in depth: do not score a result attached to a different item.
        if match and (match["requirement_id"] != requirement["id"] or match["job_id"] != report["job_id"]):
            raise ValueError("Evidence score identity mismatch")
        support = match["judgment"]["assessment"]["alignment"] if match else "pending"
        weight = WEIGHTS[requirement["category"]]
        rows.append({"requirement_id": requirement["id"], "weight": weight,
                     "support": support, "earned": weight * CREDIT.get(support, 0)})
    maximum = sum(r["weight"] for r in rows)
    earned = sum(r["earned"] for r in rows)
    pending = sum(r["support"] == "pending" for r in rows)
    return {
        "version": VERSION,
        "percent": round(100 * earned / maximum) if maximum else None,
        "earned": earned, "maximum": maximum, "pending": pending,
        "provisional": report["state"] != "COMPLETE" or bool(pending),
        "excluded_ambiguous": sum(r["weight"] == 0 for r in rows),
        "required_attention": [r["id"] for r in report["requirements"]
            if r["category"] == "requirement" and
            report["results"].get(r["id"], {}).get("judgment", {}).get("assessment", {}).get("alignment") != "strong"],
        "items": rows,
    }


def score_report(report):
    from .dimension_score import aggregate
    return aggregate(report, alignment_score(report))
