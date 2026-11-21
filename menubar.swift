// The menu bar bird. macOS 26 does not show a menu bar item that Python creates, so this helper shows it, owns the
// push-to-talk key, the key recorder and the Memory window, and passes what happens on to Beacon as JSON lines.
// Arguments: the bird icon, the memory notes file, the memory folder.
// stdin, one line per change: {"state", "mode", "token", "wake", "hotkey": {"code", "modifiers", "label"}, "name"}.
// stdout, one line per event: {"action": "push", "down": Bool} | {"action": "hotkey", "hotkey": {...}} |
// {"action": "wake" | "power" | "mac" | "chrome" | "window" | "quit"}. It quits when stdin closes.
import AppKit
import Carbon.HIToolbox

struct Hotkey: Codable, Equatable {
    var code: UInt32  // a Carbon virtual key code
    var modifiers: UInt32  // a Carbon modifier mask
    var label: String
}

struct Status: Decodable, Equatable {
    var state: String  // off, starting, ready, hearing, working, speaking
    var mode: String
    var token: Bool
    var wake: Bool
    var hotkey: Hotkey
    var name: String
}

let birdPoints: CGFloat = 22
let glyphPoints: CGFloat = 11
let glyphGap: CGFloat = 3
let dimmed: CGFloat = 0.35
let pulseSeconds = 0.5
let headlines = ["off": "Off", "starting": "Starting…", "ready": "Ready", "hearing": "Listening…",
                 "working": "Working…", "speaking": "Speaking…"]
let glyphs = ["starting": "ellipsis", "hearing": "mic.fill", "working": "ellipsis", "speaking": "waveform"]
let pulsing: Set<String> = ["starting", "working", "speaking"]
let hotkeySignature: OSType = 0x4243_4E4B  // "BCNK"
let keyNames: [UInt16: String] = [
    49: "Space", 36: "Return", 48: "Tab", 51: "Delete", 117: "⌦", 115: "Home", 119: "End", 116: "Page Up", 121: "Page Down",
    123: "←", 124: "→", 125: "↓", 126: "↑", 122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6", 98: "F7",
    100: "F8", 101: "F9", 109: "F10", 103: "F11", 111: "F12", 105: "F13", 107: "F14", 113: "F15", 106: "F16", 64: "F17",
    79: "F18", 80: "F19", 90: "F20",
]
let functionKeys: Set<UInt16> = [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111, 105, 107, 113, 106, 64, 79, 80, 90]
let escapeKey: UInt16 = 53

func send(_ message: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: message)
    print(String(decoding: data, as: UTF8.self))
    fflush(stdout)
}

/// The bird, with a small glyph beside it for what Beacon is doing: a microphone while it listens to you, dots while it
/// works, a waveform while it talks. Dimmed while off or starting.
func indicator(bird: NSImage?, state: String, faded: Bool) -> NSImage {
    let glyph = glyphs[state].flatMap { NSImage(systemSymbolName: $0, accessibilityDescription: headlines[state]) }?
        .withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: glyphPoints, weight: .semibold))
    let width = birdPoints + (glyph.map { glyphGap + $0.size.width } ?? 0)
    let image = NSImage(size: NSSize(width: width, height: birdPoints), flipped: false) { _ in
        let birdFraction = state == "off" || state == "starting" ? dimmed : 1
        bird?.draw(in: NSRect(x: 0, y: 0, width: birdPoints, height: birdPoints), from: .zero, operation: .sourceOver,
                   fraction: birdFraction)
        if let glyph {
            let frame = NSRect(x: birdPoints + glyphGap, y: (birdPoints - glyph.size.height) / 2,
                               width: glyph.size.width, height: glyph.size.height)
            glyph.draw(in: frame, from: .zero, operation: .sourceOver, fraction: faded ? dimmed : 1)
        }
        return true
    }
    image.isTemplate = true
    return image
}

func modifierLabel(_ modifiers: UInt32) -> String {
    [(controlKey, "⌃"), (optionKey, "⌥"), (shiftKey, "⇧"), (cmdKey, "⌘")]
        .filter { modifiers & UInt32($0.0) != 0 }.map { $0.1 }.joined()
}

final class Menu: NSObject {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let bird: NSImage?
    let headline = NSMenuItem(title: "Starting…", action: nil, keyEquivalent: "")
    let hint = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    lazy var wake = entry("", "wake")
    lazy var hotkeyItem = NSMenuItem(title: "Push-to-Talk Key…", action: #selector(recordHotkey), keyEquivalent: "")
    lazy var memoryItem = NSMenuItem(title: "Memory…", action: #selector(openMemory), keyEquivalent: "")
    lazy var power = entry("Turn Off", "power")
    lazy var wholeMac = entry("My whole Mac", "mac")
    lazy var myChrome = entry("My Chrome", "chrome")
    lazy var ownWindow = entry("Separate Chrome window", "window")
    let memory: MemoryWindow
    var recorder: KeyRecorder?
    var status: Status?
    var registered: (hotkey: Hotkey, reference: EventHotKeyRef)?
    var pushing = false
    var faded = false

    init(icon: String, memory: MemoryWindow) {
        bird = NSImage(contentsOfFile: icon)
        self.memory = memory
        super.init()
        item.button?.image = indicator(bird: bird, state: "starting", faded: false)
        let menu = NSMenu()
        menu.autoenablesItems = false
        headline.isEnabled = false
        hint.isEnabled = false
        for target in [hotkeyItem, memoryItem] { target.target = self }
        let works = NSMenuItem(title: "Works in", action: nil, keyEquivalent: "")
        works.submenu = NSMenu()
        works.submenu?.autoenablesItems = false
        [wholeMac, myChrome, ownWindow].forEach { works.submenu?.addItem($0) }
        [headline, hint, .separator(), wake, hotkeyItem, memoryItem, works, .separator(), power, entry("Quit Beacon", "quit")]
            .forEach { menu.addItem($0) }
        item.menu = menu
        Timer.scheduledTimer(withTimeInterval: pulseSeconds, repeats: true) { [weak self] _ in self?.pulse() }
    }

    func entry(_ title: String, _ action: String) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: #selector(clicked(_:)), keyEquivalent: "")
        entry.target = self
        entry.representedObject = action
        return entry
    }

    @objc func clicked(_ sender: NSMenuItem) {
        send(["action": sender.representedObject as! String])
    }

    /// The state to show: the key being held shows the microphone at once, before Beacon reports it.
    var shown: String {
        guard let status else { return "starting" }
        return pushing && status.state != "off" && status.state != "starting" ? "hearing" : status.state
    }

    func show(_ status: Status) {
        guard status != self.status else { return }
        self.status = status
        let wakePhrase = "“Hey \(status.name)”"
        headline.title = headlines[status.state] ?? status.state
        hint.title = "Hold \(status.hotkey.label) to talk" + (status.wake ? ", or say \(wakePhrase)" : "")
        wake.title = "Listen for \(wakePhrase)"
        wake.state = status.wake ? .on : .off
        hotkeyItem.title = "Push-to-Talk Key: \(status.hotkey.label)…"
        power.title = status.state == "off" ? "Turn On" : "Turn Off"
        for choice in [wholeMac, myChrome, ownWindow] {
            choice.state = choice.representedObject as? String == status.mode ? .on : .off
        }
        myChrome.isEnabled = status.token
        myChrome.title = status.token ? "My Chrome" : "My Chrome (set extension_token first)"
        if recorder == nil { register(status.hotkey) }
        draw()
    }

    func draw() {
        item.button?.image = indicator(bird: bird, state: shown, faded: faded && pulsing.contains(shown))
    }

    func pulse() {
        faded.toggle()
        if pulsing.contains(shown) { draw() }
    }

    func pushed(_ down: Bool) {
        guard down != pushing else { return }  // key repeat
        pushing = down
        send(["action": "push", "down": down])
        draw()
    }

    func register(_ hotkey: Hotkey?) {
        if let registered, registered.hotkey == hotkey { return }
        if let registered { UnregisterEventHotKey(registered.reference) }
        registered = nil
        guard let hotkey else { return }
        var reference: EventHotKeyRef?
        let result = RegisterEventHotKey(hotkey.code, hotkey.modifiers, EventHotKeyID(signature: hotkeySignature, id: 1),
                                         GetApplicationEventTarget(), 0, &reference)
        if result == noErr, let reference {
            registered = (hotkey, reference)
        } else {
            FileHandle.standardError.write(Data("Push-to-talk key \(hotkey.label) is taken by another app (\(result)).\n".utf8))
        }
    }

    @objc func recordHotkey() {
        register(nil)  // so pressing the current key is recorded instead of talking
        if pushing { pushed(false) }
        recorder = KeyRecorder(current: status?.hotkey) { [weak self] chosen in
            guard let self else { return }
            self.recorder = nil
            if let chosen {
                send(["action": "hotkey", "hotkey": ["code": chosen.code, "modifiers": chosen.modifiers, "label": chosen.label]])
                self.status?.hotkey = chosen
            }
            self.register(self.status?.hotkey)
            if let status = self.status { self.hotkeyItem.title = "Push-to-Talk Key: \(status.hotkey.label)…" }
        }
    }

    @objc func openMemory() {
        memory.show()
    }
}

/// A small window that records the key combination to hold for push-to-talk.
final class KeyRecorder: NSObject, NSWindowDelegate {
    let window = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 320, height: 150), styleMask: [.titled, .closable],
                         backing: .buffered, defer: false)
    let shown = NSTextField(labelWithString: "")
    let advice = NSTextField(labelWithString: "Press the keys you want to hold while you talk.")
    let save = NSButton(title: "Save", target: nil, action: nil)
    var chosen: Hotkey?
    var monitor: Any?
    let done: (Hotkey?) -> Void

    init(current: Hotkey?, done: @escaping (Hotkey?) -> Void) {
        self.done = done
        super.init()
        window.title = "Push-to-Talk Key"
        window.isReleasedWhenClosed = false
        window.delegate = self
        shown.stringValue = current?.label ?? "—"
        shown.font = .systemFont(ofSize: 28, weight: .medium)
        shown.alignment = .center
        advice.font = .systemFont(ofSize: NSFont.smallSystemFontSize)
        advice.textColor = .secondaryLabelColor
        advice.alignment = .center
        save.target = self
        save.action = #selector(saveChoice)
        save.keyEquivalent = "\r"
        save.isEnabled = false
        let cancel = NSButton(title: "Cancel", target: self, action: #selector(cancelChoice))
        let buttons = NSStackView(views: [cancel, save])
        let stack = NSStackView(views: [shown, advice, buttons])
        stack.orientation = .vertical
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 18, left: 18, bottom: 16, right: 18)
        window.contentView = stack
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            self?.record(event)
            return nil
        }
        NSApp.activate(ignoringOtherApps: true)
        window.center()
        window.makeKeyAndOrderFront(nil)
    }

    func record(_ event: NSEvent) {
        let flags = event.modifierFlags
        var modifiers: UInt32 = 0
        if flags.contains(.control) { modifiers |= UInt32(controlKey) }
        if flags.contains(.option) { modifiers |= UInt32(optionKey) }
        if flags.contains(.shift) { modifiers |= UInt32(shiftKey) }
        if flags.contains(.command) { modifiers |= UInt32(cmdKey) }
        if event.keyCode == escapeKey && modifiers == 0 { return cancelChoice() }
        let commandModifiers = modifiers & ~UInt32(shiftKey)
        guard commandModifiers != 0 || functionKeys.contains(event.keyCode) else {
            advice.stringValue = "Add ⌃, ⌥ or ⌘, or use a function key, so typing still works."
            return
        }
        let key = keyNames[event.keyCode] ?? (event.charactersIgnoringModifiers ?? "?").uppercased()
        chosen = Hotkey(code: UInt32(event.keyCode), modifiers: modifiers, label: modifierLabel(modifiers) + key)
        shown.stringValue = chosen!.label
        advice.stringValue = "Hold \(chosen!.label) and talk; let go when you are done."
        save.isEnabled = true
    }

    @objc func saveChoice() { finish(chosen) }
    @objc func cancelChoice() { finish(nil) }

    func finish(_ hotkey: Hotkey?) {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
        window.delegate = nil
        window.close()
        done(hotkey)
    }

    func windowWillClose(_ notification: Notification) { finish(nil) }  // the close button
}

/// Notes Beacon follows and files it can read and attach. Changes are saved as they are made; Beacon reads them at
/// the start of its next conversation.
final class MemoryWindow: NSObject, NSWindowDelegate, NSTextViewDelegate, NSTableViewDataSource {
    let notesFile: URL
    let folder: URL
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 440, height: 480), styleMask: [.titled, .closable, .resizable],
                          backing: .buffered, defer: false)
    let notesScroll = NSTextView.scrollableTextView()
    var notes: NSTextView { notesScroll.documentView as! NSTextView }
    let table = NSTableView()
    let remove = NSButton(title: "Remove", target: nil, action: nil)
    var files: [URL] = []

    init(notesFile: URL, folder: URL) {
        self.notesFile = notesFile
        self.folder = folder
        super.init()
        window.title = "Memory"
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 360, height: 400)
        notes.font = .systemFont(ofSize: 13)
        notes.isRichText = false
        notes.allowsUndo = true
        notes.textContainerInset = NSSize(width: 4, height: 6)
        notes.delegate = self
        notes.setValue(NSAttributedString(string: "My name is Sam. Keep replies short. I use Gmail and Notion.",
                                          attributes: [.foregroundColor: NSColor.placeholderTextColor, .font: notes.font!]),
                       forKey: "placeholderAttributedString")
        notesScroll.borderType = .bezelBorder
        table.addTableColumn(NSTableColumn(identifier: NSUserInterfaceItemIdentifier("name")))
        table.headerView = nil
        table.dataSource = self
        table.allowsMultipleSelection = true
        table.registerForDraggedTypes([.fileURL])
        let tableScroll = NSScrollView()
        tableScroll.documentView = table
        tableScroll.hasVerticalScroller = true
        tableScroll.borderType = .bezelBorder
        let add = NSButton(title: "Add Files…", target: self, action: #selector(addFiles))
        remove.target = self
        remove.action = #selector(removeFiles)
        let stack = NSStackView(views: [
            heading("Notes"), help("Who you are and how Beacon should work. It follows these on every task."), notesScroll,
            heading("Files"), help("Documents Beacon can read and attach for you. Drop them below or add them."), tableScroll,
            NSStackView(views: [add, remove]),
        ])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 6
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 18, bottom: 16, right: 18)
        stack.setCustomSpacing(16, after: notesScroll)
        for view in [notesScroll, tableScroll] {
            view.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -36).isActive = true
        }
        notesScroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 140).isActive = true
        tableScroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 120).isActive = true
        window.contentView = stack
        window.delegate = self
    }

    func heading(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = .boldSystemFont(ofSize: NSFont.systemFontSize)
        return label
    }

    func help(_ text: String) -> NSTextField {
        let label = NSTextField(wrappingLabelWithString: text)
        label.font = .systemFont(ofSize: NSFont.smallSystemFontSize)
        label.textColor = .secondaryLabelColor
        return label
    }

    func show() {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        notes.string = (try? String(contentsOf: notesFile, encoding: .utf8)) ?? ""
        reload()
        NSApp.activate(ignoringOtherApps: true)
        if !window.isVisible { window.center() }
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(notes)
    }

    func reload() {
        let found = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil,
                                                                 options: .skipsHiddenFiles)) ?? []
        files = found.sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
        table.reloadData()
        remove.isEnabled = !files.isEmpty
    }

    func textDidChange(_ notification: Notification) {
        do {
            try notes.string.write(to: notesFile, atomically: true, encoding: .utf8)
        } catch {
            FileHandle.standardError.write(Data("Could not save the memory notes: \(error)\n".utf8))
        }
    }

    func copyIn(_ urls: [URL]) {
        for source in urls {
            let target = folder.appendingPathComponent(source.lastPathComponent)
            do {
                if FileManager.default.fileExists(atPath: target.path) { try FileManager.default.removeItem(at: target) }
                try FileManager.default.copyItem(at: source, to: target)
            } catch {
                FileHandle.standardError.write(Data("Could not add \(source.lastPathComponent) to memory: \(error)\n".utf8))
            }
        }
        reload()
    }

    @objc func addFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.beginSheetModal(for: window) { [weak self] response in
            if response == .OK { self?.copyIn(panel.urls) }
        }
    }

    @objc func removeFiles() {
        for row in table.selectedRowIndexes where row < files.count {
            try? FileManager.default.trashItem(at: files[row], resultingItemURL: nil)  // the Trash, so it can be undone
        }
        reload()
    }

    func numberOfRows(in tableView: NSTableView) -> Int { files.count }

    func tableView(_ tableView: NSTableView, objectValueFor tableColumn: NSTableColumn?, row: Int) -> Any? {
        files[row].lastPathComponent
    }

    func tableView(_ tableView: NSTableView, validateDrop info: NSDraggingInfo, proposedRow row: Int,
                   proposedDropOperation dropOperation: NSTableView.DropOperation) -> NSDragOperation {
        tableView.setDropRow(-1, dropOperation: .on)  // the whole list is the target
        return .copy
    }

    func tableView(_ tableView: NSTableView, acceptDrop info: NSDraggingInfo, row: Int,
                   dropOperation: NSTableView.DropOperation) -> Bool {
        let urls = info.draggingPasteboard.readObjects(forClasses: [NSURL.self],
                                                       options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
        copyIn(urls.filter { !$0.hasDirectoryPath })
        return !urls.isEmpty
    }
}

/// Copy, paste and select-all reach a text view through the main menu, which an accessory app has to make itself.
func editMenu() -> NSMenu {
    let main = NSMenu()
    let holder = NSMenuItem()
    main.addItem(holder)
    let edit = NSMenu(title: "Edit")
    edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
    edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
    edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
    edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
    edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
    edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
    edit.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
    holder.submenu = edit
    return main
}

let application = NSApplication.shared
application.setActivationPolicy(.accessory)
application.mainMenu = editMenu()
let arguments = CommandLine.arguments
let menu = Menu(icon: arguments[1], memory: MemoryWindow(notesFile: URL(fileURLWithPath: arguments[2]),
                                                          folder: URL(fileURLWithPath: arguments[3], isDirectory: true)))
var hotkeyEvents = [EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
                    EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased))]
InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
    let down = GetEventKind(event) == UInt32(kEventHotKeyPressed)
    DispatchQueue.main.async { menu.pushed(down) }
    return noErr
}, hotkeyEvents.count, &hotkeyEvents, nil, nil)
var pending = ""  // a line can arrive split across reads
FileHandle.standardInput.readabilityHandler = { input in
    let data = input.availableData
    if data.isEmpty { exit(0) }  // Beacon is gone
    pending += String(decoding: data, as: UTF8.self)
    while let end = pending.firstIndex(of: "\n") {
        let line = String(pending[..<end])
        pending = String(pending[pending.index(after: end)...])
        if let status = try? JSONDecoder().decode(Status.self, from: Data(line.utf8)) {
            DispatchQueue.main.async { menu.show(status) }
        }
    }
}
application.run()
