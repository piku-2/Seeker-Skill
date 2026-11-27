# Truth Seeker v1.3

根拠付きの歴史調査から、一枚の時系列PDFとインタラクティブなHTML知識地図を生成するスキルです。
このスキルで作った資料は商用利用できません。

## クイックスタート

**Codex（Sol）での利用を推奨しています。**
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
uv run pytest -v -rs
```

`pyproject.toml` に定義された依存パッケージをインストールし、仮想環境を準備します。

### 実行フォルダの命名規則

実行フォルダは **`YYYYMMDD_HHMM_テーマ_ID`** の形式で作成されます。

- **日時**: 調査開始日時（JST 日本時間）
- **テーマ**: 検索クエリ（日本語対応、Windows/Linux禁止文字置換、最大30文字）
- **ID**: 同名・同時刻衝突防止用ランダムID (6文字)
- **検索テーマの全文**および**調査開始日時 (`created_at`) / 最終更新日時 (`updated_at`)** はフォルダ内の `manifest.json` に安全に保持されます。
- 内部ID（`run_id`）は従来どおり独立して保持され、既存の `run_xxxxx` フォルダとの完全な後方互換性があります。

正式成果物は一枚の `map.pdf` と Truth Seeker Explorer の `map.html` です。正式な代替形式として `map.svg` も生成します。配布用は `<run_dir>/render/`、確認済み画像を使う個人閲覧用は `<run_dir>/render-local/` に出力します。各形式は同じ `scene.json` を使い、PDFが利用できない場合は `render_result.json` に理由を記録します。

### Explorerで読む

`map.html` をブラウザーで直接開きます。左側の検索では名称・別名・説明・関連する用語が一致します。結果を選ぶとカードへ移動し、詳細、入力・出力関係、出典を確認できます。ホイールや拡大・縮小ボタン、ドラッグ、タッチのピンチで移動できます。絞り込みは複数条件を組み合わせられ、「表示条件を解除」で全採用対象と関係に戻ります。年代入力には紀元前を負の数で指定します。地図と検索はオフラインで動作し、外部出典を開くときはネット接続が必要です。

### 進捗・部分再生成

```bash
python -m truth_seeker status <run_dir>
python -m truth_seeker render <run_dir>
python -m truth_seeker render <run_dir> --format layout
python -m truth_seeker render <run_dir> --from-scene
python -m truth_seeker render <run_dir> --format html
python -m truth_seeker layout-metrics <run_dir>/render/scene.json
```

`status` の `summary` で実行中・再試行可能・保留のタスク、未解決事項、資料と図版の件数、出力別の最新QA、再開可能なgenerationを確認できます。

`--format layout` は下書きを保存します。`--from-scene` は有効な下書きがあれば全形式へ反映します。`--format pdf|svg|html` は公開済みの同一シーンから選んだ形式だけを修復・再生成します。generation、画像プロファイル、画像、他の成果物の検証に失敗した場合は、通常の `render` で全形式を作り直します。画像の掲載状態を変更した後も通常の `render` を使います。古いrunも通常の `render` で再生成できます。

ブラウザー回帰テストには開発環境の Playwright と Chromium を使います。引数に実際の `map.html` も追加できます。

```bash
PYTHONPATH=. python tests/test_explorer_v13.py /tmp/truth-seeker-browser
node tests/explorer_browser.cjs /tmp/truth-seeker-browser/map-3.html /tmp/truth-seeker-browser/map-1200.html
```

ノードとエッジの重要度はそれぞれL1〜L4で指定でき、未指定時はL2になる。`nodes.jsonl` または `edges.jsonl` の各レコードに `importance` を記録するか、`render --importance <file.json>` でノードID・エッジIDをキーにしたJSONを渡す。CLIで指定したレコードは描画時に優先し、判断理由と出典をシーンに記録する。歴史的重要度は主題との関係、歴史的な影響、転換点や合流点としての役割に基づいて判断する。

```json
{
  "node_example": {
    "level": 4,
    "reason": "主題の成立を決めた転換点",
    "source": "research"
  },
  "edge_example": {
    "level": 3,
    "reason": "後続機の設計を可能にした技術的依存",
    "source": "research"
  }
}
```

同じ年代のカードを年代帯の中で横に展開し、接続先の近さと関係の重要度で配置を調整する。エッジの色は関係種別、線種は根拠の確実性、太さは重要度に対応する。

写真・一次史料・模式図・地図・データグラフは `assets.jsonl` に登録できる。関連ノードに図版と出典の領域を確保してから配線する。調査と並行して同じrunの図版探索タスクを進め、`illustrations-save` で検索・権利・採否を記録する。未探索のままQAを完了できない。探索して0件だった場合は理由を記録する。手順とレビュー形式は[データ契約](references/data-contract.md#illustration-search-and-visual-review)を参照。

3形式は同梱のNoto Sans JP Regularを使い、共通シーンに記録した改行と座標で描画する。旧レイアウトの成果物に対して品質判定を行う場合は、先に現在の `render` で再生成する。フォントに収録されていない文字がある場合、描画前のQAが文字を報告する。同梱のフォントは Google Fonts の可変フォントからウェイト400を生成したもの。[配布元](https://github.com/google/fonts/tree/main/ofl/notosansjp)・[ライセンス](truth_seeker/fonts/OFL-NotoSansJP.txt)

### 写真・実物史料の探索と確認

`photo-candidates <run_dir>` で重要ノードと採用済みの代表対象を確認する。候補には人物写真、実物、画面、パッケージ、書影、公式ビジュアル、文書、図面、当時の広告、保存されたウェブページが含まれる。実画像の探索・取得・同定・権利・採否を `illustrations-save` の `photo_plan` で記録し、`image-inspect <path>` で取得内容を点検できる。権利が未確認でも、取得できるファイルはカタログに残す。取得と掲載可否は別の判断である。`rights_unavailable` は、取得済みでまだ掲載できない状態であり、取得前に探索を終える理由ではない。完了時は、実写真・実物史料の代表対象カバレッジと各出力への掲載を、生成図表とは別に確認する。有用な未取得候補を不採用理由だけで十分と判定しない。[写真のデータ契約](references/data-contract.md#documentary-image-coverage)

作品パッケージ、書影、公式ビジュアル、ゲーム画面も対象に含める。年代や仕様の説明であることだけでは `not_suitable` にしない。`images-save` で取得後のファイルと候補をまとめて保存し、`images-status` で未取得・失敗・掲載待ちを確認し、`images-stage` で確認済みの掲載可否に応じて登録する。`render --image-profile local` の個人閲覧用は標準の配布向けフォルダと分かれ、個別に確認した利用条件に従う。[画像収集と出力設定](references/data-contract.md#collecting-images-before-an-embedding-decision)

### 調査に使ったモデルとハーネス

```bash
python -m truth_seeker init "調査テーマ" --model "確認済みのモデル識別子" --harness "確認済みのハーネス名"
python -m truth_seeker resume "$RUN" --model "変更後のモデル識別子"
python -m truth_seeker execution-add "$RUN" --role subagent --phase research --model "確認済みの補助モデル"
```

明示入力のない項目はホストが設定した `TRUTH_SEEKER_MODEL` と `TRUTH_SEEKER_HARNESS` を読み、値がなければ `unknown` と記録します。履歴は `manifest.json` に保存され、`status` とExplorerの実行情報で確認できます。PDF・SVGの表題付近には主担当のモデル、ハーネス、地図シーンの生成日時を表示します。通常の再描画では生成日時を更新し、部分再生成や保存シーンの再利用では同じ生成日時を保持します。調査開始日時と成果物の公開日時は個別に保存します。

### 動的配置・図表・代表例

年代帯の中でカードと複数ノードの図表の寸法を使って配置を調整する。研究上の推定範囲と、明示的な先後関係による配置目安を区別し、根拠が足りない場合は年代不明の理由を記録する。

`figure-candidates <generation_dir>` で採用グラフから図表候補を抽出し、比較年表・系統図・構成図・学説比較・史料対照などの採否を `illustrations-save` で保存する。掲載数に固定枠はなく、調査完了時に候補の検討と実際の描画を確認する。代表的な人物・場所・遺物などは、系譜調査と並行して `representatives-save` に採否を記録する。[データ契約](references/data-contract.md)


### skill-repair

コードの修正案を作るときは `/skill-repair`（Codexでは `$skill-repair`）を使います。経験ログは `experience/local/` を見て、修正案と対象ファイル、テスト内容を提示します。本人が提案の `repair_id` を明示して承認するまではリポジトリを変更しません。

```bash
python -m truth_seeker repair-show <proposal>
python -m truth_seeker repair-apply <proposal> --approve <repair_id>
python -m truth_seeker git-check
```

`repair-apply` のテストコマンドは `python -m pytest` または `uv run pytest` に限られます。Git への commit と push は、表示した差分を見たうえでの別承認が必要です。

## ライセンス

コードは MIT License、同梱フォントは SIL Open Font License 1.1 です。詳細は [LICENSE](LICENSE) を参照してください。
