"""Produce an offline Explorer around the canonical SVG."""

import html
import json
from functools import cache
from pathlib import Path

from .chronology import KINDS
from .execution import summary
from .model import LayoutError


def payload(scene):
    nodes = (
        "node_id",
        "label",
        "summary",
        "bbox",
        "type",
        "importance",
        "assessments",
        "date_label",
        "date_placement",
        "display_date",
        "dates",
        "date_claim_refs",
        "anchor_year",
        "claim_refs",
        "source_numbers",
        "asset_refs",
        "aliases",
        "original_label",
    )
    edges = (
        "edge_id",
        "source_node_id",
        "target_node_id",
        "relation_type",
        "direction_meaning",
        "label",
        "importance",
        "assessments",
        "claim_refs",
        "source_numbers",
    )
    sources = ("number", "evidence_id", "title", "source_url", "locator")
    return {
        **({"execution": scene["execution"]} if "execution" in scene else {}),
        "date_kinds": KINDS,
        "width": scene["canvas_width"],
        "height": scene["canvas_height"],
        "nodes": [{k: n[k] for k in nodes if k in n} for n in scene["nodes"]],
        "edges": [{k: e[k] for k in edges if k in e} for e in scene["edges"]],
        "sources": [{k: s[k] for k in sources if k in s} for s in scene["sources"]],
        "research": scene.get("explorer_research", {}),
        "assets": [
            {
                "id": a["id"],
                "caption": a.get("caption", ""),
                "urls": a.get("source_urls", []),
                "bbox": a.get("reserved_bbox") or a.get("bbox"),
            }
            for a in scene.get("assets", [])
        ],
        "generation": scene.get("graph_generation"),
        "profile": scene.get("image_profile", "distribution"),
    }


def serialized(scene):
    return json.dumps(
        payload(scene), ensure_ascii=False, separators=(",", ":")
    ).replace("<", "\\u003c")


DATA_TAG = '<script id="explorer-data" type="application/json">'


def check_data(scene, document):
    if DATA_TAG not in document:
        if "explorer_research" in scene:
            raise LayoutError("explorer_data", "HTML Explorer data is missing")
        return  # Scenes saved before the Explorer can still be verified.
    text = document.split(DATA_TAG, 1)[1].split("</script>", 1)[0]
    if text != serialized(scene):
        raise LayoutError("explorer_data", "HTML Explorer data differs from the scene")
    if "execution" in scene and _execution_section(scene["execution"]) not in document:
        raise LayoutError(
            "explorer_data", "HTML execution summary differs from the scene"
        )


@cache
def _static(name):
    return (
        Path(__file__)
        .with_name("explorer_ui")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _execution_section(metadata):
    if metadata is None:
        return ""
    model, harness, generated = summary(metadata)
    escape = html.escape
    rows = []
    for item in metadata["history"]:
        parts = [item["recorded_at"], item["generation"], item["phase"], item["role"]]
        if item.get("task_id"):
            parts.append(item["task_id"])
        parts += [
            f"Model {item['model']} ({item['model_source']})",
            f"Harness {item['harness']} ({item['harness_source']})",
        ]
        rows.append(f"<li>{escape(' · '.join(parts))}</li>")
    detail = f"<p>Run {escape(metadata['run_id'])}</p>"
    if metadata.get("started_at"):
        detail += f"<p>調査開始 {escape(metadata['started_at'])}</p>"
    if rows:
        detail += "<ol>" + "".join(rows) + "</ol>"
    synopsis = f"Model {model} · Harness {harness} · Generated {generated}"
    return (
        f'<div class="execution"><p data-execution-summary>{escape(synopsis)}</p>'
        f"<details><summary>実行情報</summary>{detail}</details></div>"
    )


def render(scene, svg):
    title = html.escape(scene["title"])
    index = "".join(
        f'<li><a href="#n-{html.escape(n["node_id"])}" data-target="{html.escape(n["node_id"])}">{html.escape(n["label"])}</a></li>'
        for n in scene["nodes"]
    )
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | Truth Seeker Explorer</title><style>{_static("explorer.css")}</style></head>
<body><header><div><strong>Truth Seeker Explorer</strong><h1>{title}</h1></div>
<div class="camera"><button id="zoom-out" aria-label="縮小">−</button><output id="zoom-level"></output>
<button id="zoom-in" aria-label="拡大">＋</button><button id="fit">全体表示</button></div></header>
<main><aside id="browse" aria-label="地図の検索と絞り込み">
{_execution_section(scene.get("execution"))}
<label for="search">対象を検索</label><input id="search" type="search" placeholder="名称・別名・説明・用語" autocomplete="off">
<details open><summary>表示条件</summary><div id="filters">
<label>重要度<select id="level"><option value="">すべて</option></select></label>
<label>対象の種類<select id="kind"><option value="">すべて</option></select></label>
<label>関係の種類<select id="relation"><option value="">すべて</option></select></label>
<label>検証状態<select id="assessment"><option value="">すべて</option></select></label>
<label>年代<select id="period"><option value="">すべて</option><option value="anchored">年代の根拠あり</option>
<option value="estimated">推定年代</option><option value="sequence">先後関係による配置</option><option value="unknown">年代不明</option></select></label>
<fieldset><legend>期間（紀元前は負数）</legend><div class="range">
<input type="number" id="from" placeholder="開始年" aria-label="開始年" step="1"><input type="number" id="to" placeholder="終了年" aria-label="終了年" step="1"></div></fieldset>
<button id="reset">表示条件を解除</button></div></details>
<p id="count" role="status"></p><ul id="results"></ul><button id="more" hidden>さらに表示</button>
<noscript><style>#view{{overflow:auto}} #view>svg{{width:auto;height:auto}}</style><ul>{index}</ul></noscript></aside>
<section id="view" aria-label="時系列地図" tabindex="0" aria-describedby="controls-help">{svg}
<p id="controls-help">ドラッグで移動・ホイールで拡大縮小・矢印キーで移動</p></section>
<aside id="detail" aria-label="対象の詳細" hidden><button id="close" aria-label="選択を解除">×</button><div id="content"></div></aside>
</main>{DATA_TAG}{serialized(scene)}</script><script>{_static("explorer.js")}</script>
</body></html>"""
