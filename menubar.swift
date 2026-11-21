// The menu bar bird. macOS 26 does not show a menu bar item that Python creates, so this helper shows it and
// passes clicks on. stdin, one line per change: "<state>\t<mode: window|chrome|mac>\t<has a token: 0|1>".
// stdout: the action of the clicked item. It quits when stdin closes.
import AppKit

let shortTitles = ["Starting…": "…", "Listening": "", "Muted": "muted", "Off": "off"]

final class Menu: NSObject {
    let bird = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let state = NSMenuItem(title: "Starting…", action: nil, keyEquivalent: "")
    lazy var mute = entry("Mute", "mute")
    lazy var power = entry("Turn Off", "power")
    lazy var wholeMac = entry("My whole Mac", "mac")
    lazy var myChrome = entry("My Chrome", "chrome")
    lazy var ownWindow = entry("Separate Chrome window", "window")

    init(icon: String) {
        super.init()
        let image = NSImage(contentsOfFile: icon)
        image?.size = NSSize(width: 18, height: 18)
        image?.isTemplate = true
        bird.button?.image = image
        bird.button?.imagePosition = .imageLeading
        let menu = NSMenu()
        menu.autoenablesItems = false
        state.isEnabled = false
        let browser = NSMenuItem(title: "Works in", action: nil, keyEquivalent: "")
        browser.submenu = NSMenu()
        browser.submenu?.autoenablesItems = false
        [wholeMac, myChrome, ownWindow].forEach { browser.submenu?.addItem($0) }
        [state, .separator(), mute, power, browser, .separator(), entry("Quit Beacon", "quit")].forEach { menu.addItem($0) }
        bird.menu = menu
    }

    func entry(_ title: String, _ action: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: #selector(clicked(_:)), keyEquivalent: "")
        item.target = self
        item.representedObject = action
        return item
    }

    @objc func clicked(_ sender: NSMenuItem) {
        print(sender.representedObject as! String)
        fflush(stdout)
    }

    func show(_ line: String) {
        let fields = line.components(separatedBy: "\t")
        guard fields.count == 3, let short = shortTitles[fields[0]] else { return }
        state.title = fields[0]
        bird.button?.title = short
        mute.state = fields[0] == "Muted" ? .on : .off
        mute.isEnabled = fields[0] == "Listening" || fields[0] == "Muted"
        power.title = fields[0] == "Off" ? "Turn On" : "Turn Off"
        for item in [wholeMac, myChrome, ownWindow] {
            item.state = item.representedObject as? String == fields[1] ? .on : .off
        }
        myChrome.isEnabled = fields[2] == "1"
        myChrome.title = fields[2] == "1" ? "My Chrome" : "My Chrome (set extension_token first)"
    }
}

let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let menu = Menu(icon: CommandLine.arguments[1])
FileHandle.standardInput.readabilityHandler = { input in
    let data = input.availableData
    if data.isEmpty { exit(0) }  // Beacon is gone
    for line in String(decoding: data, as: UTF8.self).split(separator: "\n") {
        DispatchQueue.main.async { menu.show(String(line)) }
    }
}
application.run()
