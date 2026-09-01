(function attachInteractionUiState(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.InteractionUiState = api;
})(typeof globalThis === "object" ? globalThis : this, function createInteractionUiStateApi() {
  "use strict";

  const userActionStatuses = new Set(["needs_user_input", "blocked"]);
  const statusLabels = {
    draft: "编辑中",
    ready_for_agent: "已提交，等待工作台处理",
    processing: "工作台处理中",
    needs_user_input: "补充后重新提交",
    blocked: "补充后重新提交",
    completed: "已完成",
  };

  function setupConfigurationLoadView(
    phase,
    { hasSavedConfiguration = false, userMessage = "" } = {},
  ) {
    const normalizedPhase = String(phase || "loading");
    if (normalizedPhase === "ready") {
      return { message: "配置已准备好，请检查后提交" };
    }
    if (normalizedPhase === "failed") {
      const detail = String(userMessage || "")
        .trim()
        .replace(/[。！？!?；;，,：:]+$/u, "");
      return {
        message: detail
          ? `任务配置加载失败：${detail}。请刷新页面后重试，已保存的数据不会丢失。`
          : "任务配置加载失败：系统暂时无法读取当前配置。请刷新页面后重试，已保存的数据不会丢失。",
      };
    }
    return {
      message: hasSavedConfiguration
        ? "正在加载已保存的配置…"
        : "正在准备任务配置…",
    };
  }

  function hasSavedSetupConfiguration(session) {
    const setup = session?.stages?.setup;
    if (!setup || typeof setup !== "object") return false;
    return Number(setup.revision || 0) > 0
      || !["", "draft"].includes(String(setup.status || "draft"));
  }

  function createState(stageId) {
    return {
      stageId,
      serverStatus: "draft",
      dirty: false,
      result: null,
      submission: null,
      lastAgentHeartbeat: null,
      recoveryInstruction: null,
    };
  }

  function receiveStage(state, update) {
    if (update.stageId !== state.stageId) return state;
    return {
      ...state,
      serverStatus: update.status,
      dirty: false,
      result: Object.hasOwn(update, "result") ? update.result : state.result,
      submission: Object.hasOwn(update, "submission") ? update.submission : state.submission,
      lastAgentHeartbeat: Object.hasOwn(update, "lastAgentHeartbeat")
        ? update.lastAgentHeartbeat
        : state.lastAgentHeartbeat,
    };
  }

  function markDirty(state) {
    return { ...state, dirty: true };
  }

  function receiveStatus(state, status, lastAgentHeartbeat) {
    return {
      ...state,
      serverStatus: status,
      dirty: status === state.serverStatus ? state.dirty : false,
      lastAgentHeartbeat,
    };
  }

  function recoveryView(state) {
    return { visible: false, submitEnabled: false };
  }

  function resultView(state) {
    if (!state.result || typeof state.result !== "object") {
      return { mode: "empty", label: "尚未扫描" };
    }
    return { mode: "result", result: state.result };
  }

  function completenessEmptyView(state, view) {
    if (String(state?.serverStatus || "") === "blocked") {
      return {
        label: "巡检结果暂时未能加载",
        hint: "系统已保留当前任务进度，请重新提交当前步骤；无需重新采集。",
        allowReinspect: false,
      };
    }
    return {
      label: view?.mode === "empty" ? String(view.label || "尚未扫描") : "暂无巡检商品",
      hint: "可重新巡检“搜推高价值”，系统会重新生成完整度结果。",
      allowReinspect: true,
    };
  }

  function technicalDiagnosticView(state) {
    const diagnostic = state?.result?.agent_diagnostic;
    const active = ["needs_user_input", "blocked"].includes(
      String(state?.serverStatus || ""),
    )
      && diagnostic?.status === "open"
      && diagnostic.user_action_required !== true;
    return {
      active,
      statusLabel: active ? "需要重新提交" : "",
      submitLabel: active ? "重新提交到工作台" : "",
      message: active
        ? "上次处理未完成，当前配置已保留。请检查配置后重新提交。"
        : "",
    };
  }

  function copyRequestVersionView(requests, preferredRequestId = "") {
    const versions = (Array.isArray(requests) ? requests : [])
      .filter((item) => item?.kind === "copy_draft")
      .map((item, index, items) => {
        const status = String(item?.status || "");
        const statusLabel = {
          pending_agent: "等待生成",
          processing: "生成中",
          completed: "已完成",
          failed: "未完成",
          cancelled: "已停止",
          superseded: "已停止",
        }[status] || "状态未知";
        return {
          ...item,
          request_id: String(item?.request_id || ""),
          version_number: items.length - index,
          status_label: statusLabel,
        };
      });
    const activeStatuses = new Set(["pending_agent", "processing"]);
    const preferredActive = versions.find((item) => (
      item.request_id === String(preferredRequestId || "")
      && activeStatuses.has(String(item.status || ""))
    ));
    const active = preferredActive || versions.find(
      (item) => activeStatuses.has(String(item.status || "")),
    ) || null;
    const preferred = versions.find(
      (item) => item.request_id === String(preferredRequestId || ""),
    ) || null;
    return {
      versions,
      activeRequestId: active?.request_id || "",
      selectedRequestId: active?.request_id
        || preferred?.request_id
        || versions[0]?.request_id
        || "",
    };
  }

  function copyDraftsForVersion(
    assignments,
    existingItems,
    drafts,
    requestId,
    processedSlots = [],
  ) {
    const versionId = String(requestId || "");
    const draftsBySlot = new Map(
      (Array.isArray(drafts) ? drafts : [])
        .map((draft) => [String(draft?.slot_id || ""), draft]),
    );
    const existingBySlot = new Map(
      (Array.isArray(existingItems) ? existingItems : [])
        .filter((item) => item?.slot_id)
        .map((item) => [String(item.slot_id), item]),
    );
    const processedBySlot = new Map(
      (Array.isArray(processedSlots) ? processedSlots : [])
        .filter((item) => item?.slot_id)
        .map((item) => [String(item.slot_id), item]),
    );
    return (Array.isArray(assignments) ? assignments : []).map((assignment) => {
      const slotId = String(assignment?.slot_id || "");
      const draft = draftsBySlot.get(slotId);
      const existing = existingBySlot.get(slotId) || {};
      const sameVersion = String(existing.request_id || "") === versionId;
      const outputSha256 = sameVersion && Array.isArray(existing.output_sha256)
        ? existing.output_sha256
        : (processedBySlot.get(slotId)?.outputs || [])
          .map((output) => String(output?.output_sha256 || ""))
          .filter(Boolean);
      const title = draft
        ? String(draft.title || "")
        : sameVersion
          ? String(existing.title || "")
          : "";
      const description = draft
        ? String(draft.description || "")
        : sameVersion
          ? String(existing.description || "")
          : "";
      const generationStatus = draft
        ? String(draft.generation_status || "generated")
        : sameVersion
          ? String(existing.generation_status || "pending")
          : "pending";
      return {
        slot_id: slotId,
        product_id: String(assignment?.product_id || ""),
        title,
        description,
        evidence: draft
          ? (Array.isArray(draft.evidence) ? draft.evidence : [])
          : sameVersion && Array.isArray(existing.evidence)
            ? existing.evidence
            : [],
        risks: draft
          ? (Array.isArray(draft.risks) ? draft.risks : [])
          : sameVersion && Array.isArray(existing.risks)
            ? existing.risks
            : [],
        confirmed: Boolean(title.trim() && description.trim()),
        source: draft
          ? String(draft.source || "qianniu_builtin_ai")
          : sameVersion
            ? String(existing.source || "pending_qianniu_builtin_ai")
            : "pending_qianniu_builtin_ai",
        generation_status: generationStatus,
        skip_reason_code: draft
          ? String(draft.skip_reason_code || "")
          : sameVersion
            ? String(existing.skip_reason_code || "")
            : "",
        skip_message: draft
          ? String(draft.skip_message || "")
          : sameVersion
            ? String(existing.skip_message || "")
            : "",
        failure_category: draft
          ? String(draft.failure_category || "")
          : sameVersion
            ? String(existing.failure_category || "")
            : "",
        attempt_count: draft
          ? Number(draft.attempt_count || 0)
          : sameVersion
            ? Number(existing.attempt_count || 0)
            : 0,
        retry_count: draft
          ? Number(draft.retry_count || 0)
          : sameVersion
            ? Number(existing.retry_count || 0)
            : 0,
        repair_pass_count: draft
          ? Number(draft.repair_pass_count || 0)
          : sameVersion
            ? Number(existing.repair_pass_count || 0)
            : 0,
        final_retry_exhausted: draft
          ? Boolean(draft.final_retry_exhausted)
          : sameVersion
            ? Boolean(existing.final_retry_exhausted)
            : false,
        request_id: versionId,
        output_sha256: outputSha256,
      };
    });
  }

  function submissionView(state) {
    const createdAt = state.submission?.created_at;
    return { createdAt: typeof createdAt === "string" ? createdAt : null };
  }

  function leaseIsLive(lease, now, expiresAtField, remainingSecondsField) {
    if (!lease || typeof lease !== "object" || lease.expired === true) return false;
    const expiresAt = Date.parse(lease[expiresAtField] || "");
    if (Number.isFinite(expiresAt)) return now < expiresAt;
    const remainingSeconds = Number(lease[remainingSecondsField]);
    if (Number.isFinite(remainingSeconds)) return remainingSeconds > 0;
    return lease.expired === false;
  }

  function connectionView(state, now, presence = {}) {
    const workflowDispatch = presence.workflowDispatch || presence.workflow_dispatch;
    const waitIsLive = leaseIsLive(
      presence.agentWait || presence.agent_wait,
      now,
      "expires_at",
      "remaining_seconds",
    );
    const processingIsLive = leaseIsLive(
      presence.processingClaim || presence.processing_claim,
      now,
      "lease_expires_at",
      "remaining_seconds",
    );
    if (processingIsLive) {
      return {
        offline: false,
        statusLabel: statusLabels.processing,
        connectionLabel: "当前任务正在处理",
      };
    }
    if (workflowDispatch?.online === true) {
      const dispatching = ["queued", "running"].includes(
        String(workflowDispatch.status || ""),
      );
      return {
        offline: false,
        statusLabel: dispatching
          ? statusLabels.processing
          : statusLabels[state.serverStatus] || statusLabels.draft,
        connectionLabel: dispatching
          ? "当前任务正在处理"
          : "当前任务已就绪",
      };
    }
    if (waitIsLive) {
      return {
        offline: false,
        statusLabel: statusLabels[state.serverStatus] || statusLabels.draft,
        connectionLabel: "当前任务已连接",
      };
    }
    return {
      offline: true,
      statusLabel: statusLabels[state.serverStatus] || statusLabels.draft,
      connectionLabel: "当前任务暂时无法连接",
    };
  }

  function selectionPreflightWeight(candidate) {
    const width = Math.max(0, Number(candidate?.width || candidate?.source_inspection?.width || 0));
    const height = Math.max(0, Number(candidate?.height || candidate?.source_inspection?.height || 0));
    const pixels = width * height;
    const format = String(
      candidate?.format || candidate?.source_inspection?.format || "",
    ).toUpperCase();
    if (pixels >= 60_000_000) return 4;
    if (pixels > 30_000_000) return 3;
    if (pixels > 12_000_000 || ["PNG", "GIF", "HEIC", "HEIF"].includes(format)) {
      return 2;
    }
    return 1;
  }

  function mergeSelectedAssetDecision(items, decision, selected = true) {
    const current = (Array.isArray(items) ? items : []).filter(
      (item) => item?.decision === "selected" && item?.asset_id,
    );
    const productId = String(decision?.product_id || "");
    const assetId = String(decision?.asset_id || "");
    if (!productId || !assetId) return current;
    const targetIndex = current.findIndex(
      (item) => String(item.product_id || "") === productId
        && String(item.asset_id || "") === assetId,
    );
    if (!selected) {
      return current.filter((_item, index) => index !== targetIndex);
    }
    const previous = targetIndex >= 0 ? current[targetIndex] : null;
    const previousOrder = Number(previous?.selection_order || 0);
    const maximumProductOrder = current.reduce((maximum, item) => (
      String(item.product_id || "") === productId
        ? Math.max(maximum, Number(item.selection_order || 0))
        : maximum
    ), 0);
    const merged = {
      ...(previous || {}),
      ...(decision || {}),
      product_id: productId,
      asset_id: assetId,
      decision: "selected",
      selection_order: previousOrder > 0
        ? previousOrder
        : maximumProductOrder + 1,
    };
    if (targetIndex < 0) return [...current, merged];
    return current.map((item, index) => (index === targetIndex ? merged : item));
  }

  function assetSelectedByOtherProduct(items, productId, sha256) {
    const currentProductId = String(productId || "");
    const assetSha256 = String(sha256 || "");
    if (!currentProductId || !assetSha256) return false;
    return (Array.isArray(items) ? items : []).some(
      (item) => item?.decision === "selected"
        && String(item.product_id || "") !== currentProductId
        && String(item.sha256 || item.source_sha256 || "") === assetSha256,
    );
  }

  function candidateFeasibleRatios(candidate) {
    if (Array.isArray(candidate?.feasible_ratios)) {
      return ["3:4", "1:1"].filter(
        (ratio) => candidate.feasible_ratios
          .map((value) => String(value || ""))
          .includes(ratio),
      );
    }
    const supported = new Set();
    const ratioOptions = candidate?.ratio_options || {};
    const checks = candidate?.resolution_checks
      || candidate?.preflight?.resolution_checks
      || {};
    ["3:4", "1:1"].forEach((ratio) => {
      if (
        ratioOptions?.[ratio]?.feasible === true
        || checks?.[ratio]?.minimum_status === "meets_minimum"
      ) supported.add(ratio);
    });
    return ["3:4", "1:1"].filter((ratio) => supported.has(ratio));
  }

  function globalAssetSelectionTarget(missingMaterials, availableCount) {
    const missing = Math.max(0, Math.floor(Number(missingMaterials) || 0));
    const available = Math.max(0, Math.floor(Number(availableCount) || 0));
    return Math.min(missing * 3, available);
  }

  function prioritizeGlobalAssetCandidates(candidates) {
    const values = Array.isArray(candidates) ? candidates : [];
    const primary = [];
    const supplement = [];
    const unsupported = [];
    values.forEach((candidate) => {
      const ratios = candidateFeasibleRatios(candidate);
      if (ratios.includes("3:4")) {
        primary.push(candidate);
      } else if (ratios.includes("1:1")) {
        supplement.push(candidate);
      } else {
        unsupported.push(candidate);
      }
    });
    return [...primary, ...supplement, ...unsupported];
  }

  function stableTextHash(value) {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function stableGlobalAssetOrder(candidates, seed, productId) {
    const prefix = `${String(seed || "")}\u0000${String(productId || "")}\u0000`;
    return [...(Array.isArray(candidates) ? candidates : [])].sort((left, right) => {
      const leftIdentity = `${String(left?.asset_id || "")}\u0000${String(left?.sha256 || "")}`;
      const rightIdentity = `${String(right?.asset_id || "")}\u0000${String(right?.sha256 || "")}`;
      const scoreDifference = stableTextHash(prefix + leftIdentity)
        - stableTextHash(prefix + rightIdentity);
      if (scoreDifference) return scoreDifference;
      return leftIdentity.localeCompare(rightIdentity, "zh-CN");
    });
  }

  function shouldDeferEditableStageHydration({
    stageId,
    workflowStep,
    dirty = false,
    persistenceInFlight = false,
    pendingPreflightCount = 0,
    copyRequestInFlight = false,
  } = {}) {
    const imageSelectionBusy = stageId === "asset_matching"
      && workflowStep === "image_selection"
      && (
        dirty
        || persistenceInFlight
        || Number(pendingPreflightCount || 0) > 0
      );
    const copyGenerationBusy = stageId === "slots_copy"
      && workflowStep === "copy"
      && (
        dirty
        || persistenceInFlight
        || copyRequestInFlight
      );
    return imageSelectionBusy || copyGenerationBusy;
  }

  function activeProductTargetIndex(rects, activationLine = 0) {
    const sections = (Array.isArray(rects) ? rects : [])
      .map((rect, index) => ({
        index,
        top: Number(rect?.top),
        bottom: Number(rect?.bottom),
      }))
      .filter((rect) => Number.isFinite(rect.top) && Number.isFinite(rect.bottom));
    if (!sections.length) return -1;
    const line = Number.isFinite(Number(activationLine))
      ? Number(activationLine)
      : 0;
    const containing = sections.find(
      (rect) => rect.top <= line && rect.bottom > line,
    );
    if (containing) return containing.index;
    const above = sections.filter((rect) => rect.top <= line);
    if (above.length) return above[above.length - 1].index;
    return sections[0].index;
  }

  function createSelectionPreflightScheduler({
    execute,
    onChange = () => {},
    maxConcurrent = 6,
    maxWeight = 8,
  } = {}) {
    if (typeof execute !== "function") throw new TypeError("execute is required");
    const jobs = new Map();
    const queue = [];
    let activeCount = 0;
    let activeWeight = 0;

    const snapshot = (job) => job ? {
      assetId: job.assetId,
      state: job.state,
      desiredSelected: job.desiredSelected,
      intentVersion: job.intentVersion,
      weight: job.weight,
      result: job.result,
      error: job.error,
    } : null;

    const notify = (job) => onChange(snapshot(job));

    function drain() {
      while (activeCount < maxConcurrent && queue.length) {
        const index = queue.findIndex(
          (job) => activeWeight + job.weight <= maxWeight,
        );
        if (index < 0) return;
        const [job] = queue.splice(index, 1);
        if (job.state !== "queued") continue;
        job.state = "running";
        activeCount += 1;
        activeWeight += job.weight;
        notify(job);
        Promise.resolve()
          .then(() => execute(job.candidate))
          .then((result) => {
            if (job.forgotten) {
              job.state = "cancelled";
              job.resolve({ status: "cancelled", cancelled: true });
              return;
            }
            job.state = "completed";
            job.result = result;
            job.resolve(result);
          })
          .catch((error) => {
            if (job.forgotten) {
              job.state = "cancelled";
              job.resolve({ status: "cancelled", cancelled: true });
              return;
            }
            job.state = "failed";
            job.error = error;
            job.reject(error);
          })
          .finally(() => {
            activeCount -= 1;
            activeWeight -= job.weight;
            if (!job.forgotten) notify(job);
            drain();
          });
      }
    }

    function createJob(candidate) {
      const assetId = String(candidate?.asset_id || "");
      let resolve;
      let reject;
      const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
      });
      const job = {
        assetId,
        candidate,
        state: "queued",
        desiredSelected: true,
        intentVersion: 1,
        weight: Math.min(maxWeight, selectionPreflightWeight(candidate)),
        result: null,
        error: null,
        forgotten: false,
        promise,
        resolve,
        reject,
      };
      jobs.set(assetId, job);
      queue.push(job);
      notify(job);
      drain();
      return job;
    }

    function select(candidate) {
      const assetId = String(candidate?.asset_id || "");
      if (!assetId) return Promise.reject(new Error("候选图片缺少稳定标识"));
      let job = jobs.get(assetId);
      if (!job || ["cancelled", "failed"].includes(job.state)) {
        job = createJob(candidate);
        return job.promise;
      }
      job.desiredSelected = true;
      job.intentVersion += 1;
      notify(job);
      if (job.state === "completed") return Promise.resolve(job.result);
      return job.promise;
    }

    function selectCached(candidate, result) {
      const assetId = String(candidate?.asset_id || "");
      if (!assetId) return Promise.reject(new Error("候选图片缺少稳定标识"));
      let job = jobs.get(assetId);
      if (!job || job.state !== "completed") {
        if (job) forget(assetId);
        job = {
          assetId,
          candidate,
          state: "completed",
          desiredSelected: true,
          intentVersion: 1,
          weight: Math.min(maxWeight, selectionPreflightWeight(candidate)),
          result,
          error: null,
          forgotten: false,
          promise: Promise.resolve(result),
          resolve: () => {},
          reject: () => {},
        };
        jobs.set(assetId, job);
      } else {
        job.candidate = candidate;
        job.desiredSelected = true;
        job.intentVersion += 1;
        job.result = result;
        job.error = null;
        job.forgotten = false;
        job.promise = Promise.resolve(result);
      }
      notify(job);
      return Promise.resolve(result);
    }

    function cancel(assetId) {
      const job = jobs.get(String(assetId || ""));
      if (!job) return null;
      job.desiredSelected = false;
      job.intentVersion += 1;
      if (job.state === "queued") {
        const index = queue.indexOf(job);
        if (index >= 0) queue.splice(index, 1);
        job.state = "cancelled";
        job.resolve({ status: "cancelled", cancelled: true });
      }
      notify(job);
      drain();
      return snapshot(job);
    }

    function forget(assetId) {
      const job = jobs.get(String(assetId || ""));
      if (!job) return null;
      jobs.delete(job.assetId);
      job.desiredSelected = false;
      job.intentVersion += 1;
      job.forgotten = true;
      if (job.state === "queued") {
        const index = queue.indexOf(job);
        if (index >= 0) queue.splice(index, 1);
        job.state = "cancelled";
        job.resolve({ status: "cancelled", cancelled: true });
      }
      drain();
      return snapshot(job);
    }

    function reset() {
      [...jobs.keys()].forEach((assetId) => forget(assetId));
    }

    function desiredPendingCount() {
      return [...jobs.values()].filter(
        (job) => job.desiredSelected && ["queued", "running"].includes(job.state),
      ).length;
    }

    return {
      cancel,
      desiredPendingCount,
      forget,
      get: (assetId) => snapshot(jobs.get(String(assetId || ""))),
      reset,
      select,
      selectCached,
    };
  }

  function galleryNeedsReload(
    preparedFolderKeys,
    decisions,
    ignoredProductIds = [],
  ) {
    if (!Array.isArray(preparedFolderKeys)) return true;
    const ignoredProducts = new Set(
      (Array.isArray(ignoredProductIds) ? ignoredProductIds : [])
        .map((value) => String(value || ""))
        .filter(Boolean),
    );
    const keyFor = (item) => (
      `${String(item?.product_id || "")}\u0000${String(item?.folder_id || "")}`
    );
    const prepared = new Set(
      preparedFolderKeys
        .filter(
          (item) => item?.product_id
            && item?.folder_id
            && !ignoredProducts.has(String(item.product_id)),
        )
        .map(keyFor),
    );
    const confirmed = new Set(
      (Array.isArray(decisions) ? decisions : [])
        .filter(
          (item) => item?.decision === "confirmed"
            && item?.product_id
            && item?.folder_id
            && !ignoredProducts.has(String(item.product_id)),
        )
        .map(keyFor),
    );
    if (prepared.size !== confirmed.size) return true;
    return [...confirmed].some((key) => !prepared.has(key));
  }

  function switchStage(state, stageId) {
    return createState(stageId);
  }

  function selectInitialStage(knownStageIds, currentStageId) {
    const known = Array.isArray(knownStageIds) ? knownStageIds : [];
    if (known.includes(currentStageId)) {
      return { stageId: currentStageId, warning: null };
    }
    return {
      stageId: known[0] || "setup",
      warning: currentStageId
        ? `任务记录的当前阶段 ${currentStageId} 不受支持，已只读回退到第一阶段。`
        : "任务缺少当前阶段，已只读回退到第一阶段。",
    };
  }

  function stageSnapshot(session, knownStageIds) {
    const known = Array.isArray(knownStageIds) ? knownStageIds : [];
    const stages = session?.stages && typeof session.stages === "object"
      ? session.stages
      : {};
    return known.map((stageId) => ({
      stageId,
      revision: Number(stages[stageId]?.revision || 0),
      status: stages[stageId]?.status || "draft",
    }));
  }

  function mergePersistenceIntent(currentMode, requestedMode) {
    if (currentMode === "submit" || requestedMode === "submit") return "submit";
    return requestedMode === "draft" ? "draft" : currentMode || null;
  }

  function canonicalJsonValue(value) {
    if (Array.isArray(value)) return value.map(canonicalJsonValue);
    if (value && typeof value === "object") {
      return Object.keys(value)
        .sort()
        .reduce((result, key) => {
          result[key] = canonicalJsonValue(value[key]);
          return result;
        }, {});
    }
    return value;
  }

  function jsonSemanticallyEqual(left, right) {
    return JSON.stringify(canonicalJsonValue(left))
      === JSON.stringify(canonicalJsonValue(right));
  }

  function stagePollChanged(
    previousRevision,
    previousStatus,
    nextRevision,
    nextStatus,
  ) {
    return previousRevision !== nextRevision || previousStatus !== nextStatus;
  }

  function stageNeedsResultHydration(
    stageId,
    status,
    result,
    inputLoaded = true,
    currentRevision = null,
  ) {
    if (!inputLoaded) return true;
    const editableReviewStage = ["completeness", "asset_matching"].includes(
      String(stageId || ""),
    ) && ["needs_user_input", "blocked"].includes(String(status || ""));
    if (!editableReviewStage) return false;
    if (!result || typeof result !== "object") return true;
    const expectedRevision = Number(currentRevision);
    const resultRevision = Number(result.revision);
    return Number.isInteger(expectedRevision)
      && Number.isInteger(resultRevision)
      && expectedRevision !== resultRevision;
  }

  function fifthStagePage(workflowState) {
    if (["plan_confirmed", "processing"].includes(workflowState)) return "process";
    if ([
      "outputs_ready",
      "copy_generating",
      "copy_review",
      "completed",
    ].includes(workflowState)) return "copy";
    return "compose";
  }

  function twoStepFifthStagePage(workflowState) {
    if ([
      "outputs_ready",
      "copy_generating",
      "copy_review",
      "completed",
    ].includes(workflowState)) return "copy";
    return "process";
  }

  function assetSelectionGuidance(decisions, missingMaterials) {
    const selected = (Array.isArray(decisions) ? decisions : [])
      .filter((item) => item?.decision === "selected");
    const identities = new Set();
    selected.forEach((item) => {
      const identity = String(
        item.sha256 || item.source_sha256 || item.asset_id || "",
      );
      if (identity) identities.add(identity);
    });
    const usableUnique = identities.size;
    const missingSlots = Math.max(0, Number(missingMaterials || 0));
    const completeSlots = Math.min(
      missingSlots,
      Math.floor(usableUnique / 3),
    );
    const assigned = Math.min(usableUnique, completeSlots * 9);
    const balancedCounts = completeSlots
      ? Array.from(
        { length: completeSlots },
        (_, index) => Math.floor(assigned / completeSlots)
          + (index < assigned % completeSlots ? 1 : 0),
      )
      : [];
    return {
      selectedCount: selected.length,
      usableUnique,
      duplicateCount: selected.length - usableUnique,
      completeSlots,
      balancedCounts,
      minimumShortage: Math.max(0, 3 - usableUnique),
      fillAllMinimumShortage: Math.max(0, missingSlots * 3 - usableUnique),
    };
  }

  function receiveRecovery(state, stageId, instruction) {
    if (stageId !== state.stageId) return state;
    return { ...state, recoveryInstruction: instruction };
  }

  function controlPresentation(value, kind, count) {
    let values;
    let checked = false;
      if (kind === "boolean") {
        values = [""];
        checked = value === true;
      } else if (kind === "list") {
        // Recover drafts written by the former auto_path_list renderer, which
        // submitted all paths as one comma-separated text value.
        const source = Array.isArray(value)
          ? value
          : (typeof value === "string" ? value.split(",").map((item) => item.trim()).filter(Boolean) : []);
        values = Array.from({ length: count }, (_, index) => String(source[index] ?? ""));
    } else if (kind === "line-list") {
      values = [Array.isArray(value) ? value.join("\n") : ""];
    } else if (kind === "json-list") {
      values = [JSON.stringify(Array.isArray(value) ? value : [], null, 2)];
    } else {
      values = [value == null ? "" : String(value)];
    }
    return { values, checked };
  }

  function imageSourceBusinessPathParts(value) {
    const raw = String(value || "").trim();
    if (!raw) return [];
    let parts = raw.split(/[\\/]+/).filter(Boolean);
    if (/^\\\\/.test(raw)) {
      parts = parts.slice(2);
    } else if (/^[A-Za-z]:[\\/]/.test(raw)) {
      parts = parts.slice(1);
    }
    return parts;
  }

  function disambiguateImageSourceLabels(value) {
    const sources = (Array.isArray(value) ? value : []).map((source) => ({
      ...source,
      label: String(source?.label || "").trim(),
      path: String(source?.path || "").trim(),
    }));
    const groups = new Map();
    sources.forEach((source, index) => {
      const key = source.label.toLocaleLowerCase();
      if (!key) return;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(index);
    });
    const occupied = new Set(
      [...groups.entries()]
        .filter(([, indexes]) => indexes.length === 1)
        .map(([key]) => key),
    );

    groups.forEach((indexes) => {
      if (indexes.length < 2) return;
      const paths = indexes.map((index) =>
        imageSourceBusinessPathParts(sources[index].path)
      );
      const shortest = Math.min(...paths.map((parts) => parts.length));
      let commonTail = 0;
      while (commonTail < shortest) {
        const components = paths.map((parts) =>
          parts[parts.length - commonTail - 1].toLocaleLowerCase()
        );
        if (!components.every((component) => component === components[0])) break;
        commonTail += 1;
      }

      const maximumExtra = Math.max(
        ...paths.map((parts) => Math.max(0, parts.length - commonTail)),
      );
      for (let extra = 1; extra <= maximumExtra; extra += 1) {
        const candidates = paths.map((parts, offset) => {
          const end = commonTail ? -commonTail : undefined;
          const start = -(commonTail + extra);
          const discriminator = parts.slice(start, end).join(" · ").trim();
          return discriminator
            ? `${sources[indexes[offset]].label}-${discriminator}`
            : "";
        });
        const candidateKeys = candidates.map((label) => label.toLocaleLowerCase());
        if (
          candidates.some((label) => !label || label.length > 100)
          || new Set(candidateKeys).size !== candidates.length
          || candidateKeys.some((key) => occupied.has(key))
        ) {
          continue;
        }
        indexes.forEach((index, offset) => {
          sources[index].label = candidates[offset];
          occupied.add(candidateKeys[offset]);
        });
        break;
      }
    });
    return sources;
  }

  function createRequestIdentity(stageId, sessionId, generation) {
    return { stageId, sessionId, generation };
  }

  function isCurrentRequest(identity, stageId, sessionId, generation) {
    return identity.stageId === stageId
      && identity.sessionId === sessionId
      && identity.generation === generation;
  }

  function resultSections(result) {
    const blockingReasons = Array.isArray(result?.blocking_reasons)
      ? result.blocking_reasons.filter((reason) => typeof reason === "string" && reason.length)
      : [];
    const nextAction = typeof result?.next_action === "string" && result.next_action.length
      ? result.next_action
      : null;
    return { blockingReasons, nextAction };
  }

  function resolveProductTitle(data, productId, preferredTitle = "") {
    const normalizedProductId = String(productId || "").trim();
    const collections = [
      data?.requirements,
      data?.products,
      data?.folder_candidates,
      data?.asset_candidates,
    ];
    for (const collection of collections) {
      const product = (Array.isArray(collection) ? collection : []).find(
        (item) => String(item?.product_id || "").trim() === normalizedProductId,
      );
      const title = String(
        product?.product_title || product?.product_name || "",
      ).trim();
      if (title) return title;
    }
    return String(preferredTitle || "").trim();
  }

  function folderReviewPresentation(data, removedProductIds = []) {
    const removed = new Set(
      (Array.isArray(removedProductIds) ? removedProductIds : [])
        .map((value) => String(value || "").trim())
        .filter(Boolean),
    );
    const candidates = (Array.isArray(data?.folder_candidates)
      ? data.folder_candidates
      : [])
      .filter((candidate) => candidate?.match_type !== "confirmed_alias")
      .filter(
        (candidate) => !removed.has(String(candidate?.product_id || "").trim()),
      );
    const productCount = new Set(
      candidates
        .map((candidate) => String(candidate?.product_id || "").trim())
        .filter(Boolean),
    ).size;
    return {
      candidates,
      candidateCount: candidates.length,
      productCount,
      summary: candidates.length
        ? `已显示 ${productCount} 个商品的 ${candidates.length} 个候选文件夹`
        : "未找到可显示的候选文件夹",
    };
  }

  function filterCompletenessProducts(products, {
    query = "",
    status = "all",
    owner = "all",
    productGrade = "all",
    selectedProductIds = [],
  } = {}) {
    const needle = String(query || "").trim().toLocaleLowerCase("zh-CN");
    const selectedStatus = String(status || "all");
    const selectedOwner = String(owner || "all");
    const selectedProductGrade = String(productGrade || "all");
    const selected = new Set(
      [...(selectedProductIds || [])]
        .map((value) => String(value || "").trim())
        .filter(Boolean),
    );
    return (Array.isArray(products) ? products : []).filter((product) => {
      const productOwner = String(product?.owner || "").trim();
      const productGrade = String(
        product?.product_grade || product?.grade || "",
      ).trim();
      const matchesStatus = selectedStatus === "all"
        || (selectedStatus === "selected"
          && selected.has(String(product?.product_id || "").trim()))
        || product?.status === selectedStatus;
      const matchesOwner = selectedOwner === "all"
        || (selectedOwner === "__unassigned__" && !productOwner)
        || productOwner === selectedOwner;
      const matchesProductGrade = selectedProductGrade === "all"
        || (selectedProductGrade === "__ungraded__" && !productGrade)
        || productGrade === selectedProductGrade;
      const haystack = [
        product?.product_id,
        product?.sku,
        product?.product_title,
        productOwner,
        productGrade,
      ].join(" ").toLocaleLowerCase("zh-CN");
      return matchesStatus
        && matchesOwner
        && matchesProductGrade
        && (!needle || haystack.includes(needle));
    });
  }

  function completenessProductHasOpenSlots(product) {
    if (String(product?.status || "") === "complete") return false;
    const promotion = product?.promotion;
    if (!promotion || typeof promotion !== "object") return true;
    const finiteNumber = (value) => {
      if (value == null || typeof value === "boolean" || value === "") return null;
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    };
    const missing = finiteNumber(promotion.missing_count);
    if (missing != null) return missing > 0;
    const current = finiteNumber(promotion.current_count);
    const target = finiteNumber(promotion.target_slots);
    if (current != null && target != null) return current < target;
    return true;
  }

  function completenessProductSelectable(product) {
    return product?.selectable !== false
      && product?.status !== "excluded"
      && completenessProductHasOpenSlots(product);
  }

  function draftRequestBody(values, revision) {
    return { values, revision };
  }

  function persistedRevision(payload) {
    return payload.revision;
  }

  return {
    activeProductTargetIndex,
    assetSelectedByOtherProduct,
    candidateFeasibleRatios,
    canonicalJsonValue,
    assetSelectionGuidance,
    completenessProductHasOpenSlots,
    completenessProductSelectable,
    completenessEmptyView,
    connectionView,
    controlPresentation,
    copyDraftsForVersion,
    copyRequestVersionView,
    createRequestIdentity,
    createSelectionPreflightScheduler,
    createState,
    draftRequestBody,
    fifthStagePage,
    filterCompletenessProducts,
    galleryNeedsReload,
    globalAssetSelectionTarget,
    prioritizeGlobalAssetCandidates,
    twoStepFifthStagePage,
    isCurrentRequest,
    jsonSemanticallyEqual,
    markDirty,
    mergeSelectedAssetDecision,
    persistedRevision,
    mergePersistenceIntent,
    receiveRecovery,
    receiveStage,
    receiveStatus,
    recoveryView,
    resultView,
    resultSections,
    resolveProductTitle,
    setupConfigurationLoadView,
    hasSavedSetupConfiguration,
    folderReviewPresentation,
    statusLabels,
    stageNeedsResultHydration,
    stagePollChanged,
    stageSnapshot,
    submissionView,
    technicalDiagnosticView,
    selectInitialStage,
    selectionPreflightWeight,
    stableGlobalAssetOrder,
    shouldDeferEditableStageHydration,
    switchStage,
    disambiguateImageSourceLabels,
  };
});
