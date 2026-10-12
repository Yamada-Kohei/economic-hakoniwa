# 追加パックの作り方

追加パックは `packs/` の下に置くフォルダです。本体パック `packs/base/` と同じ形で、
財・地形・施設などを足したり、本体の数値を差し替えたりできます。

## 最小の例: 砂糖を足す

```
packs/sugar/
  pack.yaml
  data/goods.yaml
  locale/ja/goods.yaml
```

```yaml
# pack.yaml
name: sugar            # フォルダ名と同じにする
version: 0.1.0
description: 砂糖とその工場を足す
requires: []           # 先に読み込む必要がある他のパック（本体パックは書かなくてよい）
```

```yaml
# data/goods.yaml
sugar: {tier: 2}
```

```yaml
# locale/ja/goods.yaml
sugar: 砂糖
```

使うには `game.yaml` の `packs:` に名前を足します。

```yaml
packs: [base, sugar]
```

## 読み込む順

本体パック → 必要とされたパック（`requires`）→ 名前順、です。

## 名前の衝突と上書き

ほかのパックと同じ名前の定義を書くとエラーになります。本体の数値を変えたいときは
`override: true` を付け、変えたい数値だけを書きます（数値以外は変えられません）。

```yaml
# data/facilities.yaml
steel_mill:
  override: true
  workers: 250
  recipes: {default: {outputs: {steel: 6}}}
```

表示名は後から読んだパックのもので上書きされます（言い回しを変えたいときに使えます）。

## モジュール

計算の仕組みも足したいときは、パックの中に `modules/<名前>/__init__.py` を置きます。
書き方は [modules.md](modules.md) を見てください。

## 版と移し替え

世界には読み込んだパックとその版を保存しています。定義を消したり名前を変えたりして、
保存済みの世界に残っている名前（マスの地形、待っている命令など）が定義から消えると、
ターン更新は止まります。移し替えの手順をパックに同梱する仕組みは、必要になった時点で足します。
