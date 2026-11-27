"""Regression fixtures for route approaches and raster inventory."""

import json

import pymupdf
from test_dynamic_layout import figure_data
from test_photo_qa import add_photo

from truth_seeker.image_inventory import image_inventory, other_images_for_targets
from truth_seeker.layout_quality import PathSample, layout_metrics
from truth_seeker.photos import DOCUMENTARY_TYPES
from truth_seeker.render_run import render_run
from truth_seeker.routing import (
    _untangle_ports,
    edge_obstacles,
    first_collision,
    port_point,
)
from truth_seeker.status import status
from truth_seeker.store import commit_generation, create_run


def test_routes_can_exchange_crossed_slots_without_changing_endpoints():
    cards = {"a": (0, 20, 80, 40), "b": (160, 20, 80, 40), "hub": (0, 240, 240, 45)}

    def port(key, side, fraction):
        return {
            "side": side,
            "fraction": fraction,
            "point": port_point(cards[key], side, fraction),
        }

    routes = []
    for key, slot in (("a", 0.75), ("b", 0.25)):
        edge = {"id": key, "source_id": key, "target_id": "hub"}
        start, end = port(key, "bottom", 0.5), port("hub", "top", slot)
        x, y = start["point"]
        u, v = end["point"]
        routes.append((edge, start, end, [(x, y), (x, y + 60), (u, v - 60), (u, v)]))
    samples = [PathSample(r[-1]) for r in routes]
    assert samples[0].crosses(samples[1])
    _untangle_ports(routes, samples, cards, 250)
    assert not samples[0].crosses(samples[1])
    assert [r[0]["target_id"] for r in routes] == ["hub", "hub"]
    assert [r[2]["fraction"] for r in routes] == [0.25, 0.75]
    for edge, start, end, path in routes:
        assert path[0] == start["point"] and path[-1] == end["point"]
        assert (
            first_collision(
                path, edge_obstacles(cards, edge["source_id"], edge["target_id"])
            )
            is None
        )


def test_inventory_matches_pdf_and_keeps_maps_separate_from_documentary_targets(
    tmp_path,
):
    run = create_run(tmp_path, "架空の画像一覧")
    data = figure_data("table")
    add_photo(run, data, "photo", "blue")
    add_photo(run, data, "historicmap", "teal", owner="node_new")["asset_type"] = "map"
    add_photo(run, data, "diagram", "gray", owner="node_new")["asset_type"] = "diagram"
    add_photo(run, data, "primary", "green")["asset_type"] = "primary_document"
    commit_generation(run, data)
    result = render_run(run, "distribution", "all")
    inventory = result["image_inventory"]
    assert inventory["catalog_linked_rasters"] == 0
    assert inventory["noncatalog_rasters"] == inventory["raster_images_in_scene"] == 4
    assert inventory["generated_vector_figures"] == 1
    assert {k for k, n in inventory["raster_images_by_kind"].items() if n} == {
        "photo",
        "map",
        "diagram",
        "primary_document",
    }
    with pymupdf.open(run / "render" / "map.pdf") as pdf:
        assert len(pdf) == 1
        assert (
            len(pdf[0].get_image_info())
            == inventory["verified_raster_images_by_format"]["pdf"]
        )
    scene = json.loads((run / "render" / "scene.json").read_text())
    assert other_images_for_targets(scene, {"node_new"}, DOCUMENTARY_TYPES) == {
        "node_new": [
            {"asset_id": "asset_historicmap", "kind": "map"},
            {"asset_id": "asset_diagram", "kind": "diagram"},
        ],
    }
    checked = status(run)["summary"]
    assert checked["outputs"]["distribution"]["image_inventory"] == inventory
    assert checked["image_catalog"]["discovered"] == 0
    fallback = tmp_path / "fallback"
    from truth_seeker.render import emit

    rejected = emit(scene, fallback, force_pdf_error=True)
    assert "pdf" not in rejected["image_inventory"]["verified_raster_images_by_format"]


def test_crossing_metrics_distinguish_shared_slots_and_card_groups():
    scene = {
        "nodes": [],
        "edges": [
            {
                "edge_id": name,
                "source_node_id": name,
                "target_node_id": "shared",
                "target_port": {"side": side},
                "path": [from_, from_, to, to],
            }
            for name, side, from_, to in (
                ("a", "top", (0, 0), (10, 10)),
                ("b", "top", (0, 9), (12, 0)),
                ("c", "left", (0, 8), (16, -4)),
            )
        ],
        "expected_node_ids": [],
        "expected_edge_ids": ["a", "b", "c"],
        "canvas_width": 20,
        "canvas_height": 20,
    }
    metrics = layout_metrics(scene)
    assert metrics["crossings_sharing_card"] == 2
    assert metrics["crossings_sharing_port_side"] == 1
    assert metrics["longest_edge_id"] == "c"
    assert image_inventory(scene)["verified_raster_images_by_format"] == {}
