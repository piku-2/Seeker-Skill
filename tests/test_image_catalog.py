"""Image catalog and profile regression tests with invented artwork fixtures."""

import json

import pytest
from PIL import Image
from test_engine import sample
from test_illustration_qa import (
    RIGHTS,
    SOURCE,
    representative_fixture,
    review_fixture,
    search_fixture,
)

from truth_seeker.cli import main
from truth_seeker.images import read_catalog, save_catalog, stage_catalog
from truth_seeker.model import ValidationError, load_generation
from truth_seeker.store import (
    commit_generation,
    create_run,
    read_progress,
    resume,
    save_illustrations,
    save_representatives,
)


def image_file(run, name, color):
    path = run / "staging" / name
    Image.new("RGB", (180, 96), color).save(path)
    return str(path.relative_to(run))


def item(key, path=None):
    return {
        "id": key,
        "node_ids": ["node_old"],
        "media_kind": "work_visual",
        "work_media": "cover",
        "source_page_url": SOURCE,
        "media_url": SOURCE + "/file.png",
        "provider": "架空出版社",
        "identifier": key,
        "caption": "テスト専用の架空の表紙",
        "credit": "架空出版社",
        "verification": "テスト画像の内容と出所を記録。",
        **({"input_path": path} if path else {}),
    }


def test_unreviewed_batch_keeps_acquired_and_unavailable_candidates(tmp_path):
    run = create_run(tmp_path, "架空の画像カタログ")
    commit_generation(run, sample())
    a = item("cover", image_file(run, "cover.png", "orange"))
    missing = item("missing")
    missing.update(
        acquisition="failed", acquisition_note="公式ページには画像実体がない。"
    )
    result = save_catalog(run, {"candidates": [a, missing]})
    assert (result["discovered"], result["acquired"], result["identified"]) == (2, 1, 0)
    assert result["approved"] == {"distribution": 0, "local": 0}
    assert result["acquired_rights_unconfirmed_by_profile"] == {
        "distribution": 1,
        "local": 1,
    }
    retained = result["candidates"][0]
    assert (run / retained["local_path"]).is_file()
    assert retained["source_page_url"] != retained["media_url"]
    assert retained["size"] == [180, 96]
    resume(run)
    assert read_catalog(run)["candidates"] == result["candidates"]
    assert stage_catalog(run, run / "staging" / "previews")["catalog_assets"] == []
    duplicate = item("duplicate", a["input_path"])
    with pytest.raises(ValidationError, match="identical image bytes"):
        save_catalog(run, {"candidates": [duplicate]})
    assert len(read_catalog(run)["candidates"]) == 2


def test_local_approval_is_isolated_from_distribution_and_uses_reusable_policy(
    tmp_path, capsys
):
    run = create_run(tmp_path, "架空の利用範囲")
    data = sample()
    data["nodes"][0]["type"] = "product"
    commit_generation(run, data)
    stage = run / "staging" / "use"
    policy = {
        "id": "local-test",
        "profile": "local",
        "basis": "permission",
        "source_url": RIGHTS,
        "conditions": "架空作品は個人閲覧にのみ利用可。",
    }
    batch = {
        "policies": [policy],
        "candidates": [
            {
                **item(key, image_file(run, key + ".png", color)),
                "authenticity": "verified",
                "uses": {
                    "local": {
                        "decision": "approved",
                        "policy_id": "local-test",
                        "reason": "対象画像は許諾範囲。",
                    }
                },
            }
            for key, color in (("cover", "orange"), ("alternate", "blue"))
        ],
    }
    batch_path = run / "staging" / "batch.json"
    batch_path.write_text(json.dumps(batch))
    assert main(["images-save", str(run), str(batch_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["approved"] == {"distribution": 0, "local": 2}
    staged = stage_catalog(run, stage)
    assert len(staged["catalog_assets"]) == 2
    assert all(c["decision"] == "pending" for c in staged["candidate_drafts"])
    data = load_generation(stage)
    generation = commit_generation(run, data)
    report = search_fixture()
    report["candidates"] = [
        {
            **draft,
            "search_id": "s1",
            "depicted_node_ids": ["node_old"],
            "decision": "adopted",
            "rights": "scoped",
            "rights_url": RIGHTS,
            "rights_note": asset["rights_note"],
            "reason": "テスト用",
            "useful": True,
        }
        for draft, asset in zip(staged["candidate_drafts"], data["assets"])
    ]
    save_illustrations(run, report, complete=True)
    save_representatives(run, representative_fixture(), complete=True)
    for profile, directory, count in (
        ("distribution", "render", 0),
        ("local", "render-local", 2),
    ):
        assert main(["render", str(run), "--image-profile", profile]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["coverage"]["images"] == count
        text = (run / directory / "map.svg").read_text()
        assert ("data:image/png;base64," in text) is bool(count)
        if not count:
            assert "画像参照" in text and SOURCE in text
        review = review_fixture(generation, staged["catalog_assets"] if count else [])
        if profile == "distribution":
            review["photo_review"].update(
                coverage_adequate=False,
                visual_coverage="constrained",
                constraint="図版2点の許諾はローカル閲覧に限られる。",
            )
        path = run / "qa" / f"test-{profile}.json"
        path.write_text(json.dumps(review))
        assert (
            main(["qa", str(run), "--image-profile", profile, "--review", str(path)])
            == 0
        )
        checked = json.loads(capsys.readouterr().out)
        assert checked["status"] == "complete"
        assert checked["photos"]["work_visual_count"] == count
        counts = checked["illustrations"]["image_inventory"]
        assert counts["catalog_linked_rasters"] == count
        assert counts["noncatalog_rasters"] == 0
        assert counts["verified_raster_images_by_format"] == dict.fromkeys(
            ("pdf", "svg", "html"), count
        )
        assert checked["image_catalog"]["acquired_rights_unconfirmed_by_profile"] == {
            "distribution": 2,
            "local": 0,
        }
        assert checked["photos"]["rights_cleared_count"] == count
        assert checked["photos"]["not_embeddable_count"] == 2 - count
        assert checked["photos"]["rights_pending_count"] == 0
        assert checked["photos"]["visual_coverage"] == (
            "adequate" if profile == "local" else "constrained"
        )
        assert checked["photos"]["formats"] == dict.fromkeys(
            ("pdf", "svg", "html"), count
        )
    manifest = json.loads((run / "manifest.json").read_text())
    assert all("render-local" not in a for a in manifest["artifacts"])
    assert read_progress(run)["run_status"] == "complete"
    save_catalog(
        run,
        {
            "candidates": [
                {
                    "id": "cover",
                    "uses": {"local": {"decision": "withheld", "reason": "許諾終了"}},
                },
            ]
        },
    )
    assert main(["render", str(run), "--image-profile", "local"]) == 1
    assert "restage and merge" in capsys.readouterr().err


def test_missing_basis_and_new_file_cannot_inherit_old_approval(tmp_path):
    run = create_run(tmp_path, "架空の再確認")
    commit_generation(run, sample())
    record = item("cover", image_file(run, "old.png", "green"))
    record["authenticity"] = "verified"
    record["uses"] = {
        "local": {"decision": "approved", "policy_id": "perm", "reason": "確認済み。"}
    }
    with pytest.raises(ValidationError, match="missing policy"):
        save_catalog(run, {"candidates": [record]})
    policy = {
        "id": "perm",
        "profile": "local",
        "source_url": RIGHTS,
        "conditions": "条件",
        "basis": "permission",
    }
    save_catalog(run, {"policies": [policy], "candidates": [record]})
    changed = save_catalog(
        run,
        {
            "candidates": [
                {"id": "cover", "input_path": image_file(run, "new.png", "blue")}
            ]
        },
    )
    assert changed["identified"] == 0
    assert changed["approved"]["local"] == 0


def _payloads(run):
    files = run / "image-library" / "files"
    return list(files.iterdir()) if files.is_dir() else []


def test_rejected_batch_publishes_no_payload(tmp_path):
    run = create_run(tmp_path, "架空の失敗登録")
    commit_generation(run, sample())
    bad = item("orphan", image_file(run, "orphan.png", "red"))
    bad["node_ids"] = ["node_future_unmerged"]
    with pytest.raises(ValidationError, match="missing adopted subjects"):
        save_catalog(run, {"candidates": [bad]})
    assert read_catalog(run)["candidates"] == []
    assert _payloads(run) == []


def test_failed_later_candidate_publishes_no_earlier_payload(tmp_path):
    run = create_run(tmp_path, "架空の途中失敗")
    commit_generation(run, sample())
    good = item("cover", image_file(run, "cover.png", "orange"))
    bad = item("other", image_file(run, "other.png", "blue"))
    bad["node_ids"] = ["node_future_unmerged"]
    with pytest.raises(ValidationError, match="missing adopted subjects"):
        save_catalog(run, {"candidates": [good, bad]})
    assert read_catalog(run)["candidates"] == []
    assert _payloads(run) == []


def test_failed_batch_keeps_a_payload_committed_earlier(tmp_path):
    run = create_run(tmp_path, "架空の既存ファイル")
    commit_generation(run, sample())
    saved = save_catalog(
        run, {"candidates": [item("cover", image_file(run, "cover.png", "orange"))]}
    )
    kept = run / saved["candidates"][0]["local_path"]
    assert kept.is_file()
    bad = item("other", image_file(run, "other.png", "blue"))
    bad["node_ids"] = ["node_future_unmerged"]
    with pytest.raises(ValidationError, match="missing adopted subjects"):
        save_catalog(run, {"candidates": [bad]})
    assert kept.is_file()
    assert [item["id"] for item in read_catalog(run)["candidates"]] == ["cover"]
    assert len(_payloads(run)) == 1


def test_catalog_write_failure_removes_the_new_payload(tmp_path, monkeypatch):
    run = create_run(tmp_path, "架空の書込失敗")
    commit_generation(run, sample())

    def fail(path, data):
        raise OSError("catalog write failed")

    monkeypatch.setattr("truth_seeker.store.atomic_json", fail)
    with pytest.raises(OSError, match="catalog write failed"):
        save_catalog(
            run, {"candidates": [item("cover", image_file(run, "cover.png", "orange"))]}
        )
    assert read_catalog(run)["candidates"] == []
    assert _payloads(run) == []


def test_rendered_caption_shows_only_the_active_profile(tmp_path):
    from truth_seeker.scene import check_scene, layout

    run = create_run(tmp_path, "架空の表示範囲")
    commit_generation(run, sample())
    record = item("cover", image_file(run, "cover.png", "orange"))
    record["authenticity"] = "verified"
    record["uses"] = {
        "distribution": {
            "decision": "approved",
            "policy_id": "dist",
            "reason": "配布可。",
        },
        "local": {"decision": "approved", "policy_id": "loc", "reason": "個人可。"},
    }
    save_catalog(
        run,
        {
            "policies": [
                {
                    "id": "dist",
                    "profile": "distribution",
                    "basis": "license",
                    "source_url": RIGHTS,
                    "conditions": "配布条件は作者名を表示する。",
                },
                {
                    "id": "loc",
                    "profile": "local",
                    "basis": "permission",
                    "source_url": RIGHTS,
                    "conditions": "個人条件は画面内だけで見る。",
                },
            ],
            "candidates": [record],
        },
    )
    merged = load_generation(stage_catalog(run, run / "staging" / "caps")["stage"])
    note = merged["assets"][0]["rights_note"]
    assert note == (
        "distribution: 配布条件は作者名を表示する。 / local: 個人条件は画面内だけで見る。"
    )
    for profile, shown, hidden in (
        ("distribution", "配布条件は作者名を表示する。", "個人条件は画面内だけで見る。"),
        ("local", "個人条件は画面内だけで見る。", "配布条件は作者名を表示する。"),
    ):
        scene = layout(merged, "run_caption", "題", run_dir=run, image_profile=profile)
        assert check_scene(scene, merged)["status"] == "pass"
        caption = next(
            asset["caption"]
            for asset in scene["assets"]
            if asset["record"].get("catalog_id") == "cover"
        )
        assert shown in caption and hidden not in caption
        assert f"{profile}:" in caption
    assert merged["assets"][0]["rights_note"] == note
