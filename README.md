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

## 必要環境

- Python 3.10以上
- uv
- Web検索・資料確認に対応したAIエージェント

### 依存関係のインストール

リポジトリのルートディレクトリで実行します。

```bash
uv sync
uv run pytest -v
```

`pyproject.toml` に定義された依存パッケージをインストールし、仮想環境を準備します。

### 実行フォルダの命名規則

実行フォルダは **`YYYYMMDD_HHMM_テーマ_ID`** の形式で作成されます。

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
