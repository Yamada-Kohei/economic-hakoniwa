# 案内係の辞書と相談メモ（生成物）

このフォルダの中身は**生成物**です。手で直さないでください。
直すときは定義（packs/）・注記（tools/guide_notes.yaml）・メモの原稿
（tools/guide_memos.yaml）を直してから、次のコマンドで作り直します。

    python3 tools/build_guide_dict.py

- dictionary.md / dictionary.json: 仕様の辞書（財の全部と、よく聞く施設）。
- memos.md: 相談メモ（1枚ごとに質問・答え・根拠）。
- 注記は財・施設の id をキーにし、未知 id・重複 id があると生成は失敗します。
- 施設の範囲は最初の枝では「よく聞く施設」の id の一覧で固定しています
  （tools/guide_notes.yaml の facility_scope）。施設の全部は次の枝で広げます。
- 数値は定義データから引いた「定義上の値」です。根拠がコード側にあるものは、
  項目の中に出典（ファイルと関数名）を書きます。
