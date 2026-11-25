"""Acquisition stays open when reuse terms are unknown. Fixtures are fictional."""

import hashlib
import json

import pytest
from PIL import Image
from test_dynamic_layout import figure_data
from test_engine import sample
from test_illustration_qa import (
    RIGHTS,
    SOURCE,
    assess,
    render_fixture,
    representative_fixture,
    review_fixture,
    search_fixture,
)
from test_image_catalog import image_file, item
from test_layout import add_node

from truth_seeker.model import ValidationError
from truth_seeker.photos import opportunities, validate_photo_review, validate_photo_search
from truth_seeker.store import (
    commit_generation,
    create_run,
    illustration_task,
    read_progress,
    resume,
    save_illustrations,
    save_representatives,
)
from truth_seeker.images import read_catalog, save_catalog, stage_catalog
from truth_seeker.cli import main


def _withheld(catalog_item, path_info):
    return {
        "id": "image_" + catalog_item["id"],
        "catalog_id": catalog_item["id"],
        "node_ids": ["node_old"],
        "search_id": "s1",
        "source_url": SOURCE,
        "media_kind": "work_visual",
        "depicted_node_ids": ["node_old"],
        "provider": catalog_item["provider"],
        "identifier": catalog_item["identifier"],
        "acquisition": "verified",
        "sha256": path_info["sha256"],
        "local_path": path_info["local_path"],
        "useful": True,
        "decision": "rejected",
        "rights": "unknown",
        "rejection": "rights_unavailable",
        "reason": "ファイルは取得済み。利用条件は未確認なので掲載しない。",
    }


def test_unknown_rights_keep_the_candidate_and_do_not_skip_acquisition():
    report = search_fixture()
    report["candidates"][0].update(
        acquisition="not_attempted",
        rejection="rights_unavailable",
        rights="unknown",
        useful=True,
        reason="再配布条件が未確認",
    )
    for field in ("attempts", "alternatives_considered"):
        report["candidates"][0].pop(field, None)
    with pytest.raises(ValidationError, match="does not replace acquisition"):
        validate_photo_search(report, sample(), representative_fixture())
    blocked = search_fixture()
    blocked["candidates"][0].update(
        acquisition="not_attempted",
        rejection="access_unavailable",
        acquisition_block="access_restricted",
        acquisition_block_note="提供元がログインなしの取得を拒否している",
        useful=True,
        rights="unknown",
    )
    for field in ("attempts", "alternatives_considered"):
        blocked["candidates"][0].pop(field, None)
    coverage = validate_photo_search(blocked, sample(), representative_fixture())
    assert coverage["candidates"]["c1"]["identifier"] == "fixture-1"


def test_review_cannot_hide_an_unfetched_useful_candidate():
    report = search_fixture()
    candidate = report["candidates"][0]
    candidate.update(
        decision="rejected",
        useful=True,
        rights="unknown",
        acquisition="not_attempted",
        rejection="rights_unavailable",
    )
    coverage = {
        "candidates": {"c1": candidate},
        "assets": {},
        "suitable": {"node_old"},
        "targets": {"node_old": {"decision": "search"}},
        "searches": {"s9": {"query": "保存済みの照会"}},
    }
    review = review_fixture("g")["photo_review"]
    with pytest.raises(ValidationError, match="contradicts the review"):
        validate_photo_review(review, coverage)


def test_acquired_file_can_wait_and_later_embed_for_one_profile(tmp_path, capsys):
    run = create_run(tmp_path, "架空の取得と掲載の分離")
    data = sample()
    data["nodes"][0]["type"] = "product"
    commit_generation(run, data)
    record = item("screen", image_file(run, "screen.png", "purple"))
    record["work_media"] = "screenshot"
    record["media_kind"] = "work_visual"
    saved = save_catalog(run, {"candidates": [record]})
    assert saved["acquired"] == 1 and saved["approved"] == {"distribution": 0, "local": 0}
    assert saved["embedding_pending"] == 1
    held = saved["candidates"][0]
    report = search_fixture()
    report["candidates"] = [_withheld(record, held)]
    save_illustrations(run, report, complete=True)
    save_representatives(run, representative_fixture(), complete=True)
    scene = render_fixture(run, data)
    generation = read_progress(run)["active_generation"]
    review = review_fixture(generation)
    review["photo_review"]["coverage_adequate"] = True
    blocked = assess(run, data, scene, review)
    assert blocked["status"] == "incomplete"
    assert "not full coverage" in blocked["photos"]["reason"]
    review["photo_review"].update(
        coverage_adequate=False,
        visual_coverage="constrained",
        constraint="取得済みの画面は利用条件が未確認のため、どの出力にも埋め込まない。",
    )
    waiting = assess(run, data, scene, review)
    assert waiting["status"] == "complete"
    assert waiting["photos"]["visual_coverage"] == "constrained"
    assert waiting["photos"]["embedded_count"] == 0
    assert waiting["photos"]["acquisition_verified_count"] == 1
    assert waiting["photos"]["rights_pending_count"] == 1
    assert waiting["photos"]["photo_count"] == 0
    assert waiting["illustrations"]["generated_figure_count"] == 0
    stored = illustration_task(resume(run))["illustration_report"]["candidates"][0]
    assert stored["rejection"] == "rights_unavailable"
    assert read_catalog(run)["candidates"][0]["sha256"] == held["sha256"]
    path = run / held["local_path"]
    original = path.read_bytes()
    path.write_bytes(b"not-an-image")
    assert "checksum differs" in assess(run, data, scene, review)["photos"]["reason"]
    path.write_bytes(original)

    policy = {
        "id": "local-screen",
        "profile": "local",
        "basis": "permission",
        "source_url": RIGHTS,
        "conditions": "架空の画面は個人閲覧だけ。",
    }
    save_catalog(
        run,
        {
            "policies": [policy],
            "candidates": [
                {
                    "id": "screen",
                    "authenticity": "verified",
                    "uses": {
                        "local": {
                            "decision": "approved",
                            "policy_id": "local-screen",
                            "reason": "この画面だけ個人閲覧を許諾。",
                        }
                    },
                }
            ],
        },
    )
    stage = run / "staging" / "screen-use"
    staged = stage_catalog(run, stage)
    from truth_seeker.model import load_generation

    merged = load_generation(stage)
    commit_generation(run, merged)
    report["candidates"] = [
        {
            **staged["candidate_drafts"][0],
            "search_id": "s1",
            "depicted_node_ids": ["node_old"],
            "decision": "adopted",
            "rights": "scoped",
            "rights_url": RIGHTS,
            "rights_note": merged["assets"][0]["rights_note"],
            "reason": "個人閲覧の許諾を確認して採用。",
            "useful": True,
            "source_url": SOURCE,
        }
    ]
    save_illustrations(run, report, complete=True)
    save_representatives(run, representative_fixture(), complete=True)
    for profile, count in (("distribution", 0), ("local", 1)):
        assert main(["render", str(run), "--image-profile", profile]) == 0
        rendered = json.loads(capsys.readouterr().out)
        assert rendered["coverage"]["images"] == count
    local_review = review_fixture(read_progress(run)["active_generation"], staged["catalog_assets"])
    path = run / "qa" / "local-review.json"
    path.write_text(json.dumps(local_review))
    assert main(["qa", str(run), "--image-profile", "local", "--review", str(path)]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["status"] == "complete"
    assert checked["photos"]["embedded_count"] == 1
    assert checked["photos"]["work_visual_count"] == 1
    assert checked["photos"]["rights_cleared_count"] == 1
    assert checked["photos"]["not_embeddable_count"] == 0
    assert checked["photos"]["visual_coverage"] == "adequate"
    assert checked["image_catalog"]["approved"] == {"distribution": 0, "local": 1}
    dist_review = review_fixture(read_progress(run)["active_generation"])
    dist_path = run / "qa" / "dist-review.json"
    args = ["qa", str(run), "--image-profile", "distribution", "--review", str(dist_path)]
    dist_path.write_text(json.dumps(dist_review))
    main(args)
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["status"] == "incomplete"
    assert "withheld useful image is not full coverage" in rejected["photos"]["reason"]

    dist_review["photo_review"].update(
        coverage_adequate=False,
        visual_coverage="constrained",
        constraint="取得済みの画面は個人閲覧に限り許諾されている。",
    )
    dist_path.write_text(json.dumps(dist_review))
    assert main(args) == 0
    distributed = json.loads(capsys.readouterr().out)
    assert distributed["status"] == "complete"
    assert distributed["photos"]["embedded_count"] == 0
    assert distributed["photos"]["not_embeddable_count"] == 1
    assert distributed["photos"]["rights_pending_count"] == 0
    assert distributed["photos"]["visual_coverage"] == "constrained"
    manifest = json.loads((run / "manifest.json").read_text())
    assert all("render-local" not in artifact for artifact in manifest["artifacts"])


def test_generated_figures_do_not_satisfy_a_missing_useful_image(tmp_path):
    run = create_run(tmp_path, "架空の図表は実画像を代用しない")
    data = figure_data("table")
    commit_generation(run, data)
    report = search_fixture()
    source = data["evidence"][0]["source_url"]
    report["searches"].append(
        {"id": "table", "query": "架空の表", "checked_urls": [source], "result": "比較"}
    )
    report["targets"][0]["search_ids"].append("table")
    report["targets"].append(
        {
            "node_id": "node_new",
            "search_ids": ["table"],
            "reason": "表の対象",
            "outcome": "照合",
        }
    )
    report["candidates"].append(
        {
            "id": "table",
            "search_id": "table",
            "node_ids": ["node_old", "node_new"],
            "source_url": source,
            "decision": "adopted",
            "rights": "original",
            "rights_note": "自作",
            "reason": "比較表",
            "asset_id": data["assets"][0]["id"],
        }
    )
    report["figure_plan"]["decisions"].append(
        {
            "id": "table",
            "node_ids": ["node_old", "node_new"],
            "kind": "table",
            "value": "年代と場所",
            "decision": "adopted",
            "reason": "比較",
            "asset_id": data["assets"][0]["id"],
        }
    )
    report["photo_plan"]["targets"].append(
        {
            "node_id": "node_new",
            "decision": "not_suitable",
            "reason": "この接続は架空の仮説ラベルだけで、画面・書影・文書画像という対象を持たない。",
            "search_ids": [],
            "visual_kinds_considered": ["screenshot", "cover", "document"],
        }
    )
    bad = json.loads(json.dumps(report))
    bad["candidates"][0].update(
        acquisition="not_attempted",
        rejection="rights_unavailable",
        rights="unknown",
    )
    bad["candidates"][0].pop("attempts")
    bad["candidates"][0].pop("alternatives_considered")
    with pytest.raises(ValidationError, match="does not replace acquisition"):
        save_illustrations(run, bad, complete=True)
    save_illustrations(run, report, complete=True)
    save_representatives(run, representative_fixture(), complete=True)
    scene = render_fixture(run, data)
    review = review_fixture(read_progress(run)["active_generation"], [data["assets"][0]["id"]])
    review["photo_review"]["excluded"] = {
        "node_new": "仮説ラベルには画面や書影という視覚対象がない。"
    }
    review["figure_review"]["assets"][data["assets"][0]["id"]] = {
        "data_matches": True,
        "uncertainty_clear": True,
        "findings": "架空データと一致",
    }
    result = assess(run, data, scene, review)
    assert result["status"] == "complete"
    assert result["photos"]["generated_figure_count"] == 1
    assert result["photos"]["embedded_count"] == 0
    assert result["photos"]["photo_count"] == 0
    assert result["illustrations"]["generated_figure_count"] == 1


def test_product_screens_and_covers_are_proposed_and_weak_exclusions_fail():
    data = sample()
    add_node(data, "node_product", 1990)
    data["nodes"][-1]["type"] = "product"
    data["nodes"][-1]["label"] = "架空のソフト"
    kinds = {
        item["node_id"]: item["visual_kinds"]
        for item in opportunities(data, representative_fixture())
    }
    assert {"screenshot", "package", "cover"} <= set(kinds["node_product"])
    report = search_fixture()
    weak = {
        "node_id": "node_product",
        "decision": "not_suitable",
        "reason": "仕様の変化を扱うので現物写真で差を判別しにくい。",
        "search_ids": [],
        "visual_kinds_considered": ["screenshot", "package", "cover"],
    }
    report["photo_plan"]["targets"].append(weak)
    with pytest.raises(ValidationError, match="weak visual exclusion"):
        validate_photo_search(report, data, representative_fixture())
    weak["reason"] = "内部識別子だけで、公開された外観は対象にならない。"
    weak["visual_kinds_considered"] = ["portrait"]
    with pytest.raises(ValidationError, match="work visual not considered"):
        validate_photo_search(report, data, representative_fixture())
    weak["reason"] = "配布物のない内部手続きで、画面・パッケージ・書影を探しても対象の外観は残っていない。"
    weak["visual_kinds_considered"] = ["screenshot", "package", "cover"]
    coverage = validate_photo_search(report, data, representative_fixture())
    assert coverage["targets"]["node_product"]["decision"] == "not_suitable"


def test_failed_fetch_keeps_its_history_until_an_alternative_is_recorded():
    report = search_fixture()
    report["candidates"][0]["alternatives_considered"] = ""
    with pytest.raises(ValidationError, match="without a justified decision"):
        validate_photo_search(report, sample(), representative_fixture())
    report["candidates"][0]["alternatives_considered"] = "別館の目録でもファイルは提供されていない。"
    assert (
        validate_photo_search(report, sample(), representative_fixture())["candidates"][
            "c1"
        ]["attempts"][0]["result"]
        == "提供ページに画像ファイルがない"
    )


def test_draft_candidate_survives_resume(tmp_path):
    run = create_run(tmp_path, "架空の未取得候補")
    commit_generation(run, sample())
    report = search_fixture()
    report["candidates"][0].update(
        acquisition="not_attempted",
        rejection="rights_unavailable",
        rights="unknown",
        useful=True,
    )
    report["candidates"][0].pop("attempts")
    report["candidates"][0].pop("alternatives_considered")
    save_illustrations(run, report)
    assert illustration_task(resume(run))["illustration_report"] == report


def test_image_bytes_stay_distinct(tmp_path):
    run = create_run(tmp_path, "架空の同一性")
    commit_generation(run, sample())
    path = image_file(run, "same.png", "purple")
    digest = hashlib.sha256((run / path).read_bytes()).hexdigest()
    save_catalog(run, {"candidates": [item("one", path)]})
    with pytest.raises(ValidationError, match="identical image bytes"):
        save_catalog(run, {"candidates": [item("two", path)]})
    assert read_catalog(run)["candidates"][0]["sha256"] == digest
    Image.new("RGB", (12, 12), "red").save(run / path)
    assert hashlib.sha256((run / path).read_bytes()).hexdigest() != digest
