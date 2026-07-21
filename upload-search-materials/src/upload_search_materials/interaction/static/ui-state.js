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
    const eligible = state.stageId === "results" && userActionStatuses.has(state.serverStatus);
    return { visible: eligible, submitEnabled: eligible };
  }

  function resultView(state) {
    if (!state.result || typeof state.result !== "object") {
      return { mode: "empty", label: "尚未扫描" };
    }
    return { mode: "result", result: state.result };
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
      const source = Array.isArray(value) ? value : [];
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

  return {
    connectionView,
    controlPresentation,
    createState,
    markDirty,
    receiveRecovery,
    receiveStage,
    receiveStatus,
    recoveryView,
    resultView,
    statusLabels,
    switchStage,
  };
});
