# roba-keylog — Z/Shift 誤爆の実測ツール

`mt_z_custom`（position 22 = Z / LeftShift の mod-tap）の `tapping-term-ms` と
`require-prior-idle-ms` を、勘ではなく実測で決めるための計測ツール。

## なぜ必要か

mod-tap の tap/hold 判定はファームウェア側で完結する。macOS が受け取るのは
「z」か「Shift」かの**結果だけ**なので、ホスト側からは z の実押下時間は測れない。

ただし **hold と判定された時の押下時間 = Shift の押下時間** はそのまま観測できる。
そこで次の細工をして誤爆だけを取り出す。

| キー | バインド | 意味 |
|---|---|---|
| position 22 | `&mt_z_custom LEFT_SHIFT Z` | **LSFT** が出たら Z キー由来 = 誤爆候補 |
| position 34 | `&kp RIGHT_SHIFT` | **RSFT** が出たら意図的な Shift |

macOS 上では左右どちらの Shift も同じ働きをするので、使用感は変わらない。

## 記録される内容（プライバシー）

既定（anonymous モード）で実名記録するのは以下だけ:

- `z` — 対象キー
- 母音 `a i u e o` — ローマ字入力の解析に必要
- 修飾キー `LSFT / RSFT / LCTL / ...`
- `BS DEL ENT SPC TAB ESC EISU KANA` — 訂正と文構造の検出に必要

**それ以外のキーはすべて `L`（左手）/ `R`（右手）に潰される。**
子音が落ちるのでログから文章もパスワードも復元できない。

加えて Shift 押下の瞬間だけ、前面アプリの Bundle ID と入力ソース（ことえり/ABC）を
記録する（誤爆がどの文脈で起きるかを見るため）。

`--full` を付けると全キーを実名記録できるが、実質キーロガーになるので通常は使わない。

保存先は `~/.local/share/roba-keylog/YYYY-MM-DD.jsonl`（パーミッション 600）。
リポジトリの外なので git には入らない。

## 使い方

```bash
./roba-log build     # ビルドして ~/.local/bin/roba-keylog へインストール
./roba-log start     # 計測開始（LaunchAgent 登録・再ログイン後も継続）
./roba-log permit    # 「入力監視」の許可設定を開く
./roba-log status    # 稼働状況とログ量
./roba-log report    # 解析レポート
./roba-log stop      # 計測停止
./roba-log purge     # ログ全削除
```

### 「入力監視」の許可が必須

**`CGEvent.tapCreate` は権限が無くても成功し、イベントが1件も来ないという壊れ方をする。**
そのため起動時に `CGPreflightListenEventAccess()` で明示的に確認し、結果を stderr に残している。
`./roba-log status` の末尾で必ず確認すること。

```
[roba-keylog] 入力監視: 許可済み   ← これが出ていないと何も記録されない
```

未許可なら `./roba-log permit` で設定画面を開き、`RobaKeylog` を ON にする。
権限を変えると macOS がプロセスを落とすが、`KeepAlive` で自動的に復帰する。

### なぜ .app バンドルなのか

最初は `~/.local/bin/roba-keylog` に素の実行ファイルとして置いたが、**入力監視の許可が
まったく効かなかった**。原因は署名で、`swiftc` が吐く実行ファイルは linker-signed の
ad-hoc 署名になり `Internal requirements=none`（designated requirement 無し）だった。
TCC はこれを同一のプログラムとして扱えない。

そこで `~/Applications/RobaKeylog.app` として Info.plist 付きのバンドルにまとめ、
`codesign --force --sign - --identifier com.waggy.roba-keylog` で正式に署名している。
`LSUIElement` を立てているので Dock にもメニューバーにも出ない。

**注意: `roba-log build` で作り直すと cdhash が変わり、入力監視の許可がリセットされる。**
再ビルドしたら `./roba-log permit` でもう一度 ON にすること。

### ファームウェアの書き込み

roBa は **右半分 (roBa_R) が central** で、キーマップは central 側だけが持つ
（`boards/shields/roBa/Kconfig.defconfig` の `SHIELD_ROBA_R` に `ZMK_SPLIT_ROLE_CENTRAL=y`）。
キーマップだけを変えた場合、書き込むのは **`roBa_R-seeeduino_xiao_ble-zmk.uf2` だけでよい**。

## レポートの読み方

```
── 対策シミュレーション ──
require-prior-idle-ms = 150       17 回     3 回     85%
tapping-term-ms = 180             12 回     8 回     60%
クロスハンド化（右手のみ）           0 回    20 回      0%
```

実測した誤爆1件ずつに各対策を当てはめ、**何件消えるか**を出している。
ZMK の `balanced` フレーバーは「他キーを押して離した」時点で hold が確定するため、
そのケースは `tapping-term-ms` をいくら伸ばしても救えない。
シミュレーションはその条件を織り込んで計算している。

## 解析ロジックの検証

`analyze.py` は、正解が分かっている合成ログに対して期待値どおりの分類を返すことを
確認済み（2026-08-05: 誤爆20件 = 単独Shift 12 + 訂正付き 8、判定保留5、除外4）。

環境変数 `ROBA_KEYLOG_DIR` で読み込み先を差し替えられるので、再検証する場合は
合成ログを別ディレクトリに置いて `ROBA_KEYLOG_DIR=... python3 analyze.py` で回す。

## 計測が終わったら

1. `./roba-log report` の推奨値を `config/roBa.keymap` の `mt_z_custom` に反映
2. position 34 を `RIGHT_SHIFT` → `LEFT_SHIFT` に戻す（戻さなくても実害はない）
3. `./roba-log stop` で計測を止め、必要なら `./roba-log purge`
