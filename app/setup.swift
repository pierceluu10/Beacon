// First-launch permission onboarding and the Settings window, in SwiftUI hosted by AppKit windows.
// main.swift owns the top-level code and calls showOnboarding, showSettings and settingsModel.
import AVFoundation
import EventKit
import ServiceManagement
import SwiftUI

let privacyPane = "x-apple.systempreferences:com.apple.preference.security?"
let pollInterval = Duration.seconds(1)
let onboardingWidth: CGFloat = 420
let settingsWidth: CGFloat = 520
let markSize: CGFloat = 56
let iconWidth: CGFloat = 20  // symbols differ in width; a fixed column keeps the row text aligned
let fallbackMark = "light.beacon.max"

/// Shared state for the SwiftUI windows. main.swift sets `status` on every update and fills the two closures.
final class SettingsModel: ObservableObject {
    @Published var status: Status?
    var recordHotkey: () -> Void = {}
    var openMemory: () -> Void = {}
}
let settingsModel = SettingsModel()

enum Permission: CaseIterable, Identifiable {
    case microphone, accessibility, screen, calendar

    var id: Self { self }

    /// EventKit aborts a process that asks for access outside an app bundle, so Calendar is only offered in Beacon.app.
    static func needed(bundled: Bool) -> [Permission] { bundled ? allCases : allCases.filter(\.required) }

    var required: Bool { self != .calendar }

    var name: String {
        switch self {
        case .microphone: "Microphone"
        case .accessibility: "Accessibility"
        case .screen: "Screen Recording"
        case .calendar: "Calendar & Reminders"
        }
    }

    var reason: String {
        switch self {
        case .microphone: "Hears you when you talk to it."
        case .accessibility: "Clicks, types and presses keys for you."
        case .screen: "Sees the screen to know what to do."
        case .calendar: "Optional. Reads and adds events and reminders."
        }
    }

    var symbol: String {
        switch self {
        case .microphone: "mic"
        case .accessibility: "accessibility"
        case .screen: "rectangle.dashed.badge.record"
        case .calendar: "calendar"
        }
    }

    var granted: Bool {
        switch self {
        case .microphone: AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
        case .accessibility: AXIsProcessTrusted()
        case .screen: CGPreflightScreenCaptureAccess()
        case .calendar:
            EKEventStore.authorizationStatus(for: .event) == .fullAccess
                && EKEventStore.authorizationStatus(for: .reminder) == .fullAccess
        }
    }

    func request() {
        switch self {
        case .microphone:
            if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
                AVCaptureDevice.requestAccess(for: .audio) { _ in }
            } else {
                openPrivacy("Privacy_Microphone")
            }
        case .accessibility:
            forgetEarlierBuilds("Accessibility")
            AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue(): true] as CFDictionary)
            openPrivacy("Privacy_Accessibility")
        case .screen:
            forgetEarlierBuilds("ScreenCapture")
            askedForScreen = true
            if !CGRequestScreenCaptureAccess() { openPrivacy("Privacy_ScreenCapture") }
        case .calendar:
            Task { await requestCalendar() }
        }
    }

    /// A denied request returns at once without asking again, so that case goes to System Settings instead.
    private func requestCalendar() async {
        let store = EKEventStore()
        do {
            switch EKEventStore.authorizationStatus(for: .event) {
            case .notDetermined: _ = try await store.requestFullAccessToEvents()
            case .fullAccess: break
            default: return openPrivacy("Privacy_Calendars")
            }
            switch EKEventStore.authorizationStatus(for: .reminder) {
            case .notDetermined: _ = try await store.requestFullAccessToReminders()
            case .fullAccess: break
            default: openPrivacy("Privacy_Reminders")
            }
        } catch {
            FileHandle.standardError.write(Data("Could not ask for Calendar and Reminders access: \(error)\n".utf8))
        }
    }

    /// An ad-hoc signed build is a new app to macOS, so a switch turned on for an earlier build stays on in System
    /// Settings but does nothing. Clearing Beacon's own entries first makes the switch the user turns on this build's.
    private func forgetEarlierBuilds(_ service: String) {
        guard let bundle = Bundle.main.bundleIdentifier else { return }  // from a terminal the grant is the terminal's
        let reset = Process()
        reset.executableURL = URL(fileURLWithPath: "/usr/bin/tccutil")
        reset.arguments = ["reset", service, bundle]
        try? reset.run()
        reset.waitUntilExit()
    }

    private func openPrivacy(_ anchor: String) {
        NSWorkspace.shared.open(URL(string: privacyPane + anchor)!)
    }
}

struct PermissionRow: View {
    let permission: Permission
    let granted: Bool

    var body: some View {
        LabeledContent {
            if granted {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .imageScale(.large)
                    .accessibilityLabel("Allowed")
            } else {
                Button("Allow", action: permission.request)
            }
        } label: {
            Label {
                Text(permission.name)
                Text(permission.reason)
            } icon: {
                Image(systemName: permission.symbol).frame(width: iconWidth)
            }
        }
    }
}

/// The permission rows, re-checked every second so they flip to allowed when the user comes back from System Settings.
struct PermissionsSection: View {
    let permissions: [Permission]
    @Binding var granted: Set<Permission>
    var title: String?

    var body: some View {
        Section {
            ForEach(permissions) { PermissionRow(permission: $0, granted: granted.contains($0)) }
        } header: {
            if let title { Text(title) }
        }
        .task {
            while !Task.isCancelled {
                granted = Set(permissions.filter(\.granted))
                try? await Task.sleep(for: pollInterval)
            }
        }
    }
}

struct OnboardingView: View {
    let permissions: [Permission]
    let close: () -> Void
    @State private var granted: Set<Permission> = []

    var body: some View {
        VStack(spacing: 0) {
            mark
                .frame(width: markSize, height: markSize)
                .padding(.top, 28)
            Text("Beacon needs a few permissions")
                .font(.title2.weight(.semibold))
                .padding(.top, 14)
            Form {
                PermissionsSection(permissions: permissions, granted: $granted)
            }
            .formStyle(.grouped)
            .scrollDisabled(true)
            Button("Continue", action: close)
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)  // never disabled: macOS often reports Screen Recording only after a relaunch
                .padding(.bottom, 24)
        }
        .fixedSize(horizontal: false, vertical: true)  // as tall as its rows, which are fewer outside the app
        .frame(width: onboardingWidth)
    }

    @ViewBuilder var mark: some View {
        if let url = Bundle.main.url(forResource: "mark", withExtension: "png"), let image = NSImage(contentsOf: url) {
            Image(nsImage: image).resizable().renderingMode(.template).scaledToFit()  // black ink, so it follows the theme
        } else {
            Image(systemName: fallbackMark).resizable().scaledToFit().foregroundStyle(.tint)
        }
    }
}

struct SettingsView: View {
    let bundled: Bool
    @ObservedObject var model = settingsModel
    @State private var granted: Set<Permission> = []

    var body: some View {
        Form {
            Section("General") {
                if let status = model.status {
                    // The controls show the live status and only send the action; the next status update confirms it.
                    Toggle("Listen for “Hey \(status.name)”",
                           isOn: Binding(get: { status.wake }, set: { _ in send(["action": "wake"]) }))
                    LabeledContent("Push-to-talk key") {
                        HStack {
                            Text(status.hotkey.label).foregroundStyle(.secondary)
                            Button("Change…", action: model.recordHotkey)
                        }
                    }
                    Picker("Works in", selection: Binding(get: { status.mode }, set: { send(["action": $0]) })) {
                        Text("Whole Mac").tag("mac")
                        Text("My Chrome").tag("chrome").disabled(!status.token)
                        Text("Separate Chrome window").tag("window")
                    }
                } else {
                    Text("Starting…").foregroundStyle(.secondary)
                }
                if bundled { LoginToggle() }
            }
            Section("Memory") {
                LabeledContent {
                    Button("Edit…", action: model.openMemory)
                } label: {
                    Text("Notes and files")
                    Text("Who you are, how Beacon should work, and files it can attach.")
                }
            }
            PermissionsSection(permissions: Permission.needed(bundled: bundled), granted: $granted, title: "Permissions")
        }
        .formStyle(.grouped)
        .scrollDisabled(true)
        .fixedSize(horizontal: false, vertical: true)
        .frame(width: settingsWidth)
    }
}

struct LoginToggle: View {
    @State private var enabled = SMAppService.mainApp.status == .enabled

    var body: some View {
        Toggle("Open at login", isOn: Binding(get: { enabled }, set: change))
    }

    func change(_ on: Bool) {
        let service = SMAppService.mainApp
        do {
            if on { try service.register() } else { try service.unregister() }
        } catch {
            FileHandle.standardError.write(Data("Could not change Open at login: \(error)\n".utf8))
        }
        if service.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
        enabled = service.status == .enabled
    }
}

/// An AppKit window around a SwiftUI view. Closing it tears the view down, which ends its polling task.
final class HostedWindow: NSObject, NSWindowDelegate {
    let window: NSWindow
    let closed: () -> Void

    init(title: String, content: some View, closed: @escaping () -> Void) {
        window = NSWindow(contentViewController: NSHostingController(rootView: content))
        self.closed = closed
        super.init()
        window.title = title
        window.styleMask = [.titled, .closable]
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        NSApp.activate()  // an accessory app's windows open behind the front app otherwise
        window.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        window.delegate = nil
        window.contentViewController = nil
        closed()
    }
}

/// Opens a fresh copy of Beacon and quits this one; Python stops when its pipe to this process closes.
func relaunch() {
    let fresh = NSWorkspace.OpenConfiguration()
    fresh.createsNewApplicationInstance = true
    NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: fresh) { _, _ in
        DispatchQueue.main.async { NSApp.terminate(nil) }
    }
}

var askedForScreen = false
var onboardingWindow: HostedWindow?
var settingsWindow: HostedWindow?

/// Shows the onboarding window when a required permission is missing and calls `done` when the user finishes it;
/// calls `done` at once when everything required is already granted. `bundled` is true inside Beacon.app.
func showOnboarding(bundled: Bool, done: @escaping () -> Void) {
    let permissions = Permission.needed(bundled: bundled)
    if permissions.filter(\.required).allSatisfy(\.granted) { return done() }
    let view = OnboardingView(permissions: permissions) {
        // macOS applies Screen Recording from the app's next launch, so Continue relaunches once instead of starting
        if bundled && askedForScreen && !Permission.screen.granted { return relaunch() }
        onboardingWindow?.window.close()
    }
    onboardingWindow = HostedWindow(title: "Welcome to Beacon", content: view) {
        onboardingWindow = nil
        done()
    }
}

/// Opens (or brings forward) the Settings window.
func showSettings(bundled: Bool) {
    if let settingsWindow {
        NSApp.activate()
        return settingsWindow.window.makeKeyAndOrderFront(nil)
    }
    settingsWindow = HostedWindow(title: "Beacon Settings", content: SettingsView(bundled: bundled)) { settingsWindow = nil }
}
