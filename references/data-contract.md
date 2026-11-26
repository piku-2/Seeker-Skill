# Structured graph data contract

Canonical rules are in `truth_seeker/model.py` and `Truth_Seeker_v0.5_FINAL_SPEC.md` (if present in local documentation). Every generation folder must contain `nodes.jsonl`, `edges.jsonl`, `claims.jsonl`, `evidence.jsonl`, `merge_history.jsonl`, `assets.jsonl`. JSONL means one JSON object per line, UTF-8. All records require `id`, `schema_version: "0.5"`. IDs are opaque and stable: `node_`, `edge_`, `claim_`, `evi_`, `merge_`, `asset_`.

- Nodes: `type`, `label`, `aliases: []`, `summary`, `dates: []`, `display_date`, `claim_ids: []`, `adoption_status`, `adoption_reason`, `provenance`. An optional `importance` object contains integer `level` from 1 to 4 and nonempty strings `reason` and `source`. Missing importance defaults to level 2. It describes historical significance independently of claim assessment.
- Edges: `source_id`, `target_id`, `relation_type`, `direction_meaning`, `label`, `claim_ids`, `adoption_status`, `adoption_reason`, `provenance`.
- Edges accept the same optional `importance` shape as nodes. Assess the historical significance of the relationship independently of its endpoints and claim assessment. The render importance JSON accepts both adopted node and edge IDs.
- Claims: `statement`, `subject_refs: [{"kind":"node|edge","id":"..."}]`, `supporting_evidence_ids`, `contradicting_evidence_ids`, `assessment`, `adoption_status`, `adoption_reason`, `verification`, `related_claim_ids`, `provenance`.
- Evidence: `title`, `source_url`, `locator`, `excerpt_or_summary`, `source_type`, `access_status`, `accessed_at`, `independence_group`, `rights_note`, `provenance`.
- Assets: register local rasters with `asset_type` set to `photo`, `primary_document`, `visual_source`, `work_visual`, `diagram`, `map`, or `chart`. Supply a `caption` or `title`, `license` or `rights_note`, and a source URL or cited `evidence_ids` with a source URL. Use `local_path` relative to the run and optional `sha256`. Associate a primary `node_id` and optional `related_node_ids` to reserve space near the historical subject. Charts use documented values, and diagrams distinguish explanatory reconstruction from original material. Legacy embedded figures can still be recovered from the run render.
- `adoption_status`: `candidate|adopted|pending|rejected`. `assessment`: `verified|favored|contested|hypothetical|unverified` (always preserve these five).
- Each `provenance` minimum: `run_id`, `source_task_id`, `created_at`, `updated_at`, `origin`.
- Adopted edges can only connect adopted nodes. A source URL alone cannot justify marking a claim verified. All referenced claim and evidence IDs must exist.

## Chronology and placement

`dates` stores historical assertions. Each entry can have `kind`, `status` (`confirmed` by default or `estimated`), `earliest`, `latest`, `label` and `basis_claim_ids`. Use null for an open boundary. Astronomical numbering represents 1 BCE as 0, 2 BCE as -1 and 1 CE as 1. Preserve competing ranges separately. Use distinct kinds for the narrated setting (`narrative`) and the historical phases of a text. Historical kinds include `event`, `activity`, `composition`, `editing`, `production`, `discovery`, `translation`, `transmission`, `attestation`, `language` and `reception`.

| `display_date.mode` | Inputs and display |
| --- | --- |
| `anchored` | `anchor_year` and optional `kind`, `anchor_reason` and `basis_claim_ids`. A typed anchor must match a confirmed range for its phase if one exists. Untyped legacy anchors retain their existing labels. |
| `estimated` | Historical `kind`, `earliest` and/or `latest`, `basis_claim_ids` included in the node’s claims, `anchor_reason`, and `uncertainty`. Choose the research-supported range before placing the card. The default layout year is the midpoint or the single known boundary. An optional `anchor_year` must lie inside the range. |
| `sequence` | Historical `kind`, `edge_ids`, `anchor_reason`, and `uncertainty`. Each edge must connect an independently dated node that bounds the placement. The renderer uses the strongest lower and upper bounds and the median of the reference anchors, confined to the bounds and the displayed order. Explicit `anchor_year` is allowed within those bounds. The card labels this as a placement guide. |
| `unknown` | Keep it in the undated region and supply a `reason` describing the missing evidence or incompatible interpretations. Legacy records without a reason can still be loaded and drawn. Supply the explanation in the QA review if rechecking a legacy graph. |

For sequence placement, annotate the adopted edge with `"chronology": {"source_kind": "editing", "target_kind": "translation", "basis_claim_ids": ["claim_example"]}`. Its direction asserts that the named source phase precedes the target phase. Match both endpoint kinds and cite supporting claims on the edge. Ordinary influence, resemblance, context and comparison edges never contribute date bounds. The machine rejects missing support, reversed bounds and cycles. A phase may be supported by an accessible adopted claim assessed as verified, favored or contested. Reviewers must assess what the claim actually establishes. Unverified phase dates and the chronology inside a story can be retained in `dates` alongside a different historical placement.

The common scene records the resolved year and bounds in `date_placement`. The card and chronology guide distinguish a dated anchor, a research estimate, placement based on precedence, and a node without a usable date. Only the range and phase are presented as the estimated date. The layout year remains a coordinate. This leaves line styles available for edge uncertainty.

Include `chronology_review` in the generation’s QA. Set `phases_and_order_checked` after checking what each date refers to, the basis of each estimate and whether sequence edges support precedence. Record specific `findings` and use `node_notes` keyed by node ID for each estimate, sequence placement, and unexplained legacy unknown. Save evolving estimates in staging or in a new, validated generation. Preserve the earlier generation.

## Displaying original spellings and translated labels

A record can include an optional `display` object with nonblank strings for the map. Retain the original wording in the ordinary fields. For example, an evidence record may have `"locator": "מגילת התרי־עשר"` and `"display": {"locator": "十二小預言書の巻物"}`. The display text is used consistently in the map and HTML index. The original records and citation URLs remain in the generation.

| Record | Allowed keys in `display` |
| --- | --- |
| Node | `label`, `summary` |
| Evidence | `title`, `locator` |
| Asset | `caption`, `creator`, `rights_note` |
| Generated asset | Asset keys plus text fields such as `figure.columns.0`, `figure.rows.0.cells.1`, `figure.rows.0.label` and `figure.unit` when the corresponding field exists. Indices start at 0. |

Keep enough information in each replacement to identify the document, locator or credited person. If no replacement is given, the original text is used. An asset can override `caption` even if its original wording is stored in `title`. Figure values and sources, hyperlink destinations and the license field continue to use the original data.

Run `python -m truth_seeker font-check <generation_dir> --title "<topic>"` on staged or saved data. It reports each unsupported character by record ID and field path, including `display.` paths when a replacement itself contains unsupported characters. The normal render command runs the same check before laying out the map.

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

## Collecting images before an embedding decision

Photos, original documents, historical visual records and work visuals all count as evidence-bearing imagery. Use `work_visual` with `work_media` set to `package`, `screenshot`, `key_visual`, `poster`, `cover`, `promotional`, `artwork` or `other`. Record the edition or adaptation actually depicted. Generated figures retain their own quality review.

Use image search and the original provider’s item page to distinguish a thumbnail from the available source image. Fetch through the host’s available tools and respect access restrictions. Use `python -m truth_seeker images-save <run> <batch.json>` to import a batch of candidates. It checks local input files under the run, records dimensions and their checksums, and keeps acquired files apart from graph assets in `image-library/`. A provider page alone is enough to save a discovery. A failed download can be recorded with `acquisition: "failed"` and `acquisition_note`. The command does not make a network request or grant permission.

Each item in `candidates` supplies `id`, `node_ids`, `media_kind`, `source_page_url`, optional `media_url`, and the relative `input_path` when the file is acquired. Identify the image with `authenticity: "verified"`, `provider`, `identifier`, `caption`, `credit` and a `verification` note after examining it. Record `terms_status` separately as `unreviewed`, `license_stated`, `terms_stated` or `not_found`. Include `terms_urls` when stated. Unreviewed items and items without a reuse decision remain in the catalog. `images-status <run>` shows discoveries, acquired files, identification and approval counts.

A shared record in the batch’s `policies` has an `id`, `profile` (`local` or `distribution`), `basis` (`license`, `permission`, `public_domain` or `legal_exception`), `source_url`, and applicable `conditions`. For a legal exception, include the `jurisdiction`. A candidate’s `uses` maps each reviewed profile to a `decision` of `approved` or `withheld`, a concrete `reason`, and for approval a `policy_id`. A provider policy supplies reusable evidence. Apply it individually to the identified subject, edition and purpose. Local and distribution approvals are independent. Private or noncommercial intent by itself establishes neither. A changed source, image or identification clears prior verification and use decisions unless the changed record is explicitly reviewed again.

Run `images-stage <run> <run>/staging/<name>` to prepare registered assets and candidate drafts. It keeps prior non-catalog assets. An identified image without an embedding approval is presented as a source link. After merging, include every catalog ID in the illustration report, and use `rights: "scoped"` with the applicable policy URL and matching asset terms for adopted entries. Record the applicable policy URLs in the underlying search. Keep useful withheld entries with their reasons. QA reconciles the catalog, active generation and search report.

The default `render <run>` places distribution output in `render/`. Use `render <run> --image-profile local` for a separate `render-local/` output marked for personal use. Only approvals for the selected purpose provide embedded bytes. Other identified catalog imagery remains a source link. Review and run QA with the same `--image-profile`. Local QA writes under `qa/<generation>/local/`, separate from default distribution status and manifest artifacts. The software checks that a decision and its evidence are present. It cannot decide if a law, license or permission actually applies.

## Documentary image coverage

Run `python -m truth_seeker photo-candidates <run>` after adopting nodes or checkpointing representative research. Check the proposed people, artifacts, technology, places, events, documents and organizations, including representative subjects at low importance. For each representative candidate, the first adopted `node_ids` entry identifies its subject by default. Set `subject_node_ids` explicitly for several subjects or when the first association is contextual. The command proposes subjects from the graph and saved representative report. Add topic-specific subjects whose appearance or source material would clarify the history.

Use `asset_type: "photo"` for a photograph, `"primary_document"` for an original record or documentary scan, and `"visual_source"` for another authentic visual artifact such as a historical print or drawing. Contemporary external explanatory materials continue to use `diagram`, `map` or `chart`; Truth Seeker explanatory figures use `generated`. A file extension supplies no evidence of authenticity. Legacy records continue to load and render.

Record a separate `photo_plan` inside the illustration report. The illustration report’s shared searches and candidates carry the details, so an asset, source and rights decision only need one entry.

| Field | Contents |
| --- | --- |
| `photo_plan.scope` | The documentary image investigation and its limits. |
| `photo_plan.targets` | One record with `node_id`, `decision` (`search` or `not_suitable`), and a specific `reason` for each suggested node. A searchable target also needs explanatory `value`, `search_ids`, and `outcome`. `not_suitable` also needs `visual_kinds_considered`, naming the kinds that were actually considered: portrait, object photo, screenshot, package, cover, key visual, poster, document, manuscript, inscription, drawing, advertisement, archived page, or other. Products and software must consider a screenshot, package or cover. A statement that a card is about a date, specification or relationship does not justify exclusion. |
| `searches[]` | Mark documentary searches with `purpose: "photo"`, `execution: "executed"` and `strategy` (`institution`, `catalog_id`, `alternate_name`, `image_search` or `archive`). Include the actual query, consulted pages and findings. Record blocked attempts as `execution: "blocked"`. They remain visible in history and cannot satisfy a target’s search requirement. Give important unillustrated targets a second, distinct search strategy and report the results. |
| `candidates[]` | Extend the ordinary candidate with `media_kind` (the documentary asset type), `depicted_node_ids` (the subjects the image actually shows), `provider`, the catalog `identifier`, and boolean `useful`. Keep contributing or otherwise associated nodes in the ordinary `node_ids` field. In this way a photographer can be linked without counting as the person shown. |
| Acquisition | Record `acquisition` as `not_attempted`, `failed` or `verified`. For failures, keep `attempts` entries with the tried `url` and `result`. Retain attempts when another source succeeds. Save the actual image inside the run, and record its `local_path` and `sha256` in the asset. `python -m truth_seeker image-inspect <path>` verifies a local download and prints its dimensions, render format and checksum. |
| Adoption | Use the checked object/catalog page as `source_url` in the candidate and `source_page_url` in the asset. Optionally store the media URL in the asset’s `source_url`. Keep the provider credit in `creator` and the exact reuse decision in the candidate and asset rights notes. The same checked provider policy URL can be reused across entries after verifying its applicability to each object. |
| Rejection | Supply `rejection` (`unsuitable`, `access_unavailable`, `acquisition_failed`, `rights_unavailable`, `scope_unavailable` or `redundant`) and a concrete reason. These states are not interchangeable. `access_unavailable` means the file was not fetched because of `acquisition_block` `not_offered` or `access_restricted`, with `acquisition_block_note`. Do not use unknown rights as that block. `acquisition_failed` keeps `attempts` (`url`, `result`) and `alternatives_considered`. `rights_unavailable` is allowed only after `acquisition: verified`, with the measured `sha256` and `local_path` retained and `rights` still `unknown` or `restricted`. It withholds embedding. It does not delete the candidate or close the search before a fetch. Redundancy requires an adopted `replacement_asset_id` depicting the same subjects. A cleared, acquired, useful image cannot be rejected without that replacement. |

Start with the subject’s original repository or public institution. Use original spellings, catalog IDs, alternate repositories or image search to fill gaps. Keep provider item pages, media URLs and rights URLs distinct. Respect access limits and keep useful unresolved images open until the related investigation is complete. Credit each image and check file readability, caption, proportions and rights in the normal illustration review.

In final QA include `photo_review` findings and `checks` with `node_id`, a separate `query`, consulted `checked_urls`, a specific `finding`, and `unresolved_useful_candidate`. That flag must be false only when the node has no useful candidate left unfetched, unidentified, or uncleared. Check every suitable subject without an embedded documentary image, and use a separate lookup for a sample of subjects when all are illustrated. In `excluded`, provide a reviewed reason keyed by each `not_suitable` node ID. Repeat weak date-or-specification dismissals are rejected. Use independent review when available.

Set `coverage_adequate` true only when every useful candidate is closed by a real search result: nothing suitable was found, access or fetch failed with the records above, a duplicate was replaced, or a cleared image was embedded. An acquired image that is still waiting on reuse terms, or is restricted, is not full coverage. For that case set `coverage_adequate` false, `visual_coverage` to `constrained`, and `constraint` to the specific limit. Historical research can still reach `complete` or `complete_limited_review` when the constraint is honest and no useful candidate remains unresolved. Python verifies records, checksums of acquired files, and actual artifacts. The reviewer checks authenticity, worthwhile coverage and whether an accessible useful candidate was missed.

QA reports, independently of generated figures: search targets, useful candidates, fetches not attempted, fetch successes, fetch failures, identified files, rights still pending, images that cannot be embedded, images actually present in the output, and representative subjects with an embedded image. Profile approvals remain the catalog counts for `local` and `distribution`. Only output payloads count as embedded. A run with no obtainable documentary image can pass with those zero counts. A useful image left unfetched cannot.

## Generated vector figures

An `assets.jsonl` record can use `asset_type: "generated"` with a `figure` object, caption, original credit in `rights_note`, and accessible `evidence_ids`. Link it to adopted nodes using `node_id` or `related_node_ids`. Use `placement: "gallery"` for a separate comparison panel that keeps its node associations. Use `placement: "band"` for a panel shared by at least two adopted nodes, or leave placement unset for an inline panel. A band panel is placed near the highest-ranked dated owner, preferring a designated `node_id` when one is given. Undated panels can share the undated band. Band panels participate in the same width and row calculations as cards. Dense bands are scored by occupied width and height as well as overall dimensions. The edge router treats reserved panel areas as obstacles. The dated guide for a lower row is clipped behind cards and panels. Photo and scan records still use the raster path. Multiple panels may share a node, and full graph coverage takes priority over output dimensions.

| `figure.kind` | Content |
| --- | --- |
| `table` | `columns` and `rows` containing one `cells` array per row |
| `relations` | `edge_ids` referring to adopted graph edges whose endpoints are panel owners. Each edge must share a cited source with the panel. |
| `timeline` | `node_ids` of at least two related nodes with historical dates or research estimates. Cite supporting evidence for each node’s date. The figure uses the same phase, range and estimate labels as the map. |
| `lineage` | `edge_ids` grouped by source. Actual edge types and qualification are shown. Use the explanatory meaning to distinguish precedence from textual or technical continuations. |
| `composition` | `edge_ids` grouped by the receiving whole when `meaning: "component_of"` is present. Record source → target where the source is the component. Untyped legacy composition draws ordinary relationship rows. |
| `bar` | `unit` and `rows` with a `label` and finite numeric `value` |
| `distribution` | `rows` with `label`, `latitude` and `longitude`. Displays a coordinate plot without an inferred basemap. |


Supply a subject-specific `figure.title` with `figure.meaning` for a newly authored panel. Explain the subject and what the panel makes visible in its title and caption. The title may be translated or adapted for glyph coverage via `display["figure.title"]`. Archived figures can omit both keys.

- `technical_succession`, `product_development`, `organization_change`, `conceptual_influence`, `textual_transmission`, `textual_revision` and `component_of` require the same `semantic_role` on every referenced graph edge. Record the direction with the edge and justify the classification using its cited claims.
- `dependency` requires dependency edges. `precedence` accepts evidenced chronology edges or a dated timeline. Chronology by itself does not establish influence or transmission.
- `comparison` and `source_concordance` apply to tables, `geographic_distribution` applies to coordinate plots, and `quantitative_comparison` applies to tables and bar charts. `dispute` applies to comparison tables and edge rows with opposition, hypotheses or qualified claims. A single panel with intentionally mixed edge meanings can use `relationship_overview` and state the distinctions in the title or caption.
- Figure candidates retain stable IDs. The `category` and `suggested_kind` distinguish chronological edges from lineage interpretations, and dependencies from composition. Reviewers use the existing per-figure `findings` to explain why the title, meaning, direction, claims and numbers agree with the sources and what the panel adds. Machine QA checks compatible types, declared roles, chronology and references. It also identifies exact duplicate data across panels. Source interpretation and useful overlap differ by subject and require semantic review.

Every table, numeric or coordinate row must reference a nonempty subset of the panel's `evidence_ids`. Supply only confirmed values. The engine tests the formal references and geometry; the reviewer checks whether each source actually supports the content. Figures render as common text and vector rectangles in PDF, SVG and HTML. In the illustration search record, use `rights: "original"` and a `rights_note` for generated panels, citing a checked evidence URL. External images continue to require cleared reuse terms and a rights URL. Include generated panels in the same visual review as photos.

## Figure opportunities and completion

Run `python -m truth_seeker figure-candidates <generation_dir>` on a staging or saved generation. It suggests structures from adopted dates, documents and relationships. Review these leads for importance and add topic-specific opportunities, including comparisons, chronology, lineage, composition, competing hypotheses, source concordances, geographic data, quantities and networks. Use the already verified node, edge, claim and evidence IDs. For hypotheses and source concordances, an ordinary evidence-backed `table` keeps competing positions or witness readings side by side.

In the illustration report, save `figure_plan` with:

| Field | Contents |
| --- | --- |
| `checks` | A specific finding per category using keys `comparisons`, `chronology`, `lineage`, `composition`, `hypotheses`, `sources`, `geography`, `quantities` and `relationships`. |
| `decisions` | Each candidate’s `id`, `kind` (one of the figure kinds), adopted `node_ids`, explanatory `value`, `decision` and `reason`. Cover every ID returned by `figure-candidates` with at least two related nodes from the suggestion. Use an `asset_id` for adoption. Rejections need the concrete evidence or explanatory limitation. |
| `zero_reason` | Required if no generated figures are adopted, even if photographs or scans are available. |

Checkpoint the plan with `illustrations-save` while research continues. Finalization requires every decision to be resolved and every generated figure to have a matching adopted decision on the active generation. A resumed run retains its draft, and a legacy finished illustration report with no figure plan reopens for review. There is no figure count requirement.

In `figure_review`, record `coverage_adequate` and specific `findings`. The `assets` object is keyed by generated asset ID. Each entry records `data_matches`, `uncertainty_clear` and `findings`. Use an empty object for a reviewed zero-figure run. Use `illustration_review` for the common caption, rights and visual checks on all assets. The completion gate verifies the plan, review records and actual output coverage. Reviewers check historical meaning, conflicting claims, approved reuse and explanatory value. The CLI saves the review and result under `qa/<generation>/`.

## Representative-subject review

`init` and `resume` plan a separate `representative_search` task. Search for subjects that concretely explain the theme, such as people, places, artifacts, buildings, cultural practices and events. Consider thematic relevance and explanatory value along with recognition. Use evidence-backed graph links that express the actual relationship.

Record a `scope` and `searches` with unique `id`, actual `query` and `result`. Each entry in `candidates` needs a unique `id`, `label`, `kind`, `search_ids`, consulted `source_urls`, and a `decision` and `reason`. A final decision is `adopted` or `rejected`. Use `node_ids` to link each adopted candidate to the active graph. Leave it empty for a rejection. Save progress with `representatives-save <run> <report.json>`, then finalize with `--complete` after merging. Include a `representative_review` object with `coverage_adequate` and concrete `findings` in the current generation's QA. The reviewer determines whether the search covers the theme. The engine checks that decisions are resolved and adopted subjects have valid graph links.

Layout revision 4 uses measured card dimensions inside chronological bands. The placement search tries geometrically increasing shelf widths up to the widest unbroken band, minimizing estimated vertical extent and penalizing unnecessary width. It stops expanding when the candidate range is exhausted and only accepts improvements above one point in the score. Bounded sweeps can tighten horizontal graph connections without moving a card to another date. The renderer still accepts revisions 2 and 3.


## Explorer and saved render bundles

The shared scene remains the basis of the one-page PDF, Explorer HTML and SVG. New scenes carry node aliases, original labels, date claim references, edge direction meanings and numbered citations. The `explorer_research` object holds the claims and evidence referenced by the adopted graph and its date assertions. HTML includes the exact rendered SVG plus searchable JSON derived from this scene. Filters affect browser visibility. They preserve the full graph and hide connections when either endpoint is hidden. Automated artifact checks validate the SVG, Explorer payload and PDF against the scene. Historical and visual review still use the existing QA workflow.

The default `render` validates the active generation, profile and image catalog before rendering all formats. A saved scene records the snapshot index fingerprint, embedded raster file fingerprints, run ID, generation and image profile in `render_inputs`. Each output profile has its own directory and receipt. `render_result.json` stores `scene_sha256`, per-format `artifact_sha256`, `formal_formats` and the PDF fallback reason when applicable. The legacy `primary_format` still indicates PDF availability and fallback. Older runs remain readable and can receive a current receipt through a full render.

`render --format layout` stages `scene-pending.json` and `layout_result.json`. `render --from-scene` checks these dependencies before promoting the draft into a complete output bundle. `render --format pdf|svg|html` reuses only the published scene and checks the other artifacts before replacing the target. Publication happens after verification. If an older PDF cannot be retained within the new bundle, a failed re-render preserves the previous files. The receipt detects inconsistent files if publication is interrupted. Rerun the full render after source, image policy or generation changes.

`status` keeps all existing progress fields and adds a `summary` with task buckets, unresolved issues, evidence and image counts, output and QA state by profile, and the latest recoverable generation. `layout-metrics` reports crossings as sampled crossing pairs rather than individual intersection points. It also reports obstacle collisions, overlapping cards, edge lengths, congestion near important nodes, canvas dimensions and graph coverage.


## Execution metadata

`manifest.json` stores append-only `execution_history` entries with `model`, `harness`, individual origins (`explicit`, `environment`, `unknown`), `phase`, `role`, `generation`, `recorded_at` and an optional `task_id`. `init` and `resume` take `--model` and `--harness`; a configured host can supply `TRUTH_SEEKER_MODEL` and `TRUTH_SEEKER_HARNESS`. Use `execution-add` to append confirmed information about a past generation or auxiliary role. Existing manifest fields keep their roles. Missing values are `unknown`; a legacy run can have an empty history.

The scene preserves `run_id`, topic, run start time, scene generation time in UTC and recorded history. SVG carries the same record in `metadata`. PDF and SVG display a compact summary of the most recent main session. Explorer also exposes the full historical record. A fresh layout creates a new scene timestamp. Reusing a layout or repairing one output retains that scene timestamp. The render receipt records publication time separately. Appending execution history changes the render dependency fingerprint; regenerate the shared scene to publish the additional information.
