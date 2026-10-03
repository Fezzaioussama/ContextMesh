import assert from "node:assert/strict";
import test from "node:test";
import { ESLint } from "eslint";

const linter = new ESLint({ overrideConfigFile: "eslint.config.mjs" });

async function messages(code, filePath = "fixture.ts") {
  const [result] = await linter.lintText(code, { filePath });
  return result.messages;
}

function forRule(diagnostics, rule) {
  return diagnostics.filter((finding) => finding.ruleId === rule);
}

test("complexity four is accepted", async () => {
  const code = `export function choose(x: number) {
    if (x === 1) return "one";
    if (x === 2) return "two";
    if (x === 3) return "three";
    return "other";
  }`;
  assert.deepEqual(await messages(code), []);
});

test("complexity five is rejected", async () => {
  const code = `export function choose(x: number) {
    if (x === 1) return "one";
    if (x === 2) return "two";
    if (x === 3) return "three";
    if (x === 4) return "four";
    return "other";
  }`;
  assert.equal(forRule(await messages(code), "complexity").length, 1);
});

test("nested arrow functions are checked", async () => {
  const code = `export function factory() {
    return (x: number) => {
      if (x === 1) return 1;
      if (x === 2) return 2;
      if (x === 3) return 3;
      if (x === 4) return 4;
      return 0;
    };
  }`;
  assert.equal(forRule(await messages(code), "complexity").length, 1);
});

test("React TSX is parsed and checked", async () => {
  const code = `export const Card = ({count}: {count: number}) => {
    if (count === 1) return <div>One</div>;
    if (count === 2) return <div>Two</div>;
    if (count === 3) return <div>Three</div>;
    if (count === 4) return <div>Four</div>;
    return <div>Other</div>;
  };`;
  assert.equal(forRule(await messages(code, "fixture.tsx"), "complexity").length, 1);
});

test("inline directives cannot disable the complexity gate", async () => {
  const code = `/* eslint complexity: off */
  export const choose = (x: number) => {
    if (x === 1) return 1;
    if (x === 2) return 2;
    if (x === 3) return 3;
    if (x === 4) return 4;
    return 0;
  };`;
  assert.equal(forRule(await messages(code), "complexity").length, 1);
});

test("one thousand physical lines are accepted", async () => {
  const code = "// maintained text\n".repeat(1000);
  assert.deepEqual(await messages(code), []);
});

test("one thousand and one physical lines are rejected", async () => {
  const code = "// maintained text\n".repeat(1001);
  assert.equal(forRule(await messages(code), "max-lines").length, 1);
});

test("frontend cannot directly import the Python backend", async () => {
  const code = 'import "../../backend/src/context_mesh/domain/model";';
  const diagnostics = await messages(code, "frontend/src/fixture.ts");
  assert.equal(forRule(diagnostics, "no-restricted-imports").length, 1);
});

test("frontend TypeScript module files receive the import boundary", async () => {
  const code = 'import "../../backend/src/context_mesh/domain/model";';
  const diagnostics = await messages(code, "frontend/src/fixture.mts");
  assert.equal(forRule(diagnostics, "no-restricted-imports").length, 1);
});

test("frontend JavaScript module files receive the import boundary", async () => {
  const code = 'import "../../backend/src/context_mesh/domain/model";';
  const diagnostics = await messages(code, "frontend/src/fixture.mjs");
  assert.equal(forRule(diagnostics, "no-restricted-imports").length, 1);
});

test("dependency and cache directories are excluded from JS analysis", async () => {
  const paths = [
    "third_party/provider.js",
    ".venv-quality/generated.js",
    ".cache/tool-result.js",
    "frontend/node_modules/package/index.js",
  ];
  for (const path of paths) {
    assert.equal(await linter.isPathIgnored(path), true);
  }
});

test("parse errors are reported instead of silently skipped", async () => {
  const diagnostics = await messages("export function incomplete(");
  assert.equal(diagnostics.length, 1);
  assert.equal(diagnostics[0].fatal, true);
});
