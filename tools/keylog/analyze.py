#!/usr/bin/env python3
"""roba-keylog のログを解析して mt_z_custom の推奨値を出す。

前提となるファーム側の細工:
  - position 22 = &mt_z_custom LEFT_SHIFT Z  → LSFT は「Z キー由来」だけ
  - position 34 = &kp RIGHT_SHIFT            → RSFT は「意図的な Shift」だけ
この分離により、LSFT イベントを誤爆候補として数えられる。

使い方: roba-log report [--days N] [--raw]
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

LOG_DIR = os.path.expanduser(os.environ.get("ROBA_KEYLOG_DIR", "~/.local/share/roba-keylog"))

# ラベル → 手（roBa 上の物理位置ベース）
LEFT = {"L", "z", "a", "e", "TAB"}
RIGHT = {"R", "i", "u", "o", "BS", "DEL", "ENT"}
# SPC / ESC / EISU / KANA は親指クラスタで左右判定できないので除外扱い

MODS = {"LSFT", "RSFT", "LCTL", "RCTL", "LALT", "RALT", "LCMD", "RCMD", "CAPS", "FN"}
OTHER_MODS = MODS - {"LSFT", "RSFT", "CAPS", "FN"}

# 訂正の検出窓。緩くすると無関係な z / BS を拾って誤爆を過大評価するので、
# 「解放直後の数打鍵」に限定する。
CORRECTION_WINDOW_MS = 1200.0
CORRECTION_LOOKAHEAD_KEYS = 2


def pct(values, p):
    """パーセンタイル（線形補間なしの単純版）"""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def fmt(v, unit="ms"):
    return "—" if v is None else f"{v:.0f}{unit}"


def load_sessions(days):
    """ログを読み、セッション（mach 時刻が連続する区間）ごとのイベント列にする。"""
    if not os.path.isdir(LOG_DIR):
        sys.exit(f"ログディレクトリがありません: {LOG_DIR}\n先に `roba-log start` を実行してください。")

    files = sorted(f for f in os.listdir(LOG_DIR) if f.endswith(".jsonl"))
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        files = [f for f in files if f[:10] >= cutoff]
    if not files:
        sys.exit("解析対象のログがありません。")

    sessions, cur, meta = [], [], {}
    for fn in files:
        day = fn[:10]
        with open(os.path.join(LOG_DIR, fn), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("hdr"):
                    if cur:
                        sessions.append((meta, cur))
                    cur, meta = [], {"day": day, **ev}
                    continue
                if not cur and not meta:
                    meta = {"day": day}
                ev["day"] = day
                # 時刻が巻き戻ったら別セッション扱い（再起動など）
                if cur and ev["t"] < cur[-1]["t"] - 1000:
                    sessions.append((meta, cur))
                    cur = []
                cur.append(ev)
    if cur:
        sessions.append((meta, cur))
    return [(m, e) for m, e in sessions if e], files


def analyze_shift_events(events, name):
    """指定した修飾キーの押下ごとに、持続時間・直前間隔・同時押しキーを抽出する。"""
    out = []
    held_mods = set()
    for i, ev in enumerate(events):
        k, e = ev.get("k"), ev.get("e")
        if e == "md":
            held_mods.add(k)
        elif e == "mu":
            held_mods.discard(k)
        if not (e == "md" and k == name):
            continue

        # 直前の打鍵（keyDown / modDown）からの間隔 = require-prior-idle-ms の判定材料
        prior_gap = None
        for j in range(i - 1, -1, -1):
            if events[j].get("e") in ("d", "md"):
                prior_gap = ev["t"] - events[j]["t"]
                break

        # 対応する mu を探しつつ、その間のイベントを集める
        during_down, during_up, mods_at_press = [], [], held_mods - {name}
        release_t = None
        for j in range(i + 1, len(events)):
            e2, k2 = events[j].get("e"), events[j].get("k")
            if e2 == "mu" and k2 == name:
                release_t = events[j]["t"]
                break
            if e2 == "d":
                during_down.append(k2)
            elif e2 == "u":
                during_up.append(k2)
            elif e2 == "md":
                mods_at_press.add(k2)
        if release_t is None:
            continue  # ログの切れ目

        # 訂正シグナル: 解放直後の数打鍵に BS / z の打ち直しがあるか。
        # 窓を広げると通常の z 打鍵を拾ってしまうため、直後 N 打鍵だけを見る。
        corrected, retyped_z, seen = False, False, 0
        for j in range(i + 1, len(events)):
            if events[j]["t"] - release_t > CORRECTION_WINDOW_MS:
                break
            if events[j].get("e") != "d":
                continue
            seen += 1
            if seen > CORRECTION_LOOKAHEAD_KEYS:
                break
            if events[j].get("k") in ("BS", "DEL"):
                corrected = True
            elif events[j].get("k") == "z":
                retyped_z = True

        out.append({
            "t": ev["t"],
            "day": ev.get("day"),
            "dur": release_t - ev["t"],
            "prior_gap": prior_gap,
            "during": during_down,
            # balanced フレーバーは「他キーが押されて離された」時点で hold 確定する
            "interrupted_by_release": bool(set(during_down) & set(during_up)),
            "other_mods": mods_at_press & OTHER_MODS,
            "corrected": corrected,
            "retyped_z": retyped_z,
            "ims": ev.get("ims", "?"),
            "app": ev.get("app", "?"),
        })
    return out


def classify(lshift_events):
    """LSFT イベントを誤爆/正当に分類する。"""
    misfires, deliberate, combos = [], [], []
    for s in lshift_events:
        if s["other_mods"]:
            combos.append(s)          # キーマップ側のコンボ/マクロ（LS(LG(..)) 等）
        elif not s["during"]:
            s["kind"] = "lone"        # Shift だけ出て何も入力されなかった = 確定誤爆
            misfires.append(s)
        elif s["corrected"] or s["retyped_z"]:
            s["kind"] = "corrected"   # 大文字が出て打ち直した = 確定誤爆
            misfires.append(s)
        else:
            s["kind"] = "shifted"     # 大文字入力。意図的かもしれないので別枠
            deliberate.append(s)
    return misfires, deliberate, combos


def hand_of(keys):
    for k in keys:
        if k in LEFT:
            return "same"   # 左手 = Z と同じ手
        if k in RIGHT:
            return "cross"  # 右手
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None, help="直近 N 日だけ解析")
    ap.add_argument("--raw", action="store_true", help="誤爆イベントを1件ずつ出す")
    a = ap.parse_args()

    sessions, files = load_sessions(a.days)
    all_events = [e for _, evs in sessions for e in evs]
    if not all_events:
        sys.exit("イベントがありません。")

    total_down = sum(1 for e in all_events if e.get("e") == "d")
    z_count = sum(1 for e in all_events if e.get("e") == "d" and e.get("k") == "z")
    days = sorted({e.get("day") for e in all_events})

    lshift, rshift = [], []
    for _, evs in sessions:
        lshift += analyze_shift_events(evs, "LSFT")
        rshift += analyze_shift_events(evs, "RSFT")

    misfires, deliberate, combos = classify(lshift)

    # position 34 を RIGHT_SHIFT にする細工が効いているか。
    # 効いていなくても「単独Shift」「大文字＋打ち直し」は誤爆と断定できるので解析は成立する。
    has_split = len(rshift) > 0

    print("=" * 62)
    print(" roBa mt_z_custom チューニング レポート")
    print("=" * 62)
    print(f"期間          : {days[0]} 〜 {days[-1]}  ({len(days)}日 / {len(sessions)}セッション)")
    print(f"総打鍵        : {total_down:,} 回")
    print(f"z キー確定打鍵: {z_count:,} 回   （tap 判定されて z が出た分）")
    print(f"左右Shift分離 : {'有効' if has_split else '無効'}"
          f"{'' if has_split else '   ← position 34 が LEFT_SHIFT のまま'}")
    if not has_split:
        print()
        print("  RSFT が1件も観測されていません。誤爆の判定は以下だけで行います:")
        print("    ・単独Shift（他キーを伴わない）      → 誤爆と断定できる")
        print("    ・大文字が出た直後に BS/z で打ち直し → 誤爆と断定できる")
        print("  『大文字入力』は position 34 の意図的な Shift と区別できないため判定保留になります。")
        print("  分離を有効にすると保留分も判定できます（tools/keylog/STUDIO-SETUP.md）。")
    print()

    # ---- 誤爆の集計 ----
    kinds = Counter(m["kind"] for m in misfires)
    z_attempts = z_count + len(misfires)
    rate = (len(misfires) / z_attempts * 100) if z_attempts else 0
    print("── 誤爆（Z キーが Shift になった） " + "─" * 26)
    print(f"確定誤爆      : {len(misfires)} 回")
    print(f"  内訳        : 単独Shift {kinds['lone']} / 大文字＋打ち直し {kinds['corrected']}")
    print(f"誤爆率        : {rate:.1f}%   （Zを打とうとした {z_attempts} 回中）")
    if total_down:
        print(f"              : 1,000打鍵あたり {len(misfires) / total_down * 1000:.1f} 回")
    print(f"判定保留      : 大文字入力 {len(deliberate)} 回（意図的な Shift 運用か誤爆か不明）")
    print(f"除外          : コンボ/マクロ由来 {len(combos)} 回")
    print()

    if not misfires:
        print("誤爆が検出されませんでした。計測期間を延ばすか、`roba-log status` で稼働を確認してください。")
        return

    # ---- 押下時間 ----
    mis_dur = [m["dur"] for m in misfires]
    # 意図的な Shift の基準値。分離が有効なら RSFT、無効なら「大文字入力」で代用する
    # （代用時は Z キー由来の誤爆が混ざるので下振れする点に注意）。
    del_label = "意図的Shift" if has_split else "大文字入力"
    del_dur = [s["dur"] for s in (rshift if has_split else deliberate)]
    print("── 押下時間の分布 " + "─" * 43)
    print(f"{'':14}{'p50':>8}{'p75':>8}{'p90':>8}{'p95':>8}{'p99':>8}{'max':>8}")
    for lbl, vals in (("誤爆時のZ", mis_dur), (del_label, del_dur)):
        cells = "".join(f"{fmt(pct(vals, p)):>8}" for p in (50, 75, 90, 95, 99, 100))
        print(f"{lbl:14}{cells}   n={len(vals)}")
    if not has_split:
        print("  ※ 『大文字入力』には Z キー由来の誤爆が混ざるため、実際の意図的 Shift より短めに出ます")
    print()

    # ---- 直前キーからの間隔 ----
    gaps = [m["prior_gap"] for m in misfires if m["prior_gap"] is not None]
    print("── 誤爆時の「直前キーからの間隔」 " + "─" * 27)
    cells = "".join(f"{fmt(pct(gaps, p)):>8}" for p in (50, 75, 90, 95, 99, 100))
    print(f"{'':14}{'p50':>8}{'p75':>8}{'p90':>8}{'p95':>8}{'p99':>8}{'max':>8}")
    print(f"{'間隔':14}{cells}   n={len(gaps)}")
    print()

    # ---- 誤爆時に巻き込まれたキーの手 ----
    hands = Counter(hand_of(m["during"]) for m in misfires if m["during"])
    print("── 誤爆に巻き込まれたキー " + "─" * 35)
    print(f"同じ手（左）  : {hands['same']} 回  ← hold-trigger-key-positions のクロスハンド化で消える")
    print(f"反対の手（右）: {hands['cross']} 回  ← クロスハンド化では消えない")
    print(f"判定不能      : {hands['other']} 回")
    print()

    # ---- 対策のシミュレーション ----
    RPI_LADDER = (100, 120, 150, 180, 200, 250)
    TT_LADDER = (150, 180, 200, 250, 300)
    n_mis = len(misfires)

    def kills_rpi(ms):
        """直前の打鍵から ms 以内なら強制的に tap になる"""
        return {id(m) for m in misfires if m["prior_gap"] is not None and m["prior_gap"] < ms}

    def kills_tt(ms):
        """balanced では『他キーを押して離した』時点で hold が確定するので、
        そのケースは tapping-term をいくら伸ばしても救えない"""
        return {id(m) for m in misfires if m["dur"] < ms and not m["interrupted_by_release"]}

    cross_kills = {id(m) for m in misfires if hand_of(m["during"]) == "same"}

    def row(label, killed):
        print(f"{label:34}{len(killed):>6} 回{n_mis - len(killed):>6} 回{len(killed) / n_mis * 100:>7.0f}%")

    print("── 対策シミュレーション（この実測データに当てはめた場合） " + "─" * 4)
    print(f"{'設定':34}{'消える':>8}{'残る':>8}{'除去率':>8}")
    for v in RPI_LADDER:
        row(f"require-prior-idle-ms = {v}", kills_rpi(v))
    for v in TT_LADDER:
        row(f"tapping-term-ms = {v}", kills_tt(v))
    row("クロスハンド化（右手のみ）", cross_kills)

    def knee(ladder, fn):
        """効きが頭打ちになる手前の値を選ぶ（無駄に大きい値を勧めない）"""
        best = len(fn(ladder[-1]))
        if best == 0:
            return None
        for v in ladder:
            if len(fn(v)) >= best * 0.9:
                return v
        return ladder[-1]

    rec_rpi, rec_tt = knee(RPI_LADDER, kills_rpi), knee(TT_LADDER, kills_tt)
    combined = set()
    if rec_rpi:
        combined |= kills_rpi(rec_rpi)
    if rec_tt:
        combined |= kills_tt(rec_tt)
    combined |= cross_kills
    parts = [p for p in (f"rpi={rec_rpi}" if rec_rpi else None,
                         f"tt={rec_tt}" if rec_tt else None, "cross") if p]
    row("併用 (" + ", ".join(parts) + ")", combined)
    print()

    # ---- 推奨値 ----
    print("── 推奨値 " + "─" * 51)
    if rec_rpi:
        print(f"require-prior-idle-ms = <{rec_rpi}>   誤爆の {len(kills_rpi(rec_rpi))}/{n_mis} を吸収")
    else:
        print("require-prior-idle-ms : 効果なし")
        print("  誤爆はどれも直前の打鍵から十分間隔が空いており、"
              "『流れの中で巻き込まれる』型ではありません。")
    if rec_tt:
        print(f"tapping-term-ms       = <{rec_tt}>   誤爆の {len(kills_tt(rec_tt))}/{n_mis} を吸収")
        del_p10 = pct(del_dur, 10)
        if has_split and del_p10 is not None and rec_tt >= del_p10:
            print(f"  ⚠ 意図的Shiftの押下 p10 = {fmt(del_p10)} と重なります。ここまで上げると")
            print("    意図的な Shift が出しにくくなるので、他の手段で吸収するほうが安全です。")
    else:
        print("tapping-term-ms       : 効果なし（誤爆は全て他キーとの重なりで確定している）")
    if not cross_kills:
        print("クロスハンド化        : 効果なし（誤爆を起こしているのは全て右手キー）")
    print(f"想定除去率            = {len(combined)}/{n_mis} ({len(combined) / n_mis * 100:.0f}%)")
    if not has_split and deliberate:
        print(f"  ※ 判定保留の {len(deliberate)} 件は誤爆かもしれません。左右Shift分離を有効にすると")
        print("    そこまで判定できます（推奨値自体は確定した誤爆だけで算出しているので有効です）。")
    if len(combined) < n_mis * 0.8:
        print("  ※ 8割を切っています。flavor を tap-preferred にする、"
              "hold-while-undecided を外す等の")
        print("    構造的な変更も検討対象です（--raw で個別イベントを確認してください）。")
    print()

    # ---- 文脈 ----
    print("── 誤爆が起きている文脈 " + "─" * 37)
    for title, key in (("アプリ", "app"), ("入力ソース", "ims")):
        c = Counter(m[key] for m in misfires).most_common(6)
        print(f"{title}:")
        for name, n in c:
            print(f"    {n:>4} 回  {name}")
    by_day = Counter(m["day"] for m in misfires)
    print("日別:")
    for d in sorted(by_day):
        print(f"    {by_day[d]:>4} 回  {d}")
    print()

    if a.raw:
        print("── 誤爆イベント一覧 " + "─" * 41)
        for m in misfires:
            print(f"  {m['day']} kind={m['kind']:9} dur={m['dur']:6.0f}ms "
                  f"gap={fmt(m['prior_gap']):>7} during={m['during']} app={m['app']}")


if __name__ == "__main__":
    main()
