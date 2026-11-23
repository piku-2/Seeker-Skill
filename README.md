# Truth Seeker v1.1

DeepResearchを使ってめっちゃ調べて巨大な一枚のPDFを生成するスキルです。
このスキルで作った資料は商用利用できません。

## クイックスタート

クローンしたリポジトリをCodexで開き、チャットの入力欄で `$` を打って `truth-seeker` を選び、調査テーマを続けて送ります。

```text
$truth-seeker 調べたいワード
```

Codex以外のハーネスを使う場合はチャット欄で `/` を打って `truth-seeker` を選び、調査テーマを続けて送ります。

```text
/truth-seeker 調べたいワード
```

レートリミットが来たり、途中で作業が止まってしまった場合はそのセッションで `resume-truth-seeker` を選ぶと、中断した調査を再開できます。

```text
$resume-truth-seeker
```

スキルの候補が表示されない場合は、「`.agents/skills/truth-seeker/SKILL.md` に従って、〇〇についてTruth Seekerで歴史知識マップを作成して」と送れます。AIエージェントがWeb検索、史料の読み取り、裏付けと異説の検証を担い、Pythonエンジンはデータ契約・チェックポイント・描画を担います。

## 必要環境

- Python 3.10以上
- Pythonライブラリ: `reportlab`, `Pillow`（※PDF検査用の `pymupdf` は任意）
- Web検索・資料確認に対応したAIエージェント（サブエージェント利用推奨）

※依存ライブラリが未インストールの環境のみ以下を実行してください：

```bash
pip install reportlab pillow
# または uv をお使いの場合: uv pip install reportlab pillow
```

## 開発・テスト（任意）

レイアウトの回帰テストを含むテスト一式は pytest で実行します。

```bash
python -m pytest -q
```

## CLIサンプル

```bash
python -m truth_seeker init "囲碁"
# runs/20261123_1040_囲碁_a1b2c3/ のような実行フォルダが作成されます
python -m truth_seeker task-add runs/<run_dir> "囲碁の起源"
python -m truth_seeker status runs/<run_dir>
python -m truth_seeker validate <complete-staging-generation>
python -m truth_seeker merge runs/<run_dir> <complete-staging-generation>
python -m truth_seeker render runs/<run_dir>
python -m truth_seeker resume runs/<run_dir>
```

### 実行フォルダの命名規則

実行フォルダは **`YYYYMMDD_HHMM_テーマ_ID`** （例: `runs/20261123_1040_囲碁_a1b2c3/`）の形式で作成されます。

- **日時**: 調査開始日時（JST 日本時間）
- **テーマ**: 検索クエリ（日本語対応、Windows/Linux禁止文字置換、最大30文字）
- **ID**: 同名・同時刻衝突防止用ランダムID (6文字)
- **検索テーマの全文**および**調査開始日時 (`created_at`) / 最終更新日時 (`updated_at`)** はフォルダ内の `manifest.json` に安全に保持されます。
- 内部ID（`run_id`）は従来どおり独立して保持され、既存の `run_xxxxx` フォルダとの完全な後方互換性があります。

`runs/<run_dir>/render/` に `scene.json`, `map.svg`, `map.html`, PDFが有効なら `map.pdf` を生成する。PDF出力に失敗した場合はSVGを正式代替とする。強制失敗テストには `render --force-pdf-error` を使う。

ノードの重要度はL1〜L4で指定でき、未指定時はL2になる。`nodes.jsonl` の各ノードに `importance` を記録するか、`render --importance <file.json>` でノードIDをキーにしたJSONを渡す。CLIで指定したレコードは描画時に優先し、判断理由と出典をシーンに記録する。歴史的重要度は主題との関係、歴史的な影響、転換点や合流点としての役割に基づいて判断する。

```json
{
  "node_example": {
    "level": 4,
    "reason": "主題の成立を決めた転換点",
    "source": "research"
  }
}
```

3形式は同梱のNoto Sans JP Regularを使い、共通シーンに記録した改行と座標で描画する。旧レイアウトの成果物に対して品質判定を行う場合は、先に現在の `render` で再生成する。フォントに収録されていない文字がある場合、描画前のQAが文字を報告する。同梱のフォントは Google Fonts の可変フォントからウェイト400を生成したもの。[配布元](https://github.com/google/fonts/tree/main/ofl/notosansjp)・[ライセンス](truth_seeker/fonts/OFL-NotoSansJP.txt)

品質判定は構造チェックだけでは完結しない。エージェントが意味と視認性を実際に検査した後にレビューJSON（`research_complete`, `semantic_pass`, `artifact_pass`, `independent_review`）を作り、`python -m truth_seeker qa ... --review ...` で判定する。独立レビュー不在で必須QAが成功した場合のみ `complete_limited_review`。

## 修復とGit

`$skill-repair` はローカルログから修正案を提示する。**明示承認前に変更しない**。承認後に作業ツリーを直接修正し、ファイル全体のバックアップを `repair_backups/` に置く。修正テスト失敗時の復元は本人単独利用向けで、修復途中の別編集まで完全に保護するものではない。修正の承認とは別に、commitとpushの一括承認を要求する。

`.gitignore` だけを過信せず `python -m truth_seeker git-check` で実ステージングを検査する。確認できた範囲でも、秘密検査には見逃しがあり得るため目視確認が必要。

## 現時点の限界

v1.1では、重要度に応じたカード、文字量に応じた高さ、カードを避けるベジェ経路を共通シーンで生成する。自動QAは採用ID、年代、文字の完全性、ノード同士とエッジの衝突を検査する。歴史的主張の検証と、密集したエッジの視認性は、成果物のレビューで確認する。

正式なAC-01〜AC-10の**利用ハーネスでの結合試験は別途必要**。テストが通ったからといって全ての受入条件を完了と表示しない。

## 検証結果

実装初版の検証済み部分と、実際のハーネスで未検証の条件は [RELEASE_STATUS.md](RELEASE_STATUS.md) を参照。正式受入が完了したと装わない。
