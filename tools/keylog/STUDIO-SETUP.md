# ZMK Studio で計測用の設定を入れる手順

## なぜ Studio で操作するのか

roBa_R は `build.yaml` で `studio-rpc-usb-uart` 付きでビルドされている。
**ZMK Studio で一度でもキーマップを編集すると、その内容が本体のフラッシュ設定領域に
保存され、以後 uf2 を焼き直しても Studio 側のキーマップが優先される。**

2026-08-05 に実際これが起きた。`&kp RIGHT_SHIFT` を含む正しい uf2 を右手側に
書き込んだのに、計測ログに RSFT が1件も現れなかった（LSFT のみ 62件）。
uf2 の中身とビルド元コミットは検証済みだったので、原因は本体側の上書きと確定した。

## 計測フェーズ: position 34 を Right Shift にする

### 対象キー

**左手側の最下段・一番左端**（`x=0, y=3.624`）。Z キー（`x=0, y=2.621`）の真下にある、
左手親指列の左端のキー。現在 Left Shift が割り当たっている。

```
    ┌───┐
    │ Z │ ← position 22（Z / LeftShift の mod-tap。ここは触らない）
    ├───┤
    │Sft│ ← position 34（これを Right Shift に変える）
    └───┘
```

### 手順

1. roBa の**右手側**を USB でマシンに接続する（Studio は central にしか繋がらない）
2. https://zmk.studio/ を Chrome か Edge で開く（WebSerial が要るので Safari 不可）
3. 「Connect」→ USB のシリアルデバイスを選ぶ
4. Default レイヤーの上記キーをクリック
5. キーコードを `Left Shift` → **`Right Shift`** に変更
6. 保存する（Studio 上部の Save / Commit）

### 確認

変更後、そのキーを数回押してから:

```bash
grep -c '"k":"RSFT"' ~/.local/share/roba-keylog/$(date +%F).jsonl
```

1以上になれば反映済み。0のままなら保存されていない。

## チューニングフェーズ: Studio の設定を捨てて uf2 に戻す

**ZMK Studio では behavior のパラメータ（`tapping-term-ms`、`require-prior-idle-ms`、
`hold-trigger-key-positions` など）を編集できない。** 編集できるのはキーの割り当てだけ。

したがって計測結果を反映する段階では、本体の保存設定を消して uf2 を正にする必要がある。

1. `config/roBa.keymap` の `mt_z_custom` に推奨値を反映してコミット・push
2. GitHub Actions のビルド完了を待ち、uf2 をダウンロード
   ```bash
   gh run list -R Hiromu-Private/zmk-config-roBa --limit 1
   ```
   ※ フォークなので `-R` の明示が必須（既定だとフォーク元 kumamuk-git を見る）
3. **右手側**をリセット2回でブートローダーに入れ、`settings_reset-seeeduino_xiao_ble-zmk.uf2`
   を書き込む（本体の保存設定と BT ペアリングが消える）
4. 続けて `roBa_R-seeeduino_xiao_ble-zmk.uf2` を書き込む
5. Bluetooth を再ペアリングする

以後 Studio でキーマップを触ると再び同じ状態になるので、**数値のチューニングが
終わるまでは Studio でキーを編集しない**こと。
