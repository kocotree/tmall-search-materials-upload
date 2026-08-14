(function attachInteractionUiState(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.InteractionUiState = api;
})(typeof globalThis === "object" ? globalThis : this, function createInteractionUiStateApi() {
  "use strict";

  const userActionStatuses = new Set(["needs_user_input", "blocked"]);
  const statusLabels = {
    draft: "编辑中",
    ready_for_agent: "已提交，等待 Agent",
    processing: "Agent 处理中",
    needs_user_input: "补充后重新提交",
    blocked: "补充后重新提交",
    completed: "已完成",
  };

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
        connectionLabel: "Agent 正在处理",
      };
    }
    if (waitIsLive) {
      return {
        offline: false,
        statusLabel: statusLabels[state.serverStatus] || statusLabels.draft,
        connectionLabel: "Agent 正在监听",
      };
    }
    return {
      offline: true,
      statusLabel: statusLabels[state.serverStatus] || statusLabels.draft,
      connectionLabel: "Agent 未连接",
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
            job.state = "completed";
            job.result = result;
            job.resolve(result);
          })
          .catch((error) => {
            job.state = "failed";
            job.error = error;
            job.reject(error);
          })
          .finally(() => {
            activeCount -= 1;
            activeWeight -= job.weight;
            notify(job);
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

    function desiredPendingCount() {
      return [...jobs.values()].filter(
        (job) => job.desiredSelected && ["queued", "running"].includes(job.state),
      ).length;
    }

    return {
      cancel,
      desiredPendingCount,
      get: (assetId) => snapshot(jobs.get(String(assetId || ""))),
      select,
    };
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

  function draftRequestBody(values, revision) {
    return { values, revision };
  }

  function persistedRevision(payload) {
    return payload.revision;
  }

  return {
    canonicalJsonValue,
    assetSelectionGuidance,
    connectionView,
    controlPresentation,
    createRequestIdentity,
    createSelectionPreflightScheduler,
    createState,
    draftRequestBody,
    fifthStagePage,
    twoStepFifthStagePage,
    isCurrentRequest,
    jsonSemanticallyEqual,
    markDirty,
    persistedRevision,
    mergePersistenceIntent,
    receiveRecovery,
    receiveStage,
    receiveStatus,
    recoveryView,
    resultView,
    resultSections,
    statusLabels,
    stagePollChanged,
    stageSnapshot,
    submissionView,
    selectInitialStage,
    selectionPreflightWeight,
    switchStage,
    disambiguateImageSourceLabels,
  };
});
