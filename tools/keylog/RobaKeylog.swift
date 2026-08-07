// RobaKeylog — roBa の mod-tap チューニング用 打鍵タイミング計測ツール
//
// 目的:
//   Z/Shift の mod-tap (mt_z_custom) の tapping-term-ms / require-prior-idle-ms を
//   勘ではなく実測で決めるために、打鍵の「タイミングだけ」を記録する。
//
// プライバシー設計（既定 = anonymous モード）:
//   実名で記録するのは以下だけ。
//     - z（対象キー）
//     - 母音 a i u e o（ローマ字入力の解析に必要）
//     - 修飾キー（LSFT/RSFT/LCTL/... ）
//     - BS / ENT / SPC / TAB / ESC / EISU / KANA（構造と訂正の検出に必要）
//     - 記号（記号レイヤーの配列検討に必要。2026-08-07〜。--no-symbols で無効化）
//   それ以外のキーはすべて "L"（左手）/ "R"（右手）に匿名化する。
//   子音と数字が落ちるため、ログから文章もパスワードも復元できない。
//   --full を付けると全キーを実名記録する（解析精度は上がるが実質キーロガー）。
//
// 権限: 「入力監視 (Input Monitoring)」が必要。

import Foundation
import CoreGraphics
import Carbon
import AppKit

// MARK: - 設定

let args = CommandLine.arguments
let fullMode = args.contains("--full")
let stdoutMode = args.contains("--stdout")
// 記号レイヤーの配列検討用。既定で有効（2026-08-07〜）
let symbolMode = !args.contains("--no-symbols")

let logDir = ("~/.local/share/roba-keylog" as NSString).expandingTildeInPath

// MARK: - キーコード → ラベル

// 解析に必要なので実名で残すキー
let namedKeys: [Int64: String] = [
    6: "z",
    0: "a", 34: "i", 32: "u", 14: "e", 31: "o",
    51: "BS", 117: "DEL",
    36: "ENT", 49: "SPC", 48: "TAB", 53: "ESC",
    102: "EISU", 104: "KANA",
]

// 記号レイヤーの配列検討用に、記号だけは実名で残す（--no-symbols で無効化）。
//
// キーコードではなく「実際に入力された文字」で判定する。roBa は JP_* の define で
// JIS 配列として記号を送るため、キーコードと記号の対応が ANSI とずれるため。
// 英字・数字はこの集合に入らないので従来どおり L/R に潰れる＝文章もパスワードも
// 復元できない性質は変わらない（数字を残すと PIN やカード番号が読めてしまうため
// 意図的に除外している）。
let symbolChars: Set<Character> = [
    "!", "\"", "#", "$", "%", "&", "'", "(", ")", "*", "+", ",", "-", ".", "/",
    ":", ";", "<", "=", ">", "?", "@", "[", "\\", "]", "^", "_", "`",
    "{", "|", "}", "~", "¥",
]

// QWERTY 左手側のキーコード（roBa も左半分は QWERTY 配置）
let leftHandKeys: Set<Int64> = [
    0, 1, 2, 3, 5,          // a s d f g
    6, 7, 8, 9, 11,         // z x c v b
    12, 13, 14, 15, 17,     // q w e r t
    18, 19, 20, 21, 23,     // 1 2 3 4 5
    50, 48, 57,             // ` tab caps
]

let modifierKeys: [Int64: String] = [
    56: "LSFT", 60: "RSFT",
    59: "LCTL", 62: "RCTL",
    58: "LALT", 61: "RALT",
    55: "LCMD", 54: "RCMD",
    57: "CAPS", 63: "FN",
]

// flagsChanged の押下判定に使う device-dependent マスク
let modifierMasks: [Int64: UInt64] = [
    56: 0x00000002, 60: 0x00000004,   // L/R Shift
    59: 0x00000001, 62: 0x00002000,   // L/R Control
    58: 0x00000020, 61: 0x00000040,   // L/R Option
    55: 0x00000008, 54: 0x00000010,   // L/R Command
    57: 0x00010000,                   // CapsLock (maskAlphaShift)
    63: 0x00800000,                   // Fn (maskSecondaryFn)
]

// 入力された文字が記号なら、その文字を返す。
func symbolLabel(of event: CGEvent) -> String? {
    guard symbolMode else { return nil }
    var length = 0
    var chars = [UniChar](repeating: 0, count: 4)
    event.keyboardGetUnicodeString(maxStringLength: 4, actualStringLength: &length,
                                   unicodeString: &chars)
    guard length == 1, let scalar = Unicode.Scalar(chars[0]) else { return nil }
    let c = Character(scalar)
    return symbolChars.contains(c) ? String(c) : nil
}

// keyDown 時に確定したラベルを keyUp でも使う。
// Shift を先に離すと keyUp 側の unicodeString が別の文字になり、down/up の対応が
// 壊れる（"(" で押して "8" で離す等）ため、押した時のラベルを持ち回る。
var labelAtDown: [Int64: String] = [:]

func label(for keyCode: Int64, event: CGEvent, isDown: Bool) -> String {
    if !isDown, let cached = labelAtDown.removeValue(forKey: keyCode) { return cached }
    let resolved: String
    if let n = namedKeys[keyCode] {
        resolved = n
    } else if let s = symbolLabel(of: event) {
        resolved = s
    } else if fullMode {
        resolved = "k\(keyCode)"
    } else {
        resolved = leftHandKeys.contains(keyCode) ? "L" : "R"
    }
    if isDown { labelAtDown[keyCode] = resolved }
    return resolved
}

// 記号には " と \ が含まれるので JSON 文字列として出す前に潰す
func jsonEscape(_ s: String) -> String {
    s.replacingOccurrences(of: "\\", with: "\\\\")
     .replacingOccurrences(of: "\"", with: "\\\"")
}

// MARK: - 時刻

var timebase = mach_timebase_info_data_t()
mach_timebase_info(&timebase)

// 単位に注意。この2つは別物で、取り違えると timebase 倍（Apple Silicon で 41.67 倍）ずれる。
//   mach_absolute_time()  … tick。ns にするには numer/denom を掛ける
//   CGEvent.timestamp     … 既に ns。換算してはいけない

/// mach_absolute_time() の tick をミリ秒に変換
func machToMs(_ ticks: UInt64) -> Double {
    Double(ticks) * Double(timebase.numer) / Double(timebase.denom) / 1_000_000.0
}

/// CGEvent のハードウェアタイムスタンプ（ns）をミリ秒に変換
func eventToMs(_ ns: UInt64) -> Double {
    Double(ns) / 1_000_000.0
}

/// 起動時に「mach 時刻 → 壁時計」の対応を1回だけ取る（後からログを日時に戻せるように）
let bootMachMs = machToMs(mach_absolute_time())
let bootWallMs = Date().timeIntervalSince1970 * 1000.0

/// 最初のイベントで単位の取り違えを自己診断する（黙って壊れるのを防ぐ）
var unitChecked = false
func checkUnits(_ eventNs: UInt64) {
    guard !unitChecked else { return }
    unitChecked = true
    let nowMs = machToMs(mach_absolute_time())
    let evMs = eventToMs(eventNs)
    let ratio = evMs / nowMs
    let msg: String
    if ratio < 0.5 || ratio > 2.0 {
        msg = "[roba-keylog] ⚠ タイムスタンプの単位が想定と違います "
            + "(event=\(Int(evMs))ms vs mach=\(Int(nowMs))ms, 比=\(String(format: "%.2f", ratio)))\n"
            + "[roba-keylog]   このままだと計測値が全てずれます。eventToMs を見直してください。\n"
    } else {
        msg = "[roba-keylog] 時刻の単位チェック OK (比=\(String(format: "%.3f", ratio)))\n"
    }
    FileHandle.standardError.write(msg.data(using: .utf8)!)
}

// MARK: - コンテキスト取得（Shift 押下時のみサンプリング）

func currentInputSourceID() -> String {
    guard let src = TISCopyCurrentKeyboardInputSource()?.takeRetainedValue() else { return "?" }
    guard let ptr = TISGetInputSourceProperty(src, kTISPropertyInputSourceID) else { return "?" }
    let id = Unmanaged<CFString>.fromOpaque(ptr).takeUnretainedValue() as String
    // 長いので末尾だけ
    return id.replacingOccurrences(of: "com.apple.inputmethod.", with: "")
             .replacingOccurrences(of: "com.apple.keylayout.", with: "")
}

func frontmostApp() -> String {
    NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "?"
}

// MARK: - 書き出し

final class LogWriter {
    private var buffer = ""
    private var handle: FileHandle?
    private var currentDay = ""
    private let fmt: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone.current
        return f
    }()

    init() {
        try? FileManager.default.createDirectory(
            atPath: logDir, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
    }

    private func ensureHandle() {
        let day = fmt.string(from: Date())
        if day == currentDay, handle != nil { return }
        handle?.closeFile()
        currentDay = day
        let path = "\(logDir)/\(day).jsonl"
        if !FileManager.default.fileExists(atPath: path) {
            FileManager.default.createFile(atPath: path, contents: nil,
                                           attributes: [.posixPermissions: 0o600])
        }
        handle = FileHandle(forWritingAtPath: path)
        handle?.seekToEndOfFile()
        // セッションヘッダ（mach 時刻と壁時計の対応・モード）
        let header = "{\"hdr\":1,\"boot_mach_ms\":\(bootMachMs),\"boot_wall_ms\":\(bootWallMs)," +
                     "\"mode\":\"\(fullMode ? "full" : (symbolMode ? "anon+sym" : "anon"))\",\"pid\":\(getpid())}\n"
        handle?.write(header.data(using: .utf8)!)
    }

    func append(_ line: String) {
        if stdoutMode { print(line); return }
        buffer += line + "\n"
        if buffer.utf8.count > 8192 { flush() }
    }

    func flush() {
        guard !buffer.isEmpty else { return }
        ensureHandle()
        if let d = buffer.data(using: .utf8) { handle?.write(d) }
        buffer = ""
    }
}

let writer = LogWriter()

// MARK: - イベントタップ

/// 内蔵キーボードか外部（roBa）かを見分ける。
///
/// CGEvent には HID の vendor/product が乗らないので、キーボード種別 (keyboardType) で
/// 判別する。Apple の内蔵キーボードは固有の値を返し、BLE の roBa とは異なる。
/// 起動後に最初に観測した値を内蔵とみなすのではなく、実測値をそのまま記録して
/// 解析側で分離できるようにする（解析時に roBa 側の値を選べばよい）。
func keyboardKind(_ event: CGEvent) -> Int64 {
    event.getIntegerValueField(.keyboardEventKeyboardType)
}

func handle(event: CGEvent, type: CGEventType) {
    checkUnits(event.timestamp)
    let t = eventToMs(event.timestamp)
    let code = event.getIntegerValueField(.keyboardEventKeycode)
    let kb = keyboardKind(event)

    switch type {
    case .keyDown:
        // オートリピートはノイズになるので捨てる
        if event.getIntegerValueField(.keyboardEventAutorepeat) != 0 { return }
        let dk = jsonEscape(label(for: code, event: event, isDown: true))
        writer.append("{\"t\":\(String(format: "%.1f", t)),\"e\":\"d\",\"k\":\"\(dk)\",\"kb\":\(kb)}")

    case .keyUp:
        let uk = jsonEscape(label(for: code, event: event, isDown: false))
        writer.append("{\"t\":\(String(format: "%.1f", t)),\"e\":\"u\",\"k\":\"\(uk)\",\"kb\":\(kb)}")

    case .flagsChanged:
        guard let name = modifierKeys[code], let mask = modifierMasks[code] else { return }
        let isDown = (event.flags.rawValue & mask) != 0
        if isDown && (code == 56 || code == 60) {
            // Shift 押下時だけ文脈をサンプリング（頻度が低いのでコストは無視できる）
            let ims = currentInputSourceID()
            let app = frontmostApp()
            writer.append("{\"t\":\(String(format: "%.1f", t)),\"e\":\"md\",\"k\":\"\(name)\"," +
                          "\"kb\":\(kb),\"ims\":\"\(ims)\",\"app\":\"\(app)\"}")
        } else {
            writer.append("{\"t\":\(String(format: "%.1f", t)),\"e\":\"\(isDown ? "md" : "mu")\",\"k\":\"\(name)\",\"kb\":\(kb)}")
        }

    default:
        return
    }
}

// MARK: - 権限チェック
//
// tapCreate は権限が無くても成功してイベントが1件も来ない、という失敗の仕方をする。
// 起動時に明示的に確認して stderr に残す。

let hasAccess = CGPreflightListenEventAccess()
FileHandle.standardError.write(
    "[roba-keylog] 入力監視: \(hasAccess ? "許可済み" : "未許可")\n".data(using: .utf8)!)
if !hasAccess {
    FileHandle.standardError.write("[roba-keylog] 許可ダイアログを要求します\n".data(using: .utf8)!)
    CGRequestListenEventAccess()
}

var globalTap: CFMachPort?

let callback: CGEventTapCallBack = { _, type, event, _ in
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let tap = globalTap { CGEvent.tapEnable(tap: tap, enable: true) }
        return Unmanaged.passUnretained(event)
    }
    handle(event: event, type: type)
    return Unmanaged.passUnretained(event)
}

let mask = (1 << CGEventType.keyDown.rawValue)
         | (1 << CGEventType.keyUp.rawValue)
         | (1 << CGEventType.flagsChanged.rawValue)

guard let tap = CGEvent.tapCreate(
    tap: .cgSessionEventTap,
    place: .headInsertEventTap,
    options: .listenOnly,
    eventsOfInterest: CGEventMask(mask),
    callback: callback,
    userInfo: nil
) else {
    FileHandle.standardError.write("""
    [roba-keylog] イベントタップを作成できませんでした。
    「システム設定 → プライバシーとセキュリティ → 入力監視」で
    roba-keylog を許可してください。

    """.data(using: .utf8)!)
    exit(1)
}
globalTap = tap

let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)

// 3秒ごとにバッファをフラッシュ
let timer = CFRunLoopTimerCreateWithHandler(kCFAllocatorDefault, CFAbsoluteTimeGetCurrent() + 3, 3, 0, 0) { _ in
    writer.flush()
}
CFRunLoopAddTimer(CFRunLoopGetCurrent(), timer, .commonModes)

// 終了時に取りこぼさない
signal(SIGTERM) { _ in writer.flush(); exit(0) }
signal(SIGINT)  { _ in writer.flush(); exit(0) }

FileHandle.standardError.write(
    "[roba-keylog] 計測開始 mode=\(fullMode ? "full" : (symbolMode ? "anon+sym" : "anon")) out=\(logDir)\n".data(using: .utf8)!)

CFRunLoopRun()
