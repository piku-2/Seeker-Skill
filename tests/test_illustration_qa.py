"""Illustration workflow tests. All invented source and object records are fixtures."""

import json
from pathlib import Path

import pytest
from PIL import Image
from test_engine import sample

from truth_seeker.cli import main
from truth_seeker.figure_plan import CHECKS
from truth_seeker.model import ValidationError, load_generation, validate
from truth_seeker.qa import judge
from truth_seeker.render import emit, layout
from truth_seeker.store import (
    commit_generation,
    create_run,
    illustration_task,
    read_progress,
    resume,
    save_illustrations,
    save_representatives,
)

SOURCE = "https://example.org/fixture/object"
RIGHTS = "https://example.org/fixture/archive-terms"


def search_fixture():
    return {
        "scope": "架空資料の原資料と公開アーカイブを確認するテスト",
        "searches": [
            {
                "id": "s1",
                "query": "架空の資料",
                "checked_urls": [SOURCE, RIGHTS],
                "result": "架空の所蔵資料を1件確認",
            }
        ],
        "targets": [
            {
                "node_id": "node_old",
                "reason": "起点の資料を見せる",
                "search_ids": ["s1"],
                "outcome": "図版の内容と利用条件を照合",
            }
        ],
        "candidates": [
            {
                "id": "c1",
                "node_ids": ["node_old"],
                "search_id": "s1",
                "source_url": SOURCE,
                "decision": "rejected",
                "rights": "unknown",
                "reason": "公開アーカイブの再配布条件が明記されていない",
            }
        ],
        "zero_reason": "架空アーカイブで確認した画像の再配布条件が明記されていない",
        "figure_plan": {
            "checks": {key: "架空のテストデータに検証済みの根拠なし" for key in CHECKS},
            "decisions": [
                {
                    "id": f"graph-{category}",
                    "kind": kind,
                    "node_ids": ["node_old", "node_new"],
                    "value": "年代と関係を見比べる",
                    "decision": "rejected",
                    "reason": "架空の候補に根拠が付いていない",
                }
                for category, kind in (
                    ("chronology", "timeline"),
                    ("hypotheses", "table"),
                )
            ],
            "zero_reason": "グラフの比較候補はあるが、裏付けのある値・年代や比較情報はない。",
        },
    }


def review_fixture(generation, assets=(), independent="pass"):
    return {
        "generation": generation,
        "chronology_review": {
            "phases_and_order_checked": True,
            "findings": "年代の種別と前後関係を架空データで照合した。",
            "node_notes": {
                "node_unknown": "この架空の概念は前後を判断できる資料がない。"
            },
        },
        "figure_review": {
            "coverage_adequate": True,
            "findings": "図表候補と不採用理由を確認した。",
            "assets": {},
        },
        "representative_review": {
            "coverage_adequate": True,
            "findings": "架空の対象を確認",
        },
        "research_complete": True,
        "semantic_pass": True,
        "artifact_pass": True,
        "independent_review": independent,
        "illustration_review": {
            "coverage_adequate": True,
            "findings": "架空の探索範囲と選択理由を照合",
            "assets": [
                {
                    "asset_id": key,
                    "caption_matches": True,
                    "historically_appropriate": True,
                    "rights_confirmed": True,
                    "explanatory_value": "資料の色と形を区別できる",
                }
                for key in assets
            ],
        },
    }


def representative_fixture():
    return {
        "scope": "架空の代表例を調査",
        "searches": [
            {"id": "s1", "query": "架空の対象", "result": "架空の代表例を1件確認"}
        ],
        "candidates": [
            {
                "id": "rep1",
                "label": "架空の人物",
                "kind": "人物",
                "search_ids": ["s1"],
                "source_urls": [SOURCE],
                "decision": "adopted",
                "reason": "主題に直接関連する",
                "node_ids": ["node_old"],
            }
        ],
    }


def render_fixture(run, data, **kwargs):
    progress = read_progress(run)
    scene = layout(data, progress["run_id"], progress["query"], run_dir=run)
    scene["graph_generation"] = progress["active_generation"]
    emit(scene, run / "render", **kwargs)
    return scene


def assess(run, data, scene, review):
    return judge(data, scene, review, progress=read_progress(run), run_dir=run)


def test_cli_init_creates_one_illustration_task(tmp_path, capsys):
    assert main(["init", "架空の例", "--runs", str(tmp_path)]) == 0
    run = Path(json.loads(capsys.readouterr().out)["run"])
    before = illustration_task(read_progress(run))
    assert before["status"] == "pending"
    assert illustration_task(resume(run))["task_id"] == before["task_id"]


def test_missing_search_blocks_completion_even_with_all_review_flags(tmp_path):
    run = create_run(tmp_path, "架空の未探索")
    data = sample()
    gen = commit_generation(run, data)
    scene = render_fixture(run, data)
    outcome = assess(run, data, scene, review_fixture(gen))
    assert outcome["scene"]["status"] == "pass"
    assert outcome["status"] == "incomplete"
    assert outcome["illustrations"]["task_status"] == "missing"
    assert "has not been recorded" in outcome["illustrations"]["reason"]


@pytest.mark.parametrize(
    "independent,status",
    [
        ("pass", "complete"),
        ("unavailable", "complete_limited_review"),
        ("not_run", "incomplete"),
    ],
)
def test_documented_zero_images_retains_review_statuses(tmp_path, independent, status):
    run = create_run(tmp_path, "架空の探索済み")
    data = sample()
    gen = commit_generation(run, data)
    search = search_fixture()
    save_illustrations(run, search, complete=True)
    save_representatives(run, representative_fixture(), complete=True)
    scene = render_fixture(run, data, force_pdf_error=True)
    outcome = assess(run, data, scene, review_fixture(gen, independent=independent))
    assert outcome["status"] == status
    assert outcome["illustrations"]["status"] == "pass"
    assert outcome["illustrations"]["formats"] == {"svg": 0, "html": 0}
    assert outcome["illustrations"]["zero_reason"] == search["zero_reason"]


def test_draft_survives_resume_and_unresolved_candidate_blocks_completion(tmp_path):
    run = create_run(tmp_path, "架空の中断")
    data = sample()
    gen = commit_generation(run, data)
    report = search_fixture()
    report["candidates"][0]["decision"] = "pending"
    task = save_illustrations(run, report)
    with pytest.raises(ValidationError, match="unresolved candidate"):
        save_illustrations(run, report, complete=True)
    resumed = resume(run)
    saved = illustration_task(resumed)
    assert saved["task_id"] == task["task_id"]
    assert saved["status"] == "retryable"
    assert saved["task_id"] in resumed["frontier_task_ids"]
    assert saved["illustration_report"] == report
    assert resumed["active_generation"] == gen
    scene = render_fixture(run, data)
    assert assess(run, data, scene, review_fixture(gen))["status"] == "incomplete"


def test_unclear_rights_and_unexplained_zero_cannot_be_finalized(tmp_path):
    run = create_run(tmp_path, "架空の権利不明")
    commit_generation(run, sample())
    report = search_fixture()
    report["candidates"][0].update(decision="adopted", asset_id="asset_fixture")
    with pytest.raises(ValidationError, match="verified reuse"):
        save_illustrations(run, report, complete=True)
    report["candidates"][0]["decision"] = "rejected"
    del report["zero_reason"]
    with pytest.raises(ValidationError, match="no reason for zero"):
        save_illustrations(run, report, complete=True)


def test_adopted_image_and_artifact_drift(tmp_path):
    run = create_run(tmp_path, "架空の図版あり")
    data = sample()
    asset_id = "asset_fixture"
    path = run / "staging/photo.png"
    Image.new("RGB", (40, 30), "#537EA4").save(path)
    data["assets"] = [
        {
            "id": asset_id,
            "schema_version": "0.5",
            "asset_type": "photo",
            "node_id": "node_old",
            "local_path": "staging/photo.png",
            "caption": "架空の写真",
            "source_url": SOURCE,
            "license": "CC0",
        }
    ]
    gen = commit_generation(run, data)
    report = search_fixture()
    report["candidates"][0].update(
        decision="adopted",
        rights="cleared",
        rights_note="CC0",
        rights_url=RIGHTS,
        asset_id=asset_id,
    )
    save_illustrations(run, report, complete=True)
    save_representatives(run, representative_fixture(), complete=True)
    scene = render_fixture(run, data)
    full = review_fixture(gen, [asset_id])
    result = assess(run, data, scene, full)
    assert result["status"] == "complete"
    assert result["illustrations"]["formats"] == {"svg": 1, "html": 1, "pdf": 1}
    assert assess(run, data, scene, review_fixture(gen))["status"] == "incomplete"
    path.rename(path.with_suffix(".moved"))
    assert "photo.png" in assess(run, data, scene, full)["illustrations"]["reason"]
    path.with_suffix(".moved").rename(path)
    pdf = run / "render/map.pdf"
    pdf.unlink()
    assert assess(run, data, scene, full)["status"] == "incomplete"


def test_stale_search_and_render_require_recheck_after_merge(tmp_path):
    run = create_run(tmp_path, "架空の更新")
    data = sample()
    gen = commit_generation(run, data)
    report = search_fixture()
    save_illustrations(run, report, complete=True)
    original = render_fixture(run, data)
    updated = commit_generation(run, data)
    assert gen != updated
    assert (
        "active generation"
        in assess(run, data, original, review_fixture(gen))["illustrations"]["reason"]
    )
    assert illustration_task(resume(run))["status"] == "retryable"
    save_illustrations(run, report, complete=True)
    assert (
        "rerender"
        in assess(run, data, original, review_fixture(updated))["illustrations"][
            "reason"
        ]
    )


def test_saved_pharaoh_generation_can_render_and_remains_unverified_for_figures(
    tmp_path,
):
    root = Path(__file__).resolve().parents[1] / "runs"
    runs = sorted(root.glob("*_ファラオ_*"))
    if not runs:
        pytest.skip("local Pharaoh research run unavailable")
    original = runs[-1]
    generation = json.loads((original / "progress.json").read_text())[
        "active_generation"
    ]
    data = load_generation(original / "snapshots" / generation)
    counts = validate(data)
    run = create_run(tmp_path, "ファラオ")
    for asset in data["assets"]:
        if local := asset.get("local_path"):
            target = run / local
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((original / local).read_bytes())
    gen = commit_generation(run, data)
    scene = render_fixture(run, data)
    outcome = assess(run, data, scene, review_fixture(gen))
    assert outcome["scene"]["node_count"] == len(counts["adopted_node_ids"])
    assert outcome["scene"]["edge_count"] == len(counts["adopted_edge_ids"])
    assert outcome["illustrations"]["image_count"] == sum(
        "bbox" in asset or "figure_bbox" in asset for asset in scene["assets"]
    )
    assert outcome["illustrations"]["task_status"] == "missing"
    assert outcome["status"] == "incomplete"
