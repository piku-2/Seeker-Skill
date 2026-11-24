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
        path = run / "qa" / f"test-{profile}.json"
        path.write_text(json.dumps(review))
        assert (
            main(["qa", str(run), "--image-profile", profile, "--review", str(path)])
            == 0
        )
        checked = json.loads(capsys.readouterr().out)
        assert checked["status"] == "complete"
        assert checked["photos"]["work_visual_count"] == count
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
