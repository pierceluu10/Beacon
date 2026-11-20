// Prints the address and the title of the front tab when the app in front is a Chrome other than the agent's own.
// argv: the agent's Chrome profile directory. Prints nothing when there is nothing to report. Read-only.
ObjC.import("AppKit");
ObjC.import("ScriptingBridge");

const CHROME = "com.google.Chrome";
const NOT_PERMITTED = -1743;
const code = text => [...text].reduce((sum, character) => sum * 256 + character.charCodeAt(0), 0);

function run([agentProfile]) {
  const front = $.NSWorkspace.sharedWorkspace.frontmostApplication;
  if (front.bundleIdentifier.js !== CHROME) return "";
  const pid = front.processIdentifier;
  const shell = Application.currentApplication();
  shell.includeStandardAdditions = true;
  if (shell.doShellScript(`ps -ww -o command= -p ${pid}`).includes(agentProfile)) return "";
  // Every Chrome shares one name and bundle id, and an event addressed by either reaches an arbitrary instance
  // (Application(pid) included). Only ScriptingBridge really addresses a process id; it takes raw four-letter codes.
  const window = $.SBApplication.applicationWithProcessIdentifier(pid).elementArrayWithCode(code("cwin")).objectAtIndex(0);
  const read = reference => {
    const value = reference.get;
    if (reference.lastError.js && reference.lastError.code === NOT_PERMITTED) throw new Error(String(NOT_PERMITTED));
    return value.js;  // undefined on any other error, such as no open window (-1719)
  };
  if (read(window.propertyWithCode(code("mode"))) !== "normal") return "";  // incognito, or no window
  const tab = window.propertyWithCode(code("acTa"));
  const [address, title] = ["URL ", "pnam"].map(property => read(tab.propertyWithCode(code(property))));
  return address ? `${address}\n${title}` : "";
}
