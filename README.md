# Truth Seeker v1.0

自分専用の歴史知識グラフ研究・巨大地図生成ツール。Agent Skillsは **`truth-seeker` と `skill-repair` の2つだけ**。GitHubへ生成物・取得素材・ローカル研究データを載せない。

## 必要環境

- Python 3.10以上
- Pythonライブラリ `reportlab`, `Pillow`（PDFの検査に `pymupdf` は任意）
- Web検索・資料確認に対応したAIエージェント。サブエージェントが使える環境を推奨
- `.agents/skills` を認識する対応ハーネス。認識場所が異なる場合は利用するハーネスの仕様に応じ配置する

```bash
python -m pip install -e .
python -m pip install pytest     # テスト用（任意）
python -m pytest -q
```

## AIエージェントで使う

クローンしたリポジトリをAIエージェントで開き、以下を実行する。

```text
/truth-seeker 囲碁
/skill-repair
```

ハーネスでスラッシュ呼び出しがない場合、対象の `SKILL.md` を参照させて同じ目的のタスクを指示する。**`truth-seeker-tool` 自体はWebの歴史調査を実行しない**。AIエージェントがWeb検索、史料の読み取り、裏付けと異説の検証を担い、Pythonエンジンはデータ契約・チェックポイント・描画を担う。

## CLIサンプル

```bash
python -m truth_seeker init "囲碁"
python -m truth_seeker task-add runs/<run_id> "囲碁の起源"
python -m truth_seeker status runs/<run_id>
python -m truth_seeker validate <complete-staging-generation>
python -m truth_seeker merge runs/<run_id> <complete-staging-generation>
python -m truth_seeker render runs/<run_id>
python -m truth_seeker resume runs/<run_id>
```

`runs/<run_id>/render/` に `scene.json`, `map.svg`, `map.html`, PDFが有効なら `map.pdf` を生成する。PDF出力に失敗した場合はSVGを正式代替とする。強制失敗テストには `render --force-pdf-error` を使う。

品質判定は構造チェックだけでは完結しない。エージェントが意味と視認性を実際に検査した後にレビューJSON（`research_complete`, `semantic_pass`, `artifact_pass`, `independent_review`）を作り、`python -m truth_seeker qa ... --review ...` で判定する。独立レビュー不在で必須QAが成功した場合のみ `complete_limited_review`。

## 修復とGit

`/skill-repair` はローカルログから修正案を提示する。**明示承認前に変更しない**。承認後に作業ツリーを直接修正し、ファイル全体のバックアップを `repair_backups/` に置く。修正テスト失敗時の復元は本人単独利用向けで、修復途中の別編集まで完全に保護するものではない。修正の承認とは別に、commitとpushの一括承認を要求する。

`.gitignore` だけを過信せず `python -m truth_seeker git-check` で実ステージングを検査する。確認できた範囲でも、秘密検査には見逃しがあり得るため目視確認が必要。

## 現時点の限界

このv1.0は**実用コアの初期実装**。JSONL世代保存、固定のレーン分類と動的な間隔、単調な時間アンカー、ベジェ配線、SVG/HTMLとPDF生成、承認制修復のテストを含む。一方、歴史調査そのもの、意味レビュー、写真の権利確認、真の全史料網羅、巨大データでの実用性能の保証、ベジェ衝突の完全自動解決はプログラムだけでは行えない。特に重要な歴史的主張の独立検証はAIエージェント側の実運用が必要。

正式なAC-01〜AC-10の**利用ハーネスでの結合試験は別途必要**。テストが通ったからといって全ての受入条件を完了と表示しない。

## 検証結果

実装初版の検証済み部分と、実際のハーネスで未検証の条件は [RELEASE_STATUS.md](RELEASE_STATUS.md) を参照。正式受入が完了したと装わない。
