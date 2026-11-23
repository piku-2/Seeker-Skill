---
name: resume-truth-seeker
description: Resume an interrupted Truth Seeker investigation from an existing run. Use when the user invokes /resume-truth-seeker, reports a rate limit or interruption, or asks to continue a paused Truth Seeker run.
---

# Resume Truth Seeker

## User contract

既存の調査だけを再開する。新しいrunを作成しない。
中断時の `progress.json`、タスク状態、成功済みgenerationを再利用し、完了済みタスクを再調査しない。
レートリミットや外部制約が解消していない間は、進捗を保持したまま安全に中断する。

調査を完走した場合のみ、通常の `/truth-seeker` と同じ品質検証・レンダー・PDF生成まで実施する。
調査内容を推測で補完せず、実際に確認できた事実、出典、検査だけを完了扱いにする。

## Working location

- リポジトリ直下で作業する。
- 既存runはリポジトリ内の `runs/` にある。
- Pythonの再開機能は再利用する: `truth_seeker/store.py` の `resume()` と `python -m truth_seeker resume <run_dir>`。
- Pythonはrun、generation、タスク状態、検証、描画を管理し、調査・裏付け・意味判断はホストAIエージェントが実施する。
- レンダー・QA・タスク更新も通常のTruth Seekerと同じCLIを使う。

## 1. 再開対象を特定する

1. `runs/` 配下の各ディレクトリから `manifest.json` と `progress.json` を確認する。
2. 候補は、完了済みではなく未完了の調査に限定する。
   - `progress.run_status` が `paused` または `complete_limited_review` のrun
   - タスク状態に `pending`、`running` または `retryable` が残るrun
   - 未完了のタスクやQAが残っているrun
3. `manifest.status == "complete"` または `progress.run_status == "complete"` のrunは再開候補から除外する。
4. ユーザーがrunディレクトリを明示した場合はそれを使用し、候補探索は不要。
5. 候補が一つなら質問せず再開する。
6. 候補が複数でも、現在のセッションのテーマ・run名・直前の調査内容から一意に特定できる場合は質問せず再開する。
7. 候補が複数で、セッション情報から一意に特定できない場合だけ、候補のテーマとrunディレクトリを提示して選択を求める。

候補提示には無関係な完了済みrunや推測による候補を含めない。

## 2. チェックポイントを検証する

対象を決めたら、必ず既存のCLI再開処理を実行する:

```bash
python -m truth_seeker resume <run_dir>
```

このコマンドは既存の `resume()` が次を行うことを利用する:

- `active_generation` のスナップショット整合性とチェックサムを検証する。
- 壊れたactive generationの場合は、検証済みの直近世代へフォールバックする。
- `running` タスクを `retryable` に戻し、既存の進捗を保持する。
- runとmanifestを `paused` にする。
- 新しいrunフォルダを作成しない。

コマンド失敗時は既存の世代・ファイルを上書きまたは削除せず、失敗内容と正常性を報告する。

再開後に `progress.json` を読み直し、次を確認する:

- `active_generation`
- `tasks[].status`
- `completed_task_ids`
- `blocked_task_ids`
- `frontier_task_ids`
- `last_successful_phase`

## 3. 未完了タスクだけを続行する

- `completed` タスクは再実行しない。
- `superseded` タスクは通常再実行しない。
- `retryable`、`running` または `pending` タスクだけを調査対象として選ぶ。
- 優先度やチェックポイントに基づき、未完了タスクを順に処理する。
- `blocked` タスクは、レートリミットや資料制約が解消して再試行可能になった場合だけ `retryable` に戻して処理する。
- 新しい意味のある調査分岐が見つかった場合のみ `task-add` で既存runへ追加する。これは新runではなく既存runのタスク追加である。
- 途中タスクの証拠・参照・mergeに問題がない場合のみ `task-update ... completed` にする。

レートリミットが解消していない場合は、進行中タスクを再試行可能状態に保ち、`progress.json`・manifest・既存のチェックポイントを更新したうえで中断する。

## 4. チェックポイントへ研究成果を保存する

未完了タスクの調査が完了し、必要なJSONLを構築したら通常のTruth Seeker手順を使う:

```bash
python -m truth_seeker validate <complete-staging-generation>
python -m truth_seeker merge <run_dir> <complete-staging-generation>
```

mergeは既存runの新しいgenerationとして保存する。
アクティブな `snapshots/*.jsonl` を直接上書きしない。
既存のstaging、snapshot、render、qaファイルを不要に削除しない。

## 5. 完走時の品質検証と生成

調査が本当に飽和した場合のみ完走とする。
通常の `/truth-seeker` と同じ手順で、以下を実施する:

1. `python -m truth_seeker render <run_dir>`
2. `render/scene.json`、`render/render_result.json`、SVG、HTML、利用可能ならPDFを検査する。
3. 全採用ノードと採用エッジ、時系列、未知日、異説表示、`ID` の安定性を確認する。
4. メインエージェントによる資料・意味・視覚レビューを実施する。
5. 利用可能なら独立レビュアーによる高リスクな因果・異説レビューを実施する。
6. レビューJSONを既存runの `qa/` または同等の明示的な位置へ保存し、実行する:

```bash
python -m truth_seeker qa <run_dir> --review <review.json>
```

機械検査だけで歴史的正確性や視覚品質を証明したことにしない。
独立レビューが利用不能でも、必要な検査を実際に通過した場合は限界を明示した `complete_limited_review` とする。
重要な資料・意味・視覚の問題が未解決なら完了扱いにしない。

## 6. レートリミット時

レートリミット、認証制限、資料アクセス制限などで調査を続けられない場合:

- 完了済みタスクを完了のまま保持する。
- 未完了または中断タスクを `retryable` または `blocked` として保存する。
- その時点までの通常チェックポイントを保持する。
- 新しいrunや空のgenerationを作成しない。
- runを `paused` のまま、正確な再開時点と未完了理由を報告する。

## 7. 保守と整合性

- READMEの再開手順と本Skillの手順を一致させる。
- 通常の再開・品質検証手順は `/truth-seeker` のSkillを基準にする。
- 詳細なデータ契約は `references/data-contract.md` を参照する。
- 再利用可能な既存Python機能を優先し、Skill内に同等の再開・検証ロジックを重複実装しない。
