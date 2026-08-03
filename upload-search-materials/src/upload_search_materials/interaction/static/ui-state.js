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

  function connectionView(state, now, heartbeatWindowMs = 10000) {
    const heartbeatAt = Date.parse(state.lastAgentHeartbeat || "");
    const heartbeatIsRecent = Number.isFinite(heartbeatAt)
      && now >= heartbeatAt
      && now - heartbeatAt < heartbeatWindowMs;
    if (state.serverStatus === "processing") {
      return {
        offline: false,
        statusLabel: statusLabels.processing,
        connectionLabel: heartbeatIsRecent ? "Agent 已连接" : "Agent 心跳暂不可见",
      };
    }
    return {
      offline: !heartbeatIsRecent,
      statusLabel: statusLabels[state.serverStatus] || statusLabels.draft,
      connectionLabel: heartbeatIsRecent ? "Agent 已连接" : "Agent 未连接",
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
    switchStage,
  };
});
