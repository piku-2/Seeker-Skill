# Structured graph data contract

Canonical rules are in `truth_seeker/model.py` and `Truth_Seeker_v0.5_FINAL_SPEC.md` (if present in local documentation). Every generation folder must contain `nodes.jsonl`, `edges.jsonl`, `claims.jsonl`, `evidence.jsonl`, `merge_history.jsonl`, `assets.jsonl`. JSONL means one JSON object per line, UTF-8. All records require `id`, `schema_version: "0.5"`. IDs are opaque and stable: `node_`, `edge_`, `claim_`, `evi_`, `merge_`, `asset_`.

- Nodes: `type`, `label`, `aliases: []`, `summary`, `dates: []`, `display_date`, `claim_ids: []`, `adoption_status`, `adoption_reason`, `provenance`. An optional `importance` object contains integer `level` from 1 to 4 and nonempty strings `reason` and `source`. Missing importance defaults to level 2. It describes historical significance independently of claim assessment.
- Edges: `source_id`, `target_id`, `relation_type`, `direction_meaning`, `label`, `claim_ids`, `adoption_status`, `adoption_reason`, `provenance`.
- Edges accept the same optional `importance` shape as nodes. Assess the historical significance of the relationship independently of its endpoints and claim assessment. The render importance JSON accepts both adopted node and edge IDs.
- Claims: `statement`, `subject_refs: [{"kind":"node|edge","id":"..."}]`, `supporting_evidence_ids`, `contradicting_evidence_ids`, `assessment`, `adoption_status`, `adoption_reason`, `verification`, `related_claim_ids`, `provenance`.
- Evidence: `title`, `source_url`, `locator`, `excerpt_or_summary`, `source_type`, `access_status`, `accessed_at`, `independence_group`, `rights_note`, `provenance`.
- Assets: register local rasters with `asset_type` set to `photo`, `primary_document`, `diagram`, `map`, or `chart`. Supply a `caption` or `title`, `license` or `rights_note`, and a source URL or cited `evidence_ids` with a source URL. Use `local_path` relative to the run and optional `sha256`. Associate a primary `node_id` and optional `related_node_ids` to reserve space near the historical subject. Charts use documented values, and diagrams distinguish explanatory reconstruction from original material. Legacy embedded figures can still be recovered from the run render.
- `adoption_status`: `candidate|adopted|pending|rejected`. `assessment`: `verified|favored|contested|hypothetical|unverified` (always preserve these five).
- `display_date`: `{"mode":"anchored","anchor_year":1234,"anchor_reason":"...","basis_claim_ids":[]}` or `{"mode":"unknown"}`. Historical BCE year numbering is astronomical: 1 BCE = year 0, 2 BCE = year -1. `dates` holds any nuanced alternate time intervals; never average conflicting estimates into a false fact.
- Each `provenance` minimum: `run_id`, `source_task_id`, `created_at`, `updated_at`, `origin`.
- Adopted edges can only connect adopted nodes. A source URL alone cannot justify marking a claim verified. All referenced claim and evidence IDs must exist.

The validation helper is intentionally strict about required references but does not automatically judge the truth of historical assertions. Document sources and genuine counterevidence in the agent research and QA report.

Layout revision 3 groups cards in chronological bands with relationship-weighted positions and stores each text line's content, baseline, font size and bounds in `scene.json`. An edge's `path` starts with its source port coordinates followed by triples of cubic Bézier control, control and end points. The last point is the target port. The graph JSONL schema remains at `0.5`.

## Illustration search and visual review

`init` creates one `illustration_search` task alongside the research tasks. `resume` adds it to a legacy run when needed and reopens a final report when the active generation has changed. Both paths reuse the same run and its snapshots.

Use image search when available. Follow the result to the museum, university, public archive or other original provider so the identity, caption and reuse terms can be checked. When only general search is available, search those repositories directly. Select subjects for explanatory value. Distinguish photographs and scans from explanatory reconstructions, and cite the underlying facts or data for original diagrams, maps and charts. Images with unresolved reuse terms stay out of the adopted assets. A source link alone may be registered as an `external_reference`, which does not count as an image.

`python -m truth_seeker illustrations-save <run> <report.json>` checkpoints a draft in the dedicated task's `illustration_report` inside `progress.json`. On interruption, `resume` restores it. Save drafts as findings accumulate. Stage approved image files inside the run and the corresponding records in `assets.jsonl`. After the ordinary `validate` and `merge`, repeat the command with `--complete` to finalize the report against the active generation. The command replaces the draft with the supplied report, so include previous findings when resuming.

A final search report contains:

| Field | Required contents |
| --- | --- |
| `scope` | What the visual search set out to cover and any access limits |
| `searches` | Records with unique `id`, actual `query`, `checked_urls` (possibly empty for a search with no results), and `result` |
| `targets` | Records with adopted `node_id`, `reason` for looking, `search_ids`, and `outcome`, including why the search did or did not yield a useful image. Cover every explicitly L3/L4 node and other nodes with high explanatory value. |
| `candidates` | Records with unique `id`, `node_ids`, `search_id`, checked `source_url`, `decision` (`adopted` or `rejected`), `rights` (`cleared`, `restricted`, or `unknown`) and `reason`. Adoption additionally requires `rights_note`, `rights_url`, and an `asset_id` matching the merged sources and owners. Drafts may contain `pending` decisions. |
| `zero_reason` | Required when no images were adopted. Describe why the actual searches and checks yielded no suitable, usable illustrations. |

For final QA, include the active `generation` in the ordinary review JSON and add `illustration_review`:

```json
{
  "coverage_adequate": true,
  "findings": "Describe what the figures explain and any limits of coverage.",
  "assets": [{
    "asset_id": "asset_example",
    "caption_matches": true,
    "historically_appropriate": true,
    "rights_confirmed": true,
    "explanatory_value": "Describe the specific feature this figure makes visible."
  }]
}
```

For zero images use `assets: []` and explain why the documented search was adequate. Awaiting source access or an unresolved relevant candidate is unfinished research. The QA gate checks the recorded search, associations, active generation, image payloads, and saved SVG/HTML/PDF artifacts as applicable. The reviewer judges historical accuracy, relevance, visual usefulness and reuse terms. Python checks that these judgments were recorded. A completed search may have zero images, and existing image-free generations can still be read and rendered. Completion under current QA requires the search and review records. A new generation requires rechecking the report and rerendering before QA. The CLI stores the latest result in `qa/illustration-result.json`.
