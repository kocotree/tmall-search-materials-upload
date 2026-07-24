(() => {
  "use strict";

  const shell = document.querySelector("[data-component='AppShell']");
  if (!shell) return;
  const UiState = window.InteractionUiState;
  if (!UiState) throw new Error("interaction UI state module is unavailable");

  const registry = JSON.parse(document.querySelector("#stage-registry").textContent);
  const stages = new Map(registry.map((stage) => [stage.id, stage]));
  const railButtons = [...document.querySelectorAll("[data-stage-id]")];
  const panels = [...document.querySelectorAll("[data-stage-panel]")];
  const saveButton = document.querySelector("[data-save-draft]");
  const submitButton = document.querySelector("[data-submit-stage]");
  const recoveryButton = document.querySelector("[data-copy-recovery]");
  const offlinePanel = document.querySelector("[data-offline-panel]");
  const statusBadge = document.querySelector("[data-current-status]");
  const handoffStatus = document.querySelector("[data-handoff-status]");
  const actionMessage = document.querySelector("[data-action-message]");
  const revisionLabel = document.querySelector("[data-revision]");
  const titleLabel = document.querySelector("[data-current-title]");
  const currentStageLabel = document.querySelector("[data-current-stage-label]");
  const lastSubmittedLabel = document.querySelector("[data-last-submitted]");
  const taskDirectoryLabel = document.querySelector("[data-task-directory]");
  const connectionLabel = document.querySelector("[data-connection-label]");

  let sessionId = shell.dataset.sessionId || "";
  let currentStageId = railButtons[0]?.dataset.stageId || "setup";
  let revision = 0;
  let uiState = UiState.createState(currentStageId);
  let stageGeneration = 0;

  const statusCopy = UiState.statusLabels;
  const stageActions = { draft: "/draft", submit: "/submit" };
  const resultRenderers = {
    "setup_form": [],
    "inspection_matrix": ["InspectionMatrix"],
    "product_scope_table": ["ProductScopeTable"],
    "asset_match_gallery": ["AssetMatchGallery"],
    "image_review": ["CropDecision"],
    "slots_copy_editor": ["SlotBoard", "CopyEditor"],
    "dry_run_review": ["DryRunSummary"],
    "approval_table": ["ApprovalChecklist"],
    "production_confirmation": ["ProductionConfirmation"],
    "result_timeline": ["ResultTimeline"],
  };

  function apiPath(suffix = "") {
    return `/api/sessions/${encodeURIComponent(sessionId)}${suffix}`;
  }

  async function fetchJson(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({ error: "响应格式无效" }));
    if (!response.ok) {
      const error = new Error(payload.error || `请求失败（${response.status}）`);
      error.fieldErrors = payload.field_errors || {};
      throw error;
    }
    return payload;
  }

  async function ensureSession() {
    if (sessionId) return sessionId;
    const payload = await fetchJson("/api/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    });
    sessionId = payload.session_id;
    shell.dataset.sessionId = sessionId;
    document.querySelectorAll("[data-session-label]").forEach((node) => {
      node.textContent = sessionId;
    });
    const taskDirectory = `${shell.dataset.runsRoot}\\${sessionId}`;
    taskDirectoryLabel.textContent = taskDirectory;
    document.querySelectorAll("[data-setup-task-directory]").forEach((node) => {
      node.textContent = taskDirectory;
    });
    const url = new URL(window.location.href);
    url.searchParams.set("session_id", sessionId);
    window.history.replaceState({}, "", url);
    return sessionId;
  }

  function activeForm() {
    return document.querySelector(`[data-stage-form="${currentStageId}"]`);
  }

  function clearFieldErrors(form) {
    form.querySelectorAll("[data-field-error]").forEach((node) => {
      node.textContent = "";
    });
    form.querySelectorAll("[aria-invalid='true']").forEach((control) => {
      control.removeAttribute("aria-invalid");
    });
  }

  function showFieldErrors(form, fieldErrors) {
    Object.entries(fieldErrors).forEach(([name, message]) => {
      const errorNode = form.querySelector(`[data-field-error="${CSS.escape(name)}"]`);
      const control = form.querySelector(`[name="${CSS.escape(name)}"]`);
      if (errorNode) errorNode.textContent = message;
      if (control) control.setAttribute("aria-invalid", "true");
    });
  }

  function serializeForm(form) {
    const values = {};
    const processed = new Set();
    const controls = [...form.querySelectorAll("[name]")].filter((control) => !control.disabled);

    controls.forEach((control) => {
      const name = control.name;
      if (processed.has(name)) return;
      processed.add(name);
      const group = controls.filter((candidate) => candidate.name === name);
      const kind = control.dataset.valueKind || "string";

      if (group.length > 1 && control.type !== "radio") {
        values[name] = group.map((candidate) => candidate.value.trim()).filter(Boolean);
      } else if (control.type === "radio") {
        values[name] = group.find((candidate) => candidate.checked)?.value || "";
      } else if (kind === "boolean") {
        values[name] = control.checked;
      } else if (kind === "number") {
        values[name] = control.value === "" ? null : Number(control.value);
      } else if (kind === "line-list") {
        values[name] = control.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      } else if (kind === "json-list") {
        const parsed = control.value.trim() ? JSON.parse(control.value) : [];
        if (!Array.isArray(parsed)) throw new Error(`${name} 必须是 JSON 数组`);
        values[name] = parsed;
      } else {
        values[name] = control.value;
      }
    });
    return values;
  }

  function hydrateForm(form, values) {
    Object.entries(values || {}).forEach(([name, value]) => {
      const controls = [...form.querySelectorAll(`[name="${CSS.escape(name)}"]`)];
      if (!controls.length) return;
      const first = controls[0];
      if (first.type === "radio") {
        controls.forEach((control) => { control.checked = control.value === String(value); });
        return;
      }
      const kind = controls.length > 1 ? "list" : (first.dataset.valueKind || "string");
      const presentation = UiState.controlPresentation(value, kind, controls.length);
      controls.forEach((control, index) => {
        if (kind === "boolean") control.checked = presentation.checked;
        else control.value = presentation.values[index] ?? "";
      });
    });
  }

  function renderStatus() {
    const status = uiState.dirty ? "draft" : uiState.serverStatus;
    const copy = statusCopy[status] || statusCopy.draft;
    statusBadge.textContent = copy;
    statusBadge.dataset.status = status;
    handoffStatus.textContent = copy;
    const railStatus = document.querySelector(`[data-rail-status="${currentStageId}"]`);
    if (railStatus) railStatus.textContent = copy;
    submitButton.textContent = ["needs_user_input", "blocked"].includes(status)
      ? "补充后重新提交"
      : status === "ready_for_agent"
        ? "已提交，等待 Agent"
        : status === "processing"
          ? "Agent 处理中"
          : "提交给 Agent";
    const lockedByServer = !uiState.dirty
      && ["ready_for_agent", "processing", "completed"].includes(uiState.serverStatus);
    submitButton.disabled = lockedByServer;
    saveButton.disabled = lockedByServer;
    updateResultsRecovery(UiState.recoveryView(uiState));
  }

  function renderSubmission() {
    const { createdAt } = UiState.submissionView(uiState);
    if (!createdAt) {
      lastSubmittedLabel.textContent = "尚未提交";
      return;
    }
    const timestamp = Date.parse(createdAt);
    lastSubmittedLabel.textContent = Number.isFinite(timestamp)
      ? new Date(timestamp).toLocaleString("zh-CN", { hour12: false })
      : createdAt;
  }

  function updateResultsRecovery(recovery) {
    const isResults = currentStageId === "results";
    const mayRecover = isResults && recovery.visible;
    const recoveryForm = document.querySelector("[data-results-recovery]");
    if (recoveryForm) {
      recoveryForm.hidden = !mayRecover;
      recoveryForm.querySelectorAll("[name]").forEach((control) => {
        control.disabled = !mayRecover;
      });
    }
    if (isResults && !mayRecover) {
      saveButton.hidden = true;
      submitButton.hidden = true;
      saveButton.disabled = true;
      submitButton.disabled = true;
      actionMessage.textContent = "结果阶段只读；仅当 Agent 明确要求补充时开放恢复输入。";
    } else if (mayRecover) {
      saveButton.hidden = true;
      saveButton.disabled = true;
      submitButton.hidden = false;
      submitButton.disabled = !recovery.submitEnabled;
      submitButton.textContent = "补充后重新提交";
    } else {
      saveButton.hidden = false;
      submitButton.hidden = false;
    }
  }

  function renderResult(componentName, view) {
    const module = document.querySelector(`[data-component="${CSS.escape(componentName)}"]`);
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;
    content.replaceChildren();

    if (view.mode === "empty") {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.dataset.emptyState = view.label;
      const mark = document.createElement("span");
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = "◎";
      const label = document.createElement("strong");
      label.textContent = view.label;
      empty.append(mark, label);
      content.appendChild(empty);
      return;
    }

    const summary = document.createElement("div");
    summary.className = "result-data";
    const heading = document.createElement("strong");
    const result = view.result;
    heading.textContent = result.summary || "Agent 已返回结果";
    summary.appendChild(heading);
    if (Array.isArray(result.evidence) && result.evidence.length) {
      const evidence = document.createElement("ul");
      result.evidence.forEach((item) => {
        const row = document.createElement("li");
        row.textContent = typeof item === "string" ? item : JSON.stringify(item);
        evidence.appendChild(row);
      });
      summary.appendChild(evidence);
    }
    const sections = UiState.resultSections(result);
    if (sections.blockingReasons.length) {
      const blocking = document.createElement("section");
      const label = document.createElement("strong");
      label.textContent = "阻塞原因";
      const reasons = document.createElement("ul");
      sections.blockingReasons.forEach((reason) => {
        const row = document.createElement("li");
        row.textContent = reason;
        reasons.appendChild(row);
      });
      blocking.append(label, reasons);
      summary.appendChild(blocking);
    }
    if (sections.nextAction) {
      const action = document.createElement("section");
      const label = document.createElement("strong");
      label.textContent = "下一步";
      const nextAction = document.createElement("p");
      nextAction.textContent = result.next_action;
      action.append(label, nextAction);
      summary.appendChild(action);
    }
    content.appendChild(summary);
  }

  function syncSetupProductScope() {
    const form = document.querySelector('[data-stage-form="setup"]');
    const scope = form?.querySelector('[name="product_scope"]');
    const field = form?.querySelector("[data-product-ids-field]");
    const productIds = form?.querySelector('[name="product_ids"]');
    if (!scope || !field || !productIds) return;
    const selected = scope.value === "selected";
    field.hidden = !selected;
    productIds.disabled = !selected;
    if (!selected) productIds.value = "";
  }

  function readJsonListControl(name) {
    const control = activeForm()?.querySelector(`[name="${CSS.escape(name)}"]`);
    if (!control?.value.trim()) return [];
    try {
      const value = JSON.parse(control.value);
      return Array.isArray(value) ? value : [];
    } catch (_error) {
      return [];
    }
  }

  function writeJsonListControl(name, values) {
    const control = activeForm()?.querySelector(`[name="${CSS.escape(name)}"]`);
    if (!control) return;
    control.value = JSON.stringify(values, null, 2);
    control.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function assetSortKey(candidate) {
    const matchRank = {
      exact_product_id: "0",
      exact_sku: "1",
      confirmed_alias: "2",
      name_candidate: "3",
    };
    return [
      matchRank[candidate.match_type] || "9",
      candidate.source_system || "",
      candidate.source_path || "",
      candidate.sha256 || "",
      candidate.asset_id || "",
    ].join("\u0000").toLocaleLowerCase("zh-CN");
  }

  function uniqueSortedCandidates(candidates) {
    const seen = new Set();
    return [...candidates]
      .sort((left, right) => assetSortKey(left).localeCompare(assetSortKey(right), "zh-CN"))
      .filter((candidate) => {
        const fingerprint = String(candidate.sha256 || "");
        if (!fingerprint || seen.has(fingerprint)) return false;
        seen.add(fingerprint);
        return true;
      });
  }

  function confirmedLicenseIds() {
    return new Set(
      readJsonListControl("license_decisions")
        .filter((item) => item?.status === "confirmed")
        .map((item) => String(item.asset_id || "")),
    );
  }

  function selectedAssetDecisions() {
    return readJsonListControl("asset_decisions")
      .filter((item) => item?.decision === "selected" && item.asset_id);
  }

  function candidateIsSelectable(candidate, licenses) {
    const confirmedMatch = ["matched_unlicensed", "confirmed", "confirmed_alias"]
      .includes(candidate.match_status);
    return candidate.validation_status === "valid"
      && confirmedMatch
      && !candidate.remote_duplicate
      && (candidate.license_status === "confirmed" || licenses.has(String(candidate.asset_id)));
  }

  function persistSelectedCandidates(productId, selected, imagesPerMaterial = 3) {
    const otherProducts = selectedAssetDecisions()
      .filter((item) => String(item.product_id) !== String(productId));
    const selectedRows = selected.map((candidate, index) => ({
      product_id: String(productId),
      asset_id: String(candidate.asset_id),
      sha256: String(candidate.sha256),
      source_system: String(candidate.source_system || ""),
      source_path: String(candidate.source_path || ""),
      decision: "selected",
      group_index: Math.floor(index / imagesPerMaterial) + 1,
      position: (index % imagesPerMaterial) + 1,
    }));
    writeJsonListControl("asset_decisions", [...otherProducts, ...selectedRows]);
  }

  function persistLicense(assetId, confirmed) {
    const retained = readJsonListControl("license_decisions")
      .filter((item) => String(item.asset_id) !== String(assetId));
    if (confirmed) retained.push({ asset_id: String(assetId), status: "confirmed" });
    writeJsonListControl("license_decisions", retained);
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function folderDecisions() {
    return readJsonListControl("folder_decisions")
      .filter((item) => item?.folder_id && item?.product_id);
  }

  function persistFolderDecision(candidate, decision, alias = "", note = "") {
    const folderId = String(candidate.folder_id || "");
    const productId = String(candidate.product_id || "");
    const retained = folderDecisions().filter(
      (item) => String(item.folder_id) !== folderId
        || String(item.product_id) !== productId,
    );
    if (decision !== "pending") {
      retained.push({
        folder_id: folderId,
        product_id: productId,
        source_system: String(candidate.source_system || ""),
        folder_path: String(candidate.folder_path || ""),
        decision,
        alias: decision === "confirmed_alias" ? String(alias || "").trim() : "",
        note: String(note || "").trim(),
      });
    }
    writeJsonListControl("folder_decisions", retained);
  }

  function renderFolderOwnershipReview(view) {
    if (view.mode === "empty") return;
    const data = view.result?.data;
    const candidates = Array.isArray(data?.folder_candidates) ? data.folder_candidates : [];
    if (!candidates.length) return;
    const module = document.querySelector('[data-component="AssetMatchGallery"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;

    const review = element("section", "folder-review");
    const safety = element("div", "asset-safety");
    safety.dataset.status = "checked";
    safety.append(
      element("strong", "", "当前只审查文件夹"),
      element("span", "", "确认归属前不会读取、统计或哈希文件夹中的图片。"),
    );
    review.appendChild(safety);

    const decisionsByKey = new Map(
      folderDecisions().map((item) => [
        `${item.product_id}\u0000${item.folder_id}`,
        item,
      ]),
    );
    const productIds = [...new Set(candidates.map((item) => String(item.product_id || "")))]
      .sort((left, right) => left.localeCompare(right, "zh-CN"));

    productIds.forEach((productId) => {
      const productCandidates = candidates.filter(
        (item) => String(item.product_id || "") === productId,
      );
      const group = element("section", "folder-product");
      const heading = element("div", "asset-product-heading");
      const headingText = element("div");
      headingText.append(
        element("strong", "", productCandidates[0]?.product_title || `商品 ${productId}`),
        element(
          "span",
          "",
          `商品 ID ${productId} · 货号 ${productCandidates[0]?.sku || "未知"} · ${productCandidates.length} 个候选文件夹`,
        ),
      );
      const progress = element("span", "folder-review-progress");
      heading.append(headingText, progress);
      group.appendChild(heading);
      const list = element("div", "folder-list");
      group.appendChild(list);
      review.appendChild(group);

      const updateProgress = () => {
        const current = folderDecisions();
        const decided = productCandidates.filter((candidate) => current.some(
          (item) => String(item.product_id) === productId
            && String(item.folder_id) === String(candidate.folder_id),
        )).length;
        progress.textContent = `已处理 ${decided} / ${productCandidates.length}`;
      };

      productCandidates.forEach((candidate) => {
        const key = `${productId}\u0000${candidate.folder_id}`;
        const saved = decisionsByKey.get(key) || {};
        const card = element("article", "folder-card");
        const identity = element("div", "folder-card-identity");
        identity.append(
          element("strong", "", candidate.folder_name || "未命名文件夹"),
          element(
            "span",
            "folder-match-badge",
            candidate.match_type === "exact_sku" ? "货号命中" : "名称候选",
          ),
          element("small", "", candidate.source_system || "未知来源"),
          element("code", "", candidate.folder_path || ""),
        );
        const controls = element("div", "folder-card-controls");
        const decision = document.createElement("select");
        decision.setAttribute("aria-label", `${candidate.folder_name} 归属决定`);
        [
          ["pending", "待确认"],
          ["confirmed", "确认归属"],
          ["confirmed_alias", "确认归属并记录别名"],
          ["rejected", "排除该文件夹"],
        ].forEach(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          decision.appendChild(option);
        });
        decision.value = saved.decision || candidate.decision || "pending";
        const alias = document.createElement("input");
        alias.type = "text";
        alias.placeholder = "记录文件夹别名";
        alias.value = saved.alias || candidate.alias || candidate.folder_name || "";
        const note = document.createElement("input");
        note.type = "text";
        note.placeholder = "备注（可选）";
        note.value = saved.note || candidate.note || "";
        const warning = element(
          "span",
          "asset-warning",
          candidate.match_type === "exact_sku"
            ? "货号命中仍需核对同货号异名或主副链接"
            : "名称候选必须人工确认归属",
        );
        controls.append(decision, alias, note, warning);
        card.append(identity, controls);
        list.appendChild(card);

        const sync = () => {
          alias.disabled = decision.value !== "confirmed_alias";
          card.dataset.decision = decision.value;
          if (decision.value === "confirmed_alias" && !alias.value.trim()) {
            alias.value = candidate.folder_name || "";
          }
          persistFolderDecision(
            candidate,
            decision.value,
            alias.value,
            note.value,
          );
          updateProgress();
        };
        decision.addEventListener("change", sync);
        alias.addEventListener("change", sync);
        note.addEventListener("change", sync);
        alias.disabled = decision.value !== "confirmed_alias";
        card.dataset.decision = decision.value;
      });
      updateProgress();
    });
    content.appendChild(review);
  }

  function renderAssetMatchGallery(view) {
    if (view.mode === "empty") return;
    const data = view.result?.data;
    const candidates = Array.isArray(data?.asset_candidates) ? data.asset_candidates : [];
    const requirements = Array.isArray(data?.requirements) ? data.requirements : [];
    if (!candidates.length && !requirements.length) return;
    const module = document.querySelector('[data-component="AssetMatchGallery"]');
    const content = module?.querySelector("[data-result-content]");
    if (!content) return;

    const safety = element("div", "asset-safety");
    const remoteChecked = data.remote_dedupe_status === "checked";
    safety.dataset.status = remoteChecked ? "checked" : "pending";
    safety.append(
      element("strong", "", remoteChecked ? "远端去重已检查" : "远端去重未完成"),
      element(
        "span",
        "",
        remoteChecked
          ? "已知远端指纹会从候选中排除。"
          : "后台仅有素材 ID，尚无图片指纹；最终上传前必须再次核对。",
      ),
    );
    content.appendChild(safety);

    requirements.forEach((requirement) => {
      const productId = String(requirement.product_id || "");
      const imagesPerMaterial = Number(requirement.images_per_material || 3);
      const missingMaterials = Number(requirement.missing_materials || 0);
      const requiredImages = Math.max(0, imagesPerMaterial * missingMaterials);
      const productCandidates = uniqueSortedCandidates(
        candidates.filter((candidate) => String(candidate.product_id) === productId),
      );
      const product = element("section", "asset-product");
      const heading = element("div", "asset-product-heading");
      const title = element("div");
      title.append(
        element("strong", "", requirement.product_title || `商品 ${productId}`),
        element(
          "span",
          "",
          `商品 ID ${productId} · 缺 ${missingMaterials} 篇 · 每篇 ${imagesPerMaterial} 张 · 共需 ${requiredImages} 张`,
        ),
      );
      const batchButton = element("button", "secondary-button asset-change-batch", "换一批");
      batchButton.type = "button";
      heading.append(title, batchButton);
      product.appendChild(heading);

      const selectionSummary = element("p", "asset-selection-summary");
      product.appendChild(selectionSummary);
      const grid = element("div", "asset-grid");
      product.appendChild(grid);
      content.appendChild(product);

      const draw = (requestedBatch = null) => {
        const licenses = confirmedLicenseIds();
        const currentDecisions = selectedAssetDecisions();
        const hashesUsedElsewhere = new Set(
          currentDecisions
            .filter((item) => String(item.product_id) !== productId)
            .map((item) => String(item.sha256 || "")),
        );
        const eligible = productCandidates.filter(
          (candidate) => candidateIsSelectable(candidate, licenses)
            && !hashesUsedElsewhere.has(String(candidate.sha256 || "")),
        );
        const previousForProduct = currentDecisions
          .filter((item) => String(item.product_id) === productId);
        let selectedIds = new Set(previousForProduct.map((item) => String(item.asset_id)));
        let batchIndex = Number(product.dataset.batchIndex || 0);
        if (requestedBatch != null) {
          batchIndex = requestedBatch;
          const start = batchIndex * requiredImages;
          const batch = eligible.slice(start, start + requiredImages);
          if (!batch.length && batchIndex > 0) {
            batchIndex = 0;
            selectedIds = new Set(eligible.slice(0, requiredImages).map((item) => String(item.asset_id)));
          } else {
            selectedIds = new Set(batch.map((item) => String(item.asset_id)));
          }
          persistSelectedCandidates(
            productId,
            productCandidates.filter((item) => selectedIds.has(String(item.asset_id))),
            imagesPerMaterial,
          );
        }
        product.dataset.batchIndex = String(batchIndex);
        grid.replaceChildren();

        productCandidates.forEach((candidate) => {
          const assetId = String(candidate.asset_id || "");
          const selectable = candidateIsSelectable(candidate, licenses)
            && !hashesUsedElsewhere.has(String(candidate.sha256 || ""));
          const card = element("article", "asset-card");
          if (selectedIds.has(assetId)) card.classList.add("is-selected");
          const image = document.createElement("img");
          image.loading = "lazy";
          image.alt = `${requirement.product_title || productId} 候选图片`;
          image.src = apiPath(
            `/stages/asset_matching/assets/${encodeURIComponent(assetId)}`,
          );
          const meta = element("div", "asset-card-meta");
          meta.append(
            element("strong", "", candidate.source_system || "未知来源"),
            element("span", "", candidate.match_type || "未知匹配"),
            element("small", "", candidate.source_path || ""),
          );
          const controls = element("div", "asset-card-controls");
          const licenseLabel = element("label", "asset-check");
          const license = document.createElement("input");
          license.type = "checkbox";
          license.checked = candidate.license_status === "confirmed" || licenses.has(assetId);
          licenseLabel.append(license, document.createTextNode("授权已确认"));
          const selectLabel = element("label", "asset-check");
          const select = document.createElement("input");
          select.type = "checkbox";
          select.checked = selectedIds.has(assetId);
          select.disabled = !selectable;
          selectLabel.append(select, document.createTextNode("采用"));
          controls.append(licenseLabel, selectLabel);
          if (candidate.match_status === "needs_manual_confirmation") {
            controls.appendChild(element("span", "asset-warning", "名称候选需先确认归属"));
          } else if (candidate.remote_duplicate) {
            controls.appendChild(element("span", "asset-warning", "与已上传素材重复"));
          } else if (candidate.validation_status !== "valid") {
            controls.appendChild(element("span", "asset-warning", "图片校验未通过"));
          }
          card.append(image, meta, controls);
          grid.appendChild(card);

          license.addEventListener("change", () => {
            persistLicense(assetId, license.checked);
            draw();
          });
          select.addEventListener("change", () => {
            if (select.checked && selectedIds.size >= requiredImages) {
              select.checked = false;
              actionMessage.textContent = `商品 ${productId} 已选满 ${requiredImages} 张，请先取消一张再替换。`;
              return;
            }
            if (select.checked) selectedIds.add(assetId);
            else selectedIds.delete(assetId);
            persistSelectedCandidates(
              productId,
              productCandidates.filter((item) => selectedIds.has(String(item.asset_id))),
              imagesPerMaterial,
            );
            draw();
          });
        });
        selectionSummary.textContent = `已选 ${selectedIds.size} / ${requiredImages} 张；按顺序每 ${imagesPerMaterial} 张组成一篇图文素材。`;
        batchButton.disabled = eligible.length <= requiredImages;
      };

      batchButton.addEventListener("click", () => {
        draw(Number(product.dataset.batchIndex || 0) + 1);
      });
      draw();
    });
  }

  function renderStageResult(schemaComponent) {
    const view = UiState.resultView(uiState);
    (resultRenderers[schemaComponent] || []).forEach((rendererName) => {
      renderResult(rendererName, view);
    });
    if (schemaComponent === "asset_match_gallery") {
      renderFolderOwnershipReview(view);
      renderAssetMatchGallery(view);
    }
  }

  async function loadStage() {
    const requestedStageId = currentStageId;
    if (!sessionId) {
      revision = 0;
      revisionLabel.textContent = "0";
      renderStatus();
      renderSubmission();
      renderStageResult(stages.get(requestedStageId).component);
      return;
    }
    try {
      const payload = await fetchJson(apiPath(`/stages/${requestedStageId}`));
      if (requestedStageId !== currentStageId) return;
      revision = payload.state.revision;
      revisionLabel.textContent = String(revision);
      uiState = UiState.receiveStage(uiState, {
        stageId: requestedStageId,
        status: payload.state.status,
        result: payload.result,
        submission: payload.submission,
      });
      if (payload.input) hydrateForm(activeForm(), payload.input.values);
      if (requestedStageId === "setup") syncSetupProductScope();
      renderStatus();
      renderSubmission();
      renderStageResult(stages.get(requestedStageId).component);
    } catch (error) {
      if (requestedStageId !== currentStageId) return;
      actionMessage.textContent = error.message;
    }
  }

  async function persistStage(mode) {
    const requestedStageId = currentStageId;
    const requestedGeneration = stageGeneration;
    const form = activeForm();
    if (!form) return;
    clearFieldErrors(form);
    let values;
    try {
      values = serializeForm(form);
    } catch (error) {
      actionMessage.textContent = error.message;
      return;
    }

    saveButton.disabled = true;
    submitButton.disabled = true;
    actionMessage.textContent = mode === "draft" ? "正在保存草稿…" : "正在创建交接…";
    let requestIdentity = null;
    try {
      await ensureSession();
      if (requestedStageId !== currentStageId || requestedGeneration !== stageGeneration) return;
      requestIdentity = UiState.createRequestIdentity(
        requestedStageId,
        sessionId,
        requestedGeneration,
      );
      const body = mode === "draft"
        ? UiState.draftRequestBody(values, revision)
        : { values };
      if (mode === "submit") body.revision = revision + 1;
      const payload = await fetchJson(apiPath(`/stages/${requestedStageId}${stageActions[mode]}`), {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!UiState.isCurrentRequest(
        requestIdentity,
        currentStageId,
        sessionId,
        stageGeneration,
      )) return;
      if (mode === "submit") {
        revision = payload.revision;
        uiState = UiState.receiveStage(uiState, {
          stageId: requestedStageId,
          status: "ready_for_agent",
          result: null,
          submission: { created_at: payload.created_at },
        });
        renderStatus();
        renderSubmission();
        renderStageResult(stages.get(requestedStageId).component);
        actionMessage.textContent = "交接已持久化，正在等待 Agent 接收。";
        await loadRecoveryInstruction(requestedStageId);
      } else {
        revision = UiState.persistedRevision(payload);
        uiState = UiState.receiveStage(uiState, {
          stageId: requestedStageId,
          status: "draft",
          result: null,
          submission: null,
        });
        renderStatus();
        renderSubmission();
        renderStageResult(stages.get(requestedStageId).component);
        actionMessage.textContent = "草稿已保存；不会创建 Agent 交接。";
      }
      revisionLabel.textContent = String(revision);
    } catch (error) {
      if (requestedStageId !== currentStageId || requestedGeneration !== stageGeneration) return;
      if (requestIdentity && !UiState.isCurrentRequest(
        requestIdentity,
        currentStageId,
        sessionId,
        stageGeneration,
      )) return;
      showFieldErrors(form, error.fieldErrors || {});
      actionMessage.textContent = error.message;
      uiState = UiState.markDirty(uiState);
      renderStatus();
    }
  }

  async function loadRecoveryInstruction(stageId = currentStageId) {
    recoveryButton.disabled = true;
    if (!sessionId) return;
    try {
      const payload = await fetchJson(apiPath(`/stages/${stageId}/recovery`));
      uiState = UiState.receiveRecovery(uiState, stageId, payload.instruction);
      if (currentStageId === stageId && uiState.recoveryInstruction) {
        recoveryButton.disabled = false;
      }
    } catch (error) {
      if (currentStageId === stageId) recoveryButton.disabled = true;
    }
  }

  async function copyRecoveryInstruction() {
    if (!uiState.recoveryInstruction) await loadRecoveryInstruction(currentStageId);
    if (!uiState.recoveryInstruction) return;
    await navigator.clipboard.writeText(uiState.recoveryInstruction);
    recoveryButton.textContent = "已复制恢复指令";
    window.setTimeout(() => { recoveryButton.textContent = "复制恢复指令"; }, 1800);
  }

  async function pollStage() {
    if (document.hidden || !sessionId) return;
    const requestedStageId = currentStageId;
    try {
      const [stageState, sessionPayload] = await Promise.all([
        fetchJson(apiPath(`/stages/${requestedStageId}/status`)),
        fetchJson(apiPath()),
      ]);
      if (requestedStageId !== currentStageId) return;
      const priorRevision = revision;
      const priorStatus = uiState.serverStatus;
      revision = stageState.revision;
      revisionLabel.textContent = String(revision);
      const heartbeat = sessionPayload.session.last_agent_heartbeat;
      uiState = UiState.receiveStatus(uiState, stageState.status, heartbeat);
      renderStatus();
      const connection = UiState.connectionView(uiState, Date.now());
      connectionLabel.textContent = connection.connectionLabel;
      offlinePanel.hidden = connection.connectionLabel === "Agent 已连接";
      if (priorRevision !== revision || priorStatus !== stageState.status) await loadStage();
    } catch (error) {
      offlinePanel.hidden = false;
      connectionLabel.textContent = "Agent 状态暂不可用";
    }
  }

  function activateStage(stageId) {
    if (!stages.has(stageId)) return;
    stageGeneration += 1;
    currentStageId = stageId;
    uiState = UiState.switchStage(uiState, stageId);
    recoveryButton.disabled = true;
    revision = 0;
    revisionLabel.textContent = "0";
    railButtons.forEach((button) => {
      const active = button.dataset.stageId === stageId;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-current", active ? "step" : "false");
    });
    panels.forEach((panel) => {
      const active = panel.dataset.stagePanel === stageId;
      panel.classList.toggle("is-active", active);
      panel.hidden = !active;
    });
    const stage = stages.get(stageId);
    titleLabel.textContent = stage.title;
    currentStageLabel.textContent = stage.title;
    actionMessage.textContent = "填写完成后可保存草稿，或提交给 Agent。";
    renderStatus();
    renderSubmission();
    renderStageResult(stage.component);
    window.scrollTo({ top: 0, behavior: "smooth" });
    loadStage();
    if (sessionId) loadRecoveryInstruction(stageId);
  }

  railButtons.forEach((button) => {
    button.addEventListener("click", () => activateStage(button.dataset.stageId));
  });
  document.querySelector('[data-stage-form="setup"] [name="product_scope"]')
    ?.addEventListener("change", syncSetupProductScope);
  panels.forEach((panel) => {
    panel.addEventListener("input", () => {
      if (panel.dataset.stagePanel === currentStageId) {
        uiState = UiState.markDirty(uiState);
        renderStatus();
      }
    });
  });
  saveButton.addEventListener("click", () => persistStage("draft"));
  submitButton.addEventListener("click", () => persistStage("submit"));
  recoveryButton.addEventListener("click", copyRecoveryInstruction);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollStage();
  });

  setInterval(pollStage, 2000);
  syncSetupProductScope();
  activateStage(currentStageId);
})();
