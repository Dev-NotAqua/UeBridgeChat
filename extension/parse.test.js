// UECBParse self-tests: `node parse.test.js` (no dependencies).
const fs = require("fs");
const path = require("path");
const UECBParse = new Function(
  fs.readFileSync(path.join(__dirname, "parse.js"), "utf8") + "; return UECBParse;"
)();

let failures = 0;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failures++;
    console.error(`FAIL ${name}\n  expected: ${JSON.stringify(expected)}\n  actual:   ${JSON.stringify(actual)}`);
  } else {
    console.log(`ok ${name}`);
  }
}

// Non-blocks are ignored.
check("plain text", UECBParse.parseBlock("hello world"), null);
check("lone end marker", UECBParse.parseBlock("#!ue end"), null);
check("header past line 5", UECBParse.parseBlock("1\n2\n3\n4\n5\n#!ue python\nx\n#!ue end"), null);

// Python.
check("python complete", UECBParse.parseBlock("#!ue python\nprint(1)\n#!ue end"),
  { complete: true, title: "python", command: { kind: "python", code: "print(1)" }, error: null });
check("python streaming", UECBParse.parseBlock("#!ue python\nprint(1)"),
  { complete: false, title: "python", command: { kind: "python", code: "print(1)" }, error: null });
check("python empty", UECBParse.parseBlock("#!ue python\n#!ue end").error, "The python block is empty.");
check("python tolerant header", UECBParse.parseBlock("  #! UE   PYTHON  \nprint(1)\n#!ue end").command,
  { kind: "python", code: "print(1)" });

// Call.
check("call with args", UECBParse.parseBlock('#!ue call TS Tool\n{"a": 1}\n#!ue end').command,
  { kind: "call", toolset: "TS", tool: "Tool", args: { a: 1 } });
check("call dotted toolset", UECBParse.parseBlock("#!ue call TS.Tool\n{}\n#!ue end").command,
  { kind: "call", toolset: "TS", tool: "Tool", args: {} });
check("call no args", UECBParse.parseBlock("#!ue call TS Tool\n#!ue end").command,
  { kind: "call", toolset: "TS", tool: "Tool", args: {} });
check("call missing tool", UECBParse.parseBlock("#!ue call TS\n{}\n#!ue end").error,
  "Write the header as: #!ue call <toolset name> <tool name>");
check("call bad json", UECBParse.parseBlock("#!ue call TS Tool\n{nope}\n#!ue end").error.startsWith(
  "The arguments are not valid JSON"), true);

// Others.
check("toolsets", UECBParse.parseBlock("#!ue toolsets\n#!ue end").command, { kind: "toolsets" });
check("describe", UECBParse.parseBlock("#!ue describe TS\n#!ue end").command, { kind: "describe", toolset: "TS" });
check("describe missing", UECBParse.parseBlock("#!ue describe\n#!ue end").error,
  "Write the header as: #!ue describe <toolset name>");
check("screenshot", UECBParse.parseBlock("#!ue screenshot\n#!ue end").command, { kind: "screenshot" });
check("unknown kind", UECBParse.parseBlock("#!ue frobnicate\nx\n#!ue end").error.startsWith(
  'Unknown command "#!ue frobnicate"'), true);

// Risk tripwire.
check("risk os", !!UECBParse.riskOf({ kind: "python", code: "import os\nos.remove('x')" }), true);
check("risk clean", UECBParse.riskOf({ kind: "python", code: "import unreal\nprint(1)" }), "");
check("risk non-python", UECBParse.riskOf({ kind: "call" }), "");

// Squash identity.
check("squash", UECBParse.squash("  a  \n\n  b \n"), "a\nb");

if (failures) {
  console.error(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log("\nAll parse tests passed.");
