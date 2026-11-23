# Structured graph data contract

Canonical rules are in `truth_seeker/model.py` and `Truth_Seeker_v0.5_FINAL_SPEC.md` (if present in local documentation). Every generation folder must contain `nodes.jsonl`, `edges.jsonl`, `claims.jsonl`, `evidence.jsonl`, `merge_history.jsonl`, `assets.jsonl`. JSONL means one JSON object per line, UTF-8. All records require `id`, `schema_version: "0.5"`. IDs are opaque and stable: `node_`, `edge_`, `claim_`, `evi_`, `merge_`, `asset_`.

- Nodes: `type`, `label`, `aliases: []`, `summary`, `dates: []`, `display_date`, `claim_ids: []`, `adoption_status`, `adoption_reason`, `provenance`. An optional `importance` object contains integer `level` from 1 to 4 and nonempty strings `reason` and `source`. Missing importance defaults to level 2. It describes historical significance independently of claim assessment.
- Edges: `source_id`, `target_id`, `relation_type`, `direction_meaning`, `label`, `claim_ids`, `adoption_status`, `adoption_reason`, `provenance`.
- Edges accept the same optional `importance` shape as nodes. Assess the historical significance of the relationship independently of its endpoints and claim assessment. The render importance JSON accepts both adopted node and edge IDs.
- Claims: `statement`, `subject_refs: [{"kind":"node|edge","id":"..."}]`, `supporting_evidence_ids`, `contradicting_evidence_ids`, `assessment`, `adoption_status`, `adoption_reason`, `verification`, `related_claim_ids`, `provenance`.
- Evidence: `title`, `source_url`, `locator`, `excerpt_or_summary`, `source_type`, `access_status`, `accessed_at`, `independence_group`, `rights_note`, `provenance`.
- Assets: register local rasters with `asset_type` set to `photo`, `primary_document`, `diagram`, or `chart`. Supply a `caption` or `title`, `license` or `rights_note`, and a source URL or cited `evidence_ids` with a source URL. Use `local_path` relative to the run and optional `sha256`. Associate a primary `node_id` and optional `related_node_ids` to reserve space near the historical subject. Charts use documented values, and diagrams distinguish explanatory reconstruction from original material. Legacy embedded figures can still be recovered from the run render.
- `adoption_status`: `candidate|adopted|pending|rejected`. `assessment`: `verified|favored|contested|hypothetical|unverified` (always preserve these five).
- `display_date`: `{"mode":"anchored","anchor_year":1234,"anchor_reason":"...","basis_claim_ids":[]}` or `{"mode":"unknown"}`. Historical BCE year numbering is astronomical: 1 BCE = year 0, 2 BCE = year -1. `dates` holds any nuanced alternate time intervals; never average conflicting estimates into a false fact.
- Each `provenance` minimum: `run_id`, `source_task_id`, `created_at`, `updated_at`, `origin`.
- Adopted edges can only connect adopted nodes. A source URL alone cannot justify marking a claim verified. All referenced claim and evidence IDs must exist.

The validation helper is intentionally strict about required references but does not automatically judge the truth of historical assertions. Document sources and genuine counterevidence in the agent research and QA report.

Layout revision 3 groups cards in chronological bands with relationship-weighted positions and stores each text line's content, baseline, font size and bounds in `scene.json`. An edge's `path` starts with its source port coordinates followed by triples of cubic Bézier control, control and end points. The last point is the target port. The graph JSONL schema remains at `0.5`.
