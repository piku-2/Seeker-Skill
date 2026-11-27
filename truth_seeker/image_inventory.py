"""Distinguish rendered image instances from catalog and coverage records."""

from collections import Counter

RASTER_KINDS = (
    "photo",
    "primary_document",
    "visual_source",
    "work_visual",
    "map",
    "diagram",
    "chart",
)


def image_inventory(scene, verified_formats=()):
    rasters = [asset for asset in scene.get("assets", []) if "bbox" in asset]
    kinds = Counter(
        asset.get("record", {}).get("asset_type", "unknown") for asset in rasters
    )
    linked = sum(bool(asset.get("record", {}).get("catalog_id")) for asset in rasters)
    return {
        "raster_images_in_scene": len(rasters),
        "catalog_linked_rasters": linked,
        "noncatalog_rasters": len(rasters) - linked,
        "raster_images_by_kind": {**dict.fromkeys(RASTER_KINDS, 0), **kinds},
        "generated_vector_figures": sum(
            "figure_bbox" in asset for asset in scene.get("assets", [])
        ),
        "verified_raster_images_by_format": dict.fromkeys(
            verified_formats, len(rasters)
        ),
    }


def other_images_for_targets(scene, targets, documentary_kinds):
    other = {
        asset["id"]: asset.get("record", {}).get("asset_type", "unknown")
        for asset in scene.get("assets", [])
        if "bbox" in asset
        and asset.get("record", {}).get("asset_type") not in documentary_kinds
    }
    return {
        node["node_id"]: visuals
        for node in scene["nodes"]
        if node["node_id"] in targets
        and (
            visuals := [
                {"asset_id": key, "kind": other[key]}
                for key in node.get("asset_refs", [])
                if key in other
            ]
        )
    }
