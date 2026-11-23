"""Run completion gate; unexecuted independent review never becomes a pass."""

from .model import validate
from .scene import check_scene


def classify(
    research_complete, structural_pass, semantic_pass, artifact_pass, independent
):
    if not all([research_complete, structural_pass, semantic_pass, artifact_pass]):
        return "incomplete"
    if independent == "pass":
        return "complete"
    if independent == "unavailable":
        return "complete_limited_review"
    return "incomplete"


def judge(data, scene, review):
    v = validate(data)
    g = check_scene(scene, data)
    st = classify(
        review.get("research_complete", False),
        True,
        review.get("semantic_pass", False),
        review.get("artifact_pass", False),
        review.get("independent_review", "not_run"),
    )
    return {"status": st, "graph": v, "scene": g, "review": review}
