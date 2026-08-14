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

test("results remains read-only even when the upload is blocked", () => {
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
    visible: false,
    submitEnabled: false,
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

test("a live processing claim reports that the Agent is processing", () => {
  assert.equal(typeof UiState.connectionView, "function");
  let state = UiState.createState("asset_matching");
  state = UiState.receiveStage(state, {
    stageId: "asset_matching",
    status: "processing",
    result: null,
    lastAgentHeartbeat: "2026-07-21T08:00:00Z",
  });

  const view = UiState.connectionView(
    state,
    Date.parse("2026-07-21T09:00:00Z"),
    {
      processingClaim: {
        expired: false,
        lease_expires_at: "2026-07-21T09:05:00Z",
      },
    },
  );
  assert.equal(view.offline, false);
  assert.equal(view.statusLabel, "Agent 处理中");
  assert.equal(view.connectionLabel, "Agent 正在处理");
});

test("a live wait reports listening even when the old heartbeat is stale", () => {
  let state = UiState.createState("setup");
  state = UiState.receiveStage(state, {
    stageId: "setup",
    status: "draft",
    lastAgentHeartbeat: "2026-07-21T08:00:00Z",
  });

  const view = UiState.connectionView(
    state,
    Date.parse("2026-07-21T09:00:00Z"),
    {
      agentWait: {
        expired: false,
        expires_at: "2026-07-21T09:00:30Z",
        remaining_seconds: 30,
      },
    },
  );
  assert.equal(view.offline, false);
  assert.equal(view.connectionLabel, "Agent 正在监听");
});

test("expired leases report disconnected regardless of an old heartbeat", () => {
  let state = UiState.createState("setup");
  state = UiState.receiveStage(state, {
    stageId: "setup",
    status: "ready_for_agent",
    lastAgentHeartbeat: "2026-07-21T08:59:59Z",
  });

  const view = UiState.connectionView(
    state,
    Date.parse("2026-07-21T09:00:00Z"),
    {
      agentWait: {
        expired: true,
        expires_at: "2026-07-21T08:59:59Z",
        remaining_seconds: 0,
      },
    },
  );
  assert.equal(view.offline, true);
  assert.equal(view.connectionLabel, "Agent 未连接");
});

test("selection preflight weights expensive formats and large images", () => {
  assert.equal(UiState.selectionPreflightWeight({
    width: 1440,
    height: 1920,
    format: "JPEG",
  }), 1);
  assert.equal(UiState.selectionPreflightWeight({
    width: 1440,
    height: 1920,
    format: "PNG",
  }), 2);
  assert.equal(UiState.selectionPreflightWeight({
    width: 8000,
    height: 5000,
    format: "JPEG",
  }), 3);
  assert.equal(UiState.selectionPreflightWeight({
    width: 10000,
    height: 7000,
    format: "JPEG",
  }), 4);
});

test("selection preflight scheduler runs six normal images from one source", async () => {
  const releases = [];
  const started = [];
  const scheduler = UiState.createSelectionPreflightScheduler({
    maxConcurrent: 6,
    maxWeight: 8,
    execute: (candidate) => new Promise((resolve) => {
      started.push(candidate.asset_id);
      releases.push(() => resolve({ status: "passed" }));
    }),
  });
  const requests = Array.from({ length: 6 }, (_, index) => scheduler.select({
    asset_id: `asset-${index}`,
    source_system: "same-nas-source",
    width: 1440,
    height: 1920,
    format: "JPEG",
  }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(started.length, 6);
  releases.forEach((release) => release());
  await Promise.all(requests);
});

test("selection preflight scheduler limits expensive images by total weight", async () => {
  const releases = [];
  const started = [];
  const scheduler = UiState.createSelectionPreflightScheduler({
    maxConcurrent: 6,
    maxWeight: 8,
    execute: (candidate) => new Promise((resolve) => {
      started.push(candidate.asset_id);
      releases.push(() => resolve({ status: "passed" }));
    }),
  });
  const requests = Array.from({ length: 5 }, (_, index) => scheduler.select({
    asset_id: `png-${index}`,
    source_system: "same-nas-source",
    width: 1440,
    height: 1920,
    format: "PNG",
  }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(started.length, 4);
  releases[0]();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(started.length, 5);
  releases.slice(1).forEach((release) => release());
  await Promise.all(requests);
});

test("queued preflight can be cancelled before it starts", async () => {
  let releaseFirst;
  const started = [];
  const scheduler = UiState.createSelectionPreflightScheduler({
    maxConcurrent: 1,
    maxWeight: 1,
    execute: (candidate) => new Promise((resolve) => {
      started.push(candidate.asset_id);
      if (candidate.asset_id === "first") releaseFirst = resolve;
    }),
  });
  const first = scheduler.select({ asset_id: "first", width: 1, height: 1 });
  const second = scheduler.select({ asset_id: "second", width: 1, height: 1 });
  scheduler.cancel("second");
  assert.deepEqual(await second, { status: "cancelled", cancelled: true });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(started, ["first"]);
  releaseFirst({ status: "passed" });
  await first;
});

test("running preflight keeps its cache result but not cancelled selection intent", async () => {
  let release;
  let executions = 0;
  const scheduler = UiState.createSelectionPreflightScheduler({
    execute: () => new Promise((resolve) => {
      executions += 1;
      release = resolve;
    }),
  });
  const request = scheduler.select({ asset_id: "asset-a", width: 1, height: 1 });
  await new Promise((resolve) => setImmediate(resolve));
  scheduler.cancel("asset-a");
  assert.equal(scheduler.get("asset-a").desiredSelected, false);
  release({ status: "passed", feasible_ratios: ["1:1"] });
  await request;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(executions, 1);
  assert.equal(scheduler.get("asset-a").state, "completed");
  assert.equal(scheduler.get("asset-a").desiredSelected, false);
  assert.equal(scheduler.get("asset-a").result.status, "passed");

  const reused = await scheduler.select({ asset_id: "asset-a", width: 1, height: 1 });
  assert.equal(reused.status, "passed");
  assert.equal(executions, 1);
  assert.equal(scheduler.get("asset-a").desiredSelected, true);
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

test("an in-flight response is rejected after the user switches stages", () => {
  assert.equal(typeof UiState.createRequestIdentity, "function");
  assert.equal(typeof UiState.isCurrentRequest, "function");
  const request = UiState.createRequestIdentity("setup", "session-1", 4);

  assert.equal(
    UiState.isCurrentRequest(request, "setup", "session-1", 4),
    true,
  );
  assert.equal(
    UiState.isCurrentRequest(request, "scope", "session-1", 5),
    false,
  );
  assert.equal(
    UiState.isCurrentRequest(request, "setup", "session-2", 4),
    false,
  );
});

test("generic result sections include only present blocking reasons and next action", () => {
  assert.equal(typeof UiState.resultSections, "function");
  assert.deepEqual(
    UiState.resultSections({
      blocking_reasons: ["缺少主图", "别名未确认"],
      next_action: "补充后重新提交",
    }),
    {
      blockingReasons: ["缺少主图", "别名未确认"],
      nextAction: "补充后重新提交",
    },
  );
  assert.deepEqual(UiState.resultSections({}), {
    blockingReasons: [],
    nextAction: null,
  });
});

test("draft requests send the observed revision and adopt the authoritative response", () => {
  assert.equal(typeof UiState.draftRequestBody, "function");
  assert.equal(typeof UiState.persistedRevision, "function");
  assert.deepEqual(
    UiState.draftRequestBody({ store: "draft" }, 4),
    { values: { store: "draft" }, revision: 4 },
  );
  assert.equal(UiState.persistedRevision({ revision: 9 }), 9);
});

test("historical sessions select the authoritative current stage", () => {
  assert.deepEqual(
    UiState.selectInitialStage(
      ["setup", "completeness", "asset_matching", "image_review"],
      "image_review",
    ),
    { stageId: "image_review", warning: null },
  );
  const fallback = UiState.selectInitialStage(["setup", "image_review"], "legacy");
  assert.equal(fallback.stageId, "setup");
  assert.match(fallback.warning, /不受支持/);
});

test("the full navigation snapshot is derived without visiting each stage", () => {
  assert.deepEqual(
    UiState.stageSnapshot({
      current_stage: "image_review",
      stages: {
        setup: { revision: 4, status: "completed" },
        image_review: { revision: 40, status: "draft" },
      },
    }, ["setup", "image_review", "slots_copy"]),
    [
      { stageId: "setup", revision: 4, status: "completed" },
      { stageId: "image_review", revision: 40, status: "draft" },
      { stageId: "slots_copy", revision: 0, status: "draft" },
    ],
  );
});

test("submit intent has priority while persistence is in flight", () => {
  assert.equal(UiState.mergePersistenceIntent(null, "draft"), "draft");
  assert.equal(UiState.mergePersistenceIntent("draft", "draft"), "draft");
  assert.equal(UiState.mergePersistenceIntent("draft", "submit"), "submit");
  assert.equal(UiState.mergePersistenceIntent("submit", "draft"), "submit");
});

test("JSON comparison ignores object key order but preserves array order", () => {
  assert.equal(typeof UiState.jsonSemanticallyEqual, "function");
  assert.equal(
    UiState.jsonSemanticallyEqual(
      [{ asset_id: "a", decision: { action: "use", ratio: "3:4" } }],
      [{ decision: { ratio: "3:4", action: "use" }, asset_id: "a" }],
    ),
    true,
  );
  assert.equal(
    UiState.jsonSemanticallyEqual(
      [{ asset_id: "a" }, { asset_id: "b" }],
      [{ asset_id: "b" }, { asset_id: "a" }],
    ),
    false,
  );
});

test("poll refreshes only when authoritative revision or status changes", () => {
  assert.equal(typeof UiState.stagePollChanged, "function");
  assert.equal(UiState.stagePollChanged(4, "draft", 4, "draft"), false);
  assert.equal(UiState.stagePollChanged(4, "draft", 5, "draft"), true);
  assert.equal(UiState.stagePollChanged(4, "draft", 4, "processing"), true);
});

test("legacy fifth-stage states map to three progressive pages", () => {
  assert.equal(typeof UiState.fifthStagePage, "function");
  assert.equal(UiState.fifthStagePage("analysing"), "compose");
  assert.equal(UiState.fifthStagePage("plan_review"), "compose");
  assert.equal(UiState.fifthStagePage("plan_confirmed"), "process");
  assert.equal(UiState.fifthStagePage("processing"), "process");
  assert.equal(UiState.fifthStagePage("outputs_ready"), "copy");
  assert.equal(UiState.fifthStagePage("copy_generating"), "copy");
  assert.equal(UiState.fifthStagePage("copy_review"), "copy");
  assert.equal(UiState.fifthStagePage("completed"), "copy");
  assert.equal(UiState.fifthStagePage("unknown"), "compose");
});

test("historical fifth-stage states map into the two-page workflow", () => {
  assert.equal(UiState.twoStepFifthStagePage("analysing"), "process");
  assert.equal(UiState.twoStepFifthStagePage("plan_review"), "process");
  assert.equal(UiState.twoStepFifthStagePage("plan_confirmed"), "process");
  assert.equal(UiState.twoStepFifthStagePage("processing"), "process");
  assert.equal(UiState.twoStepFifthStagePage("outputs_ready"), "copy");
  assert.equal(UiState.twoStepFifthStagePage("copy_generating"), "copy");
  assert.equal(UiState.twoStepFifthStagePage("copy_review"), "copy");
  assert.equal(UiState.twoStepFifthStagePage("completed"), "copy");
});

test("selection guidance updates per change and excludes duplicate identities", () => {
  const one = UiState.assetSelectionGuidance([
    { asset_id: "a", sha256: "same", decision: "selected" },
  ], 3);
  assert.deepEqual(one, {
    selectedCount: 1,
    usableUnique: 1,
    duplicateCount: 0,
    completeSlots: 0,
    balancedCounts: [],
    minimumShortage: 2,
    fillAllMinimumShortage: 8,
  });

  const duplicate = UiState.assetSelectionGuidance([
    { asset_id: "a", sha256: "same", decision: "selected" },
    { asset_id: "b", sha256: "same", decision: "selected" },
    { asset_id: "c", sha256: "third", decision: "selected" },
  ], 3);
  assert.equal(duplicate.selectedCount, 3);
  assert.equal(duplicate.usableUnique, 2);
  assert.equal(duplicate.duplicateCount, 1);
  assert.equal(duplicate.completeSlots, 0);
  assert.equal(duplicate.minimumShortage, 1);

  const nine = UiState.assetSelectionGuidance(
    Array.from({ length: 9 }, (_, index) => ({
      asset_id: `asset-${index}`,
      sha256: `sha-${index}`,
      decision: "selected",
    })),
    3,
  );
  assert.deepEqual(nine.balancedCounts, [3, 3, 3]);
});

test("duplicate image source labels use the nearest unique business directory", () => {
  assert.equal(typeof UiState.disambiguateImageSourceLabels, "function");
  const sources = UiState.disambiguateImageSourceLabels([
    {
      label: "浙江酷趣",
      path: "\\\\192.168.110.20\\浙江酷趣\\运营中心\\营销板块\\小红书koc置换&买家秀\\优质买家秀\\",
    },
    {
      label: "视觉部",
      path: "\\\\192.168.124.85\\视觉部\\1-模特图\\",
    },
    {
      label: "浙江酷趣",
      path: "\\\\192.168.110.20\\浙江酷趣\\运营中心\\营销板块\\小红书koc置换&淘宝买家秀\\优质买家秀\\",
    },
  ]);

  assert.deepEqual(sources.map((source) => source.label), [
    "浙江酷趣-小红书koc置换&买家秀",
    "视觉部",
    "浙江酷趣-小红书koc置换&淘宝买家秀",
  ]);
});

test("image source disambiguation treats Windows roots as machine-local", () => {
  const sources = UiState.disambiguateImageSourceLabels([
    { label: "共享素材", path: "Y:\\品牌\\A款\\优质图片" },
    { label: "共享素材", path: "Z:\\品牌\\B款\\优质图片" },
  ]);

  assert.deepEqual(sources.map((source) => source.label), [
    "共享素材-A款",
    "共享素材-B款",
  ]);
});

test("identical image source paths stay duplicated for backend rejection", () => {
  const sources = UiState.disambiguateImageSourceLabels([
    { label: "共享素材", path: "\\\\server\\share\\同一目录" },
    { label: "共享素材", path: "\\\\server\\share\\同一目录" },
  ]);

  assert.deepEqual(sources.map((source) => source.label), [
    "共享素材",
    "共享素材",
  ]);
});
