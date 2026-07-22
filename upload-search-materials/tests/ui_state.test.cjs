const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

let UiState = {};
try {
  UiState = require(path.resolve(
    __dirname,
    "../src/upload_search_materials/interaction/static/ui-state.js",
  ));
} catch (_error) {
  // Assertions below report the missing production interface as test failures.
}

test("results recovery remains eligible while local edits are dirty", () => {
  assert.equal(typeof UiState.createState, "function");
  let state = UiState.createState("results");
  state = UiState.receiveStage(state, {
    stageId: "results",
    status: "blocked",
    result: { summary: "需要人工处理" },
  });
  state = UiState.markDirty(state);
  state = UiState.receiveStatus(state, "blocked", "2026-07-21T08:00:00Z");

  assert.deepEqual(UiState.recoveryView(state), {
    visible: true,
    submitEnabled: true,
  });
  assert.equal(state.serverStatus, "blocked");
  assert.equal(state.dirty, true);

  state = UiState.receiveStatus(state, "processing", "2026-07-21T08:01:00Z");
  assert.equal(state.dirty, false);
  assert.equal(UiState.recoveryView(state).visible, false);
});

test("result view clears stale content and later replaces it", () => {
  assert.equal(typeof UiState.receiveStage, "function");
  let state = UiState.createState("production_confirmation");
  state = UiState.receiveStage(state, {
    stageId: "production_confirmation",
    status: "completed",
    result: { summary: "旧结果", revision: 1 },
  });
  assert.equal(UiState.resultView(state).result.summary, "旧结果");

  state = UiState.receiveStage(state, {
    stageId: "production_confirmation",
    status: "draft",
    result: null,
  });
  assert.deepEqual(UiState.resultView(state), { mode: "empty", label: "尚未扫描" });

  state = UiState.receiveStage(state, {
    stageId: "production_confirmation",
    status: "completed",
    result: { summary: "新结果", revision: 2 },
  });
  assert.equal(UiState.resultView(state).result.summary, "新结果");
});

test("processing stays accepted when its heartbeat is old", () => {
  assert.equal(typeof UiState.connectionView, "function");
  let state = UiState.createState("asset_matching");
  state = UiState.receiveStage(state, {
    stageId: "asset_matching",
    status: "processing",
    result: null,
    lastAgentHeartbeat: "2026-07-21T08:00:00Z",
  });

  const view = UiState.connectionView(state, Date.parse("2026-07-21T09:00:00Z"));
  assert.equal(view.offline, false);
  assert.equal(view.statusLabel, "Agent 处理中");
  assert.equal(view.connectionLabel, "Agent 心跳暂不可见");
});

test("stage switch clears recovery cache and ignores the prior request", () => {
  assert.equal(typeof UiState.switchStage, "function");
  let state = UiState.createState("setup");
  state = UiState.receiveRecovery(state, "setup", "setup recovery");
  assert.equal(state.recoveryInstruction, "setup recovery");

  state = UiState.switchStage(state, "scope");
  assert.equal(state.recoveryInstruction, null);
  state = UiState.receiveRecovery(state, "setup", "late setup recovery");
  assert.equal(state.recoveryInstruction, null);
});

test("stage switch clears old submission time and hydrates the selected stage", () => {
  assert.equal(typeof UiState.submissionView, "function");
  let state = UiState.createState("setup");
  state = UiState.receiveStage(state, {
    stageId: "setup",
    status: "ready_for_agent",
    submission: { created_at: "2026-07-22T08:00:00+00:00" },
  });
  assert.equal(UiState.submissionView(state).createdAt, "2026-07-22T08:00:00+00:00");

  state = UiState.switchStage(state, "scope");
  assert.equal(UiState.submissionView(state).createdAt, null);

  state = UiState.receiveStage(state, {
    stageId: "scope",
    status: "completed",
    submission: { created_at: "2026-07-22T08:05:00+00:00" },
  });
  assert.equal(UiState.submissionView(state).createdAt, "2026-07-22T08:05:00+00:00");
});

test("saved generic values produce control presentations without defaults", () => {
  assert.equal(typeof UiState.controlPresentation, "function");
  assert.deepEqual(
    UiState.controlPresentation(["root-a", "root-b"], "list", 3),
    { values: ["root-a", "root-b", ""], checked: false },
  );
  assert.deepEqual(
    UiState.controlPresentation(true, "boolean", 1),
    { values: [""], checked: true },
  );
  assert.deepEqual(
    UiState.controlPresentation(["sku-1", "sku-2"], "line-list", 1),
    { values: ["sku-1\nsku-2"], checked: false },
  );
  assert.deepEqual(
    UiState.controlPresentation([{ id: "sku-1" }], "json-list", 1),
    { values: ['[\n  {\n    "id": "sku-1"\n  }\n]'], checked: false },
  );
});
