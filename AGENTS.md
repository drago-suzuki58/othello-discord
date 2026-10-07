# 開発メモ（エージェント向け）

## 構成と依存の向き

- `engine.py` 盤面ロジック（ビットボード）。ほかのモジュールに依存しない。外部とはマス番号（`row * 8 + col`、a1=0）と座標表記でやり取りする。
- `ai.py` CPU。`engine` だけに依存する。`choose_move` は同期関数で、`controller` から `asyncio.to_thread` で呼ぶ。
- `game.py` 対局の状態と `Registry`。Discord に依存させない。操作エラーは利用者向けの文言を持つ `GameError` で表す。
- `render.py` 盤面 PNG・リプレイ GIF・文字モード用タイル画像。`text_board.py` タイル（アプリケーション絵文字）の同期と文字盤面。
- `views.py` 状態から Components V2 のレイアウトを作る純粋な処理と `GameButton`。`controller.py` 操作を状態に反映してメッセージを更新する。`bot.py` クライアントとスラッシュコマンド。

## 守ること

- 1 局 1 メッセージ。対局中に新しいメッセージを投稿しない。利用者への個別の案内はエフェメラルで返す。
- 1 人が同じチャンネルで同時に参加できる対局は 1 つ（申し込み中・募集中を含む）。`/othello place` はこの制約を前提に対局を特定している。
- 対局はメモリ上だけで管理する。永続化はしない。
- ボタンはすべて `GameButton`（DynamicItem、custom_id は `oth:<action>:<arg>`）で作り、メッセージごとのビューを保持しない。対局は `interaction.message.id` で引く。エフェメラルからの操作は対局メッセージの ID を `arg` に入れる。
- 状態の変更は同期的に行い、メッセージの描画と送信は `game.lock` の中で行う。
- Components V2 の上限（1 メッセージ 40 コンポーネント、テキスト合計 4000 文字、ActionRow 1 行にボタン 5 個）に収める。文字モードの盤面だけで約 2,800 文字を使う。
- タイルの見た目（`render_tile`）や種類（`TILE_KINDS`）を変えたら `text_board.TILE_VERSION` を上げる。起動時に新しい版が登録され、古い版は削除される。
- 新しい依存は `uv add` で追加する。

## 確認

- `uv run pytest`、`uv run ruff check .`、`uv run ruff format --check .`
- Discord 上の挙動は実際のサーバーで確認する。
