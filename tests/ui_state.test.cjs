const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

let UiState = {};
try {
  UiState = require(path.resolve(
    __dirname,
    "../src/upload_search_materials/interaction/static/ui-state.js",
  ));
} catch (_error) {
  // Assertions below report the missing production interface as test failures.
}

test("workbench application script remains valid JavaScript", () => {
  const source = fs.readFileSync(
    path.join(
      __dirname,
      "..",
      "src",
      "upload_search_materials",
      "interaction",
      "static",
      "app.js",
    ),
    "utf8",
  );
  assert.doesNotThrow(() => new vm.Script(source));
});

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

test("setup loading copy distinguishes new and saved configuration", () => {
  const newSession = {
    stages: { setup: { revision: 0, status: "draft" } },
  };
  const savedSession = {
    stages: { setup: { revision: 2, status: "draft" } },
  };

  assert.equal(UiState.hasSavedSetupConfiguration(newSession), false);
  assert.equal(UiState.hasSavedSetupConfiguration(savedSession), true);
  assert.equal(
    UiState.setupConfigurationLoadView("loading", {
      hasSavedConfiguration: false,
    }).message,
    "正在准备任务配置…",
  );
  assert.equal(
    UiState.setupConfigurationLoadView("loading", {
      hasSavedConfiguration: true,
    }).message,
    "正在加载已保存的配置…",
  );
  assert.equal(
    UiState.setupConfigurationLoadView("ready").message,
    "配置已准备好，请检查后提交",
  );
});

test("setup loading failure stays explicit and business friendly", () => {
  assert.equal(
    UiState.setupConfigurationLoadView("failed").message,
    "任务配置加载失败：系统暂时无法读取当前配置。请刷新页面后重试，已保存的数据不会丢失。",
  );
  assert.equal(
    UiState.setupConfigurationLoadView("failed", {
      userMessage: "工作台暂时无法连接。",
    }).message,
    "任务配置加载失败：工作台暂时无法连接。请刷新页面后重试，已保存的数据不会丢失。",
  );
});

test("blocked completeness is not presented as an empty inspection", () => {
  let state = UiState.createState("completeness");
  state = UiState.receiveStage(state, {
    stageId: "completeness",
    status: "blocked",
    result: {
      summary: "完整度巡检未能继续",
      data: {},
    },
  });

  assert.deepEqual(
    UiState.completenessEmptyView(state, UiState.resultView(state)),
    {
      label: "巡检结果暂时未能加载",
      hint: "系统已保留当前任务进度，请重新提交当前步骤；无需重新采集。",
      allowReinspect: false,
    },
  );
});

test("agent-owned setup diagnostics allow a preserved configuration to be resubmitted", () => {
  assert.equal(typeof UiState.technicalDiagnosticView, "function");
  let state = UiState.createState("setup");
  state = UiState.receiveStage(state, {
    stageId: "setup",
    status: "needs_user_input",
    result: {
      agent_diagnostic: {
        status: "open",
        user_action_required: false,
      },
    },
  });

  assert.deepEqual(UiState.technicalDiagnosticView(state), {
    active: true,
    statusLabel: "需要重新提交",
    submitLabel: "重新提交到工作台",
    message: "上次处理未完成，当前配置已保留。请检查配置后重新提交。",
  });

  state = UiState.receiveStage(state, {
    stageId: "setup",
    status: "needs_user_input",
    result: {
      agent_diagnostic: {
        status: "open",
        user_action_required: true,
      },
    },
  });
  assert.equal(UiState.technicalDiagnosticView(state).active, false);
});

test("an active copy version takes over from the previously selected version", () => {
  const view = UiState.copyRequestVersionView([
    { kind: "copy_draft", request_id: "new", status: "processing" },
    { kind: "copy_draft", request_id: "old", status: "completed" },
  ], "old");

  assert.equal(view.activeRequestId, "new");
  assert.equal(view.selectedRequestId, "new");
  assert.deepEqual(
    view.versions.map((item) => [item.version_number, item.status_label]),
    [[2, "生成中"], [1, "已完成"]],
  );
});

test("a new copy version never fills unfinished slots from an old version", () => {
  const assignments = [
    { slot_id: "slot-1", product_id: "p1" },
    { slot_id: "slot-2", product_id: "p2" },
  ];
  const merged = UiState.copyDraftsForVersion(
    assignments,
    [
      {
        slot_id: "slot-1",
        product_id: "p1",
        title: "旧标题一",
        description: "旧描述一",
        request_id: "old",
      },
      {
        slot_id: "slot-2",
        product_id: "p2",
        title: "旧标题二",
        description: "旧描述二",
        request_id: "old",
      },
    ],
    [
      {
        slot_id: "slot-1",
        remote_slot_position: 3,
        title: "新标题一",
        description: "新描述一",
      },
    ],
    "new",
    [
      { slot_id: "slot-1", outputs: [{ output_sha256: "sha-1" }] },
      { slot_id: "slot-2", outputs: [{ output_sha256: "sha-2" }] },
    ],
  );

  assert.equal(merged[0].title, "新标题一");
  assert.equal(merged[0].remote_slot_position, 3);
  assert.equal(merged[0].request_id, "new");
  assert.deepEqual(merged[0].output_sha256, ["sha-1"]);
  assert.equal(merged[1].title, "");
  assert.equal(merged[1].description, "");
  assert.equal(merged[1].source, "pending_qianniu_builtin_ai");
  assert.deepEqual(merged[1].output_sha256, ["sha-2"]);
});

test("a skipped copy slot keeps its manual-fill reason in the active version", () => {
  const merged = UiState.copyDraftsForVersion(
    [{ slot_id: "slot-1", product_id: "p1" }],
    [],
    [{
      slot_id: "slot-1",
      product_id: "p1",
      remote_slot_position: 5,
      title: "",
      description: "",
      evidence: ["自动获取未完成"],
      risks: ["请人工填写"],
      source: "manual_required",
      generation_status: "skipped",
      skip_reason_code: "QIANNIU_PRODUCT_IDENTITY_MISMATCH",
      skip_message: "千牛未找到该商品",
      attempt_count: 4,
      retry_count: 3,
    }],
    "request-new",
  );

  assert.equal(merged[0].generation_status, "skipped");
  assert.equal(merged[0].skip_message, "千牛未找到该商品");
  assert.equal(merged[0].retry_count, 3);
  assert.equal(merged[0].remote_slot_position, 5);
  assert.equal(merged[0].confirmed, false);
});

test("late AI copy results never overwrite manual fields in the same version", () => {
  const merged = UiState.copyDraftsForVersion(
    [{ slot_id: "slot-1", product_id: "p1" }],
    [{
      slot_id: "slot-1",
      product_id: "p1",
      title: "人工标题",
      description: "人工描述",
      source: "manual",
      manual_fields: { title: true, description: true },
      generation_status: "manual_completed",
      request_id: "request-1",
      output_sha256: ["sha-1"],
    }],
    [{
      slot_id: "slot-1",
      product_id: "p1",
      remote_slot_position: 6,
      title: "",
      description: "",
      source: "manual_required",
      generation_status: "skipped",
      skip_reason_code: "QIANNIU_COPY_POPUP_BLOCKED",
      skip_message: "千牛自动获取文案未完成",
    }],
    "request-1",
  );

  assert.equal(merged[0].title, "人工标题");
  assert.equal(merged[0].description, "人工描述");
  assert.equal(merged[0].source, "manual");
  assert.equal(merged[0].generation_status, "manual_completed");
  assert.equal(merged[0].skip_reason_code, "");
  assert.equal(merged[0].confirmed, true);
  assert.equal(merged[0].remote_slot_position, 6);
});

test("manual copy fields merge independently with a late AI response", () => {
  const merged = UiState.copyDraftsForVersion(
    [{ slot_id: "slot-1", product_id: "p1" }],
    [{
      slot_id: "slot-1",
      product_id: "p1",
      title: "人工标题",
      description: "",
      source: "manual",
      manual_fields: { title: true, description: false },
      request_id: "request-1",
    }],
    [{
      slot_id: "slot-1",
      product_id: "p1",
      title: "AI 标题",
      description: "AI 描述",
      generation_status: "generated",
    }],
    "request-1",
  );

  assert.equal(merged[0].title, "人工标题");
  assert.equal(merged[0].description, "AI 描述");
  assert.equal(merged[0].generation_status, "manual_completed");
  assert.equal(merged[0].skip_reason_code, "");
  assert.equal(merged[0].confirmed, true);
});

test("completeness products can be filtered by owner and product grade", () => {
  assert.equal(typeof UiState.filterCompletenessProducts, "function");
  const products = [
    { product_id: "1", product_title: "太阳镜", owner: "张三", product_grade: "S级", status: "needs_supplement" },
    { product_id: "2", product_title: "软软镜", owner: "李四", product_grade: "A级", status: "complete" },
    { product_id: "3", product_title: "稳稳镜", owner: "", product_grade: "", status: "needs_supplement" },
  ];

  assert.deepEqual(
    UiState.filterCompletenessProducts(products, { owner: "张三" })
      .map((product) => product.product_id),
    ["1"],
  );
  assert.deepEqual(
    UiState.filterCompletenessProducts(products, {
      owner: "__unassigned__",
      status: "needs_supplement",
    }).map((product) => product.product_id),
    ["3"],
  );
  assert.deepEqual(
    UiState.filterCompletenessProducts(products, { query: "李四" })
      .map((product) => product.product_id),
    ["2"],
  );
  assert.deepEqual(
    UiState.filterCompletenessProducts(products, {
      status: "selected",
      owner: "张三",
      productGrade: "S级",
      selectedProductIds: new Set(["1", "2"]),
    }).map((product) => product.product_id),
    ["1"],
  );
  assert.deepEqual(
    UiState.filterCompletenessProducts(products, { productGrade: "A级" })
      .map((product) => product.product_id),
    ["2"],
  );
  assert.deepEqual(
    UiState.filterCompletenessProducts(products, { productGrade: "__ungraded__" })
      .map((product) => product.product_id),
    ["3"],
  );
});

test("a live processing claim reports that the workbench is processing", () => {
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
  assert.equal(view.statusLabel, "工作台处理中");
  assert.equal(view.connectionLabel, "当前任务正在处理");
});

test("an online dispatcher keeps normal workflow connected without agent wait", () => {
  let state = UiState.createState("setup");
  state = UiState.receiveStage(state, {
    stageId: "setup",
    status: "ready_for_agent",
  });
  const view = UiState.connectionView(state, Date.now(), {
    workflowDispatch: { online: true, status: "queued" },
  });
  assert.equal(view.offline, false);
  assert.equal(view.statusLabel, "工作台处理中");
  assert.equal(view.connectionLabel, "当前任务正在处理");
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
  assert.equal(view.connectionLabel, "当前任务已连接");
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
  assert.equal(view.connectionLabel, "当前任务暂时无法连接");
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

test("completed image preflights merge into the latest formal selection", () => {
  let decisions = [{
    product_id: "product-a",
    asset_id: "asset-1",
    sha256: "sha-1",
    decision: "selected",
    selection_order: 1,
  }];
  decisions = UiState.mergeSelectedAssetDecision(decisions, {
    product_id: "product-a",
    asset_id: "asset-2",
    sha256: "sha-2",
    feasible_ratios: ["3:4"],
  });
  decisions = UiState.mergeSelectedAssetDecision(decisions, {
    product_id: "product-a",
    asset_id: "asset-3",
    sha256: "sha-3",
    feasible_ratios: ["1:1"],
  });

  assert.deepEqual(
    decisions.map((item) => [item.asset_id, item.selection_order]),
    [["asset-1", 1], ["asset-2", 2], ["asset-3", 3]],
  );
  decisions = UiState.mergeSelectedAssetDecision(
    decisions,
    { product_id: "product-a", asset_id: "asset-2" },
    false,
  );
  assert.deepEqual(
    decisions.map((item) => item.asset_id),
    ["asset-1", "asset-3"],
  );
});

test("same-task duplicate images are reserved by the first selected product", () => {
  const decisions = [
    {
      product_id: "product-a",
      asset_id: "asset-a",
      sha256: "shared-sha",
      decision: "selected",
    },
    {
      product_id: "product-c",
      asset_id: "asset-c",
      sha256: "ignored-sha",
      decision: "rejected",
    },
  ];

  assert.equal(
    UiState.assetSelectedByOtherProduct(decisions, "product-b", "shared-sha"),
    true,
  );
  assert.equal(
    UiState.assetSelectedByOtherProduct(decisions, "product-a", "shared-sha"),
    false,
  );
  assert.equal(
    UiState.assetSelectedByOtherProduct(decisions, "product-b", "ignored-sha"),
    false,
  );
});

test("global asset selection targets three images for every missing slot", () => {
  assert.equal(UiState.globalAssetSelectionTarget(3, 20), 9);
  assert.equal(UiState.globalAssetSelectionTarget(2, 6), 6);
  assert.equal(UiState.globalAssetSelectionTarget(3, 7), 7);
  assert.equal(UiState.globalAssetSelectionTarget(2, 2), 2);
  assert.equal(UiState.globalAssetSelectionTarget(0, 20), 0);
});

test("global asset selection keeps 3:4 first and supplements with 1:1", () => {
  const both = { asset_id: "both", feasible_ratios: ["3:4", "1:1"] };
  const square = { asset_id: "square", feasible_ratios: ["1:1"] };
  const portrait = { asset_id: "portrait", feasible_ratios: ["3:4"] };
  const unsupported = { asset_id: "unsupported", feasible_ratios: [] };

  assert.deepEqual(
    UiState.prioritizeGlobalAssetCandidates([
      square,
      both,
      unsupported,
      portrait,
    ]).map((item) => item.asset_id),
    ["both", "portrait", "square", "unsupported"],
  );
  assert.equal(
    UiState.prioritizeGlobalAssetCandidates([both, square])
      .filter((item) => item.asset_id === "both").length,
    1,
  );
});

test("global asset selection recognizes all ratios accepted by existing checks", () => {
  assert.deepEqual(UiState.candidateFeasibleRatios({
    preflight: {
      resolution_checks: {
        "3:4": { minimum_status: "meets_minimum" },
        "1:1": { minimum_status: "below_minimum" },
      },
    },
  }), ["3:4"]);
  assert.deepEqual(UiState.candidateFeasibleRatios({
    feasible_ratios: ["1:1"],
    ratio_options: { "3:4": { feasible: true } },
  }), ["1:1"]);
  assert.deepEqual(UiState.candidateFeasibleRatios({
    ratio_options: {
      "3:4": { feasible: true },
      "1:1": { feasible: true },
    },
  }), ["3:4", "1:1"]);
});

test("global asset random order is stable for the same task and product", () => {
  const candidates = Array.from({ length: 12 }, (_, index) => ({
    asset_id: `asset-${index}`,
    sha256: `sha-${index}`,
  }));
  const first = UiState.stableGlobalAssetOrder(
    candidates,
    "session-a|gallery-a",
    "product-a",
  ).map((item) => item.asset_id);
  const repeated = UiState.stableGlobalAssetOrder(
    [...candidates].reverse(),
    "session-a|gallery-a",
    "product-a",
  ).map((item) => item.asset_id);

  assert.deepEqual(repeated, first);
  assert.deepEqual([...first].sort(), candidates.map((item) => item.asset_id).sort());
});

test("image-selection hydration waits for edits, saves, or running preflights", () => {
  const idle = {
    stageId: "asset_matching",
    workflowStep: "image_selection",
  };
  assert.equal(UiState.shouldDeferEditableStageHydration(idle), false);
  assert.equal(UiState.shouldDeferEditableStageHydration({
    ...idle,
    dirty: true,
  }), true);
  assert.equal(UiState.shouldDeferEditableStageHydration({
    ...idle,
    persistenceInFlight: true,
  }), true);
  assert.equal(UiState.shouldDeferEditableStageHydration({
    ...idle,
    pendingPreflightCount: 2,
  }), true);
  assert.equal(UiState.shouldDeferEditableStageHydration({
    ...idle,
    globalAssetSelectionInFlight: true,
  }), true);
  assert.equal(UiState.shouldDeferEditableStageHydration({
    stageId: "completeness",
    workflowStep: "image_selection",
    dirty: true,
  }), false);
});

test("copy generation keeps the current subpage during authoritative polling", () => {
  const idle = {
    stageId: "slots_copy",
    workflowStep: "copy",
  };
  assert.equal(UiState.shouldDeferEditableStageHydration(idle), false);
  assert.equal(UiState.shouldDeferEditableStageHydration({
    ...idle,
    copyRequestInFlight: true,
  }), true);
  assert.equal(UiState.shouldDeferEditableStageHydration({
    ...idle,
    dirty: true,
  }), true);
  assert.equal(UiState.shouldDeferEditableStageHydration({
    ...idle,
    persistenceInFlight: true,
  }), true);
  assert.equal(UiState.shouldDeferEditableStageHydration({
    stageId: "slots_copy",
    workflowStep: "process",
    copyRequestInFlight: true,
  }), false);
  assert.equal(UiState.shouldDeferEditableStageHydration({
    stageId: "slots_copy",
    workflowStep: "process",
    cropPreflightInFlight: true,
  }), true);
});

test("product navigator selects the section crossing the viewport activation line", () => {
  assert.equal(UiState.activeProductTargetIndex([], 200), -1);
  assert.equal(UiState.activeProductTargetIndex([
    { top: 260, bottom: 660 },
    { top: 680, bottom: 1080 },
  ], 200), 0);
  assert.equal(UiState.activeProductTargetIndex([
    { top: -420, bottom: 120 },
    { top: 140, bottom: 740 },
    { top: 760, bottom: 1260 },
  ], 200), 1);
  assert.equal(UiState.activeProductTargetIndex([
    { top: -900, bottom: -300 },
    { top: -280, bottom: -20 },
    { top: 320, bottom: 900 },
  ], 200), 1);
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

test("products with full material slots cannot enter asset matching", () => {
  assert.equal(
    UiState.completenessProductSelectable({
      product_id: "full",
      status: "complete",
      promotion: { current_count: 3, target_slots: 3, missing_count: 0 },
    }),
    false,
  );
  assert.equal(
    UiState.completenessProductSelectable({
      product_id: "open",
      status: "needs_supplement",
      promotion: { current_count: 2, target_slots: 3, missing_count: 1 },
    }),
    true,
  );
  assert.equal(
    UiState.completenessProductHasOpenSlots({
      status: "needs_supplement",
      promotion: { current_count: 3, target_slots: 3 },
    }),
    false,
  );
});

test("gallery reload is required whenever the active folder set changes", () => {
  const prepared = [
    { product_id: "P1", folder_id: "F1" },
    { product_id: "P1", folder_id: "F2" },
  ];
  assert.equal(UiState.galleryNeedsReload(prepared, [
    { product_id: "P1", folder_id: "F2", decision: "confirmed" },
    { product_id: "P1", folder_id: "F1", decision: "confirmed" },
    { product_id: "P1", folder_id: "F3", decision: "rejected" },
  ]), false);
  assert.equal(UiState.galleryNeedsReload(prepared, [
    { product_id: "P1", folder_id: "F1", decision: "confirmed" },
    { product_id: "P1", folder_id: "F2", decision: "rejected" },
  ]), true);
  assert.equal(UiState.galleryNeedsReload(prepared, [
    { product_id: "P1", folder_id: "F1", decision: "confirmed" },
    { product_id: "P1", folder_id: "F2", decision: "confirmed" },
    { product_id: "P1", folder_id: "F3", decision: "confirmed" },
  ]), true);
  assert.equal(UiState.galleryNeedsReload([
    ...prepared,
    { product_id: "P2", folder_id: "F4" },
  ], [
    { product_id: "P1", folder_id: "F1", decision: "confirmed" },
    { product_id: "P1", folder_id: "F2", decision: "confirmed" },
    { product_id: "P2", folder_id: "F4", decision: "rejected" },
  ], ["P2"]), false);
  assert.equal(UiState.galleryNeedsReload(null, []), true);
});

test("persisted preflight cache preserves click intent without re-executing", async () => {
  let executions = 0;
  const scheduler = UiState.createSelectionPreflightScheduler({
    execute: async () => {
      executions += 1;
      return { status: "passed" };
    },
  });
  const candidate = { asset_id: "asset-a", width: 3024, height: 4032 };
  const cached = { status: "passed", feasible_ratios: ["3:4", "1:1"] };

  assert.deepEqual(await scheduler.selectCached(candidate, cached), cached);
  assert.equal(executions, 0);
  assert.equal(scheduler.get("asset-a").state, "completed");
  assert.equal(scheduler.get("asset-a").desiredSelected, true);

  scheduler.cancel("asset-a");
  assert.equal(scheduler.get("asset-a").desiredSelected, false);
  assert.deepEqual(await scheduler.selectCached(candidate, cached), cached);
  assert.equal(executions, 0);
  assert.equal(scheduler.get("asset-a").desiredSelected, true);
});

test("workbench reuses persisted preflight through the intent-aware scheduler", () => {
  const source = fs.readFileSync(
    path.join(
      __dirname,
      "..",
      "src",
      "upload_search_materials",
      "interaction",
      "static",
      "app.js",
    ),
    "utf8",
  );
  assert.match(
    source,
    /return selectionPreflightScheduler\.selectCached\(candidate, existing\);/,
  );
});

test("forgotten completed preflight is executed again instead of reused", async () => {
  let executions = 0;
  const scheduler = UiState.createSelectionPreflightScheduler({
    execute: async () => {
      executions += 1;
      return { status: "passed", execution: executions };
    },
  });

  assert.equal((await scheduler.select({ asset_id: "asset-a" })).execution, 1);
  scheduler.forget("asset-a");
  assert.equal(scheduler.get("asset-a"), null);
  assert.equal((await scheduler.select({ asset_id: "asset-a" })).execution, 2);
});

test("reset detaches running and queued preflights from the next gallery", async () => {
  let releaseFirst;
  let executions = 0;
  const scheduler = UiState.createSelectionPreflightScheduler({
    maxConcurrent: 1,
    maxWeight: 1,
    execute: (candidate) => new Promise((resolve) => {
      executions += 1;
      if (candidate.asset_id === "first" && executions === 1) {
        releaseFirst = resolve;
      } else {
        resolve({ status: "passed", execution: executions });
      }
    }),
  });

  const first = scheduler.select({ asset_id: "first", width: 1, height: 1 });
  const second = scheduler.select({ asset_id: "second", width: 1, height: 1 });
  await new Promise((resolve) => setImmediate(resolve));
  scheduler.reset();
  assert.equal(scheduler.get("first"), null);
  assert.equal(scheduler.get("second"), null);
  assert.deepEqual(await second, { status: "cancelled", cancelled: true });
  releaseFirst({ status: "passed", execution: 1 });
  assert.deepEqual(await first, { status: "cancelled", cancelled: true });

  const refreshed = await scheduler.select({ asset_id: "first", width: 1, height: 1 });
  assert.equal(refreshed.status, "passed");
  assert.equal(executions, 2);
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

test("editable review stages retry hydration when their durable result is missing", () => {
  assert.equal(
    UiState.stageNeedsResultHydration(
      "completeness",
      "needs_user_input",
      null,
      true,
    ),
    true,
  );
  assert.equal(
    UiState.stageNeedsResultHydration(
      "asset_matching",
      "blocked",
      null,
      true,
    ),
    true,
  );
  assert.equal(
    UiState.stageNeedsResultHydration(
      "completeness",
      "needs_user_input",
      { data: { products: [] } },
      true,
    ),
    false,
  );
  assert.equal(
    UiState.stageNeedsResultHydration(
      "asset_matching",
      "needs_user_input",
      { revision: 4, data: { folder_candidates: [] } },
      true,
      5,
    ),
    true,
  );
  assert.equal(
    UiState.stageNeedsResultHydration(
      "asset_matching",
      "needs_user_input",
      { revision: 5, data: { folder_candidates: [] } },
      true,
      5,
    ),
    false,
  );
  assert.equal(
    UiState.stageNeedsResultHydration("setup", "draft", null, true),
    false,
  );
  assert.equal(
    UiState.stageNeedsResultHydration("setup", "draft", null, false),
    true,
  );
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

test("product title resolution prefers task product data over sparse candidates", () => {
  const data = {
    requirements: [{ product_id: "P1", product_title: "任务商品名称" }],
    folder_candidates: [{ product_id: "P1", product_title: "" }],
  };

  assert.equal(
    UiState.resolveProductTitle(data, "P1", "候选回退名称"),
    "任务商品名称",
  );
  assert.equal(
    UiState.resolveProductTitle({}, "P2", "回退名称"),
    "回退名称",
  );
});

test("folder review summary counts only folders that will be rendered", () => {
  const view = UiState.folderReviewPresentation({
    folder_candidates: [
      { product_id: "P1", folder_id: "F1", match_type: "exact_product_name" },
      { product_id: "P1", folder_id: "F2", match_type: "confirmed_alias" },
      { product_id: "P2", folder_id: "F3", match_type: "exact_sku" },
    ],
  }, ["P2"]);

  assert.equal(view.productCount, 1);
  assert.equal(view.candidateCount, 1);
  assert.equal(view.summary, "已显示 1 个商品的 1 个候选文件夹");
  assert.deepEqual(view.candidates.map((candidate) => candidate.folder_id), ["F1"]);
});
