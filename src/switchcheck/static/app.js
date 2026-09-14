const form = document.querySelector("#compare-form");
const configInput = document.querySelector("#config");
const fileInput = document.querySelector("#config-file");
const fileName = document.querySelector("#file-name");
const lineCount = document.querySelector("#line-count");
const errorBox = document.querySelector("#error");
const actionMessage = document.querySelector("#action-message");
const resultsSection = document.querySelector("#results");
const resultBody = document.querySelector("#result-body");
const vlanResultBody = document.querySelector("#vlan-result-body");
const configDiffBody = document.querySelector("#config-diff-body");
const emptyResults = document.querySelector("#empty-results");
const submitButton = document.querySelector("#submit-button");
const resultSearch = document.querySelector("#result-search");
const applySelectedButton = document.querySelector("#apply-selected");
const clearSelectionButton = document.querySelector("#clear-selection");
const previewSelectedButton = document.querySelector("#preview-selected");
const bulkAuditButton = document.querySelector("#bulk-audit");
const applyProgressDialog = document.querySelector("#apply-progress-dialog");
const applyProgress = document.querySelector("#apply-progress");
const applyProgressSummary = document.querySelector("#apply-progress-summary");
const applyProgressCount = document.querySelector("#apply-progress-count");
const applyProgressPercent = document.querySelector("#apply-progress-percent");
const applyProgressCurrent = document.querySelector("#apply-progress-current");
const applyProgressResults = document.querySelector("#apply-progress-results");
const closeApplyProgressButton = document.querySelector("#close-apply-progress");

let comparisonData = null;
let activeFilter = "all";
const selectedChanges = new Map();
let selectedFiles = [];

const preferenceFields = ["netbox-url", "device", "verify-tls", "ssh-host", "ssh-username"];
function loadPreferences() {
  preferenceFields.forEach((id) => {
    const input = document.querySelector(`#${id}`);
    const saved = localStorage.getItem(`switchcheck:${id}`);
    if (saved === null) return;
    if (input.type === "checkbox") input.checked = saved === "true";
    else input.value = saved;
  });
}
function savePreferences() {
  preferenceFields.forEach((id) => {
    const input = document.querySelector(`#${id}`);
    localStorage.setItem(`switchcheck:${id}`, input.type === "checkbox" ? input.checked : input.value);
  });
}
loadPreferences();

function showSetupStep(step) {
  document.querySelectorAll("[data-setup-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.setupPanel !== step;
    panel.classList.toggle("active", panel.dataset.setupPanel === step);
  });
  form.hidden = step === "results";
  document.querySelectorAll("[data-setup-step]").forEach((button) => {
    const steps = ["config", "connection", "results"];
    const buttonIndex = steps.indexOf(button.dataset.setupStep);
    const currentIndex = steps.indexOf(step);
    button.classList.toggle("active", button.dataset.setupStep === step);
    button.classList.toggle("complete", buttonIndex < currentIndex);
    button.toggleAttribute("aria-current", button.dataset.setupStep === step);
  });
}

function showResultTab(tabName) {
  document.querySelectorAll("[data-result-tab]").forEach((tab) => {
    const active = tab.dataset.resultTab === tabName;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll("[data-result-panel]").forEach((panel) => {
    const active = panel.dataset.resultPanel === tabName;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
}

function updateLineCount() {
  const count = configInput.value ? configInput.value.split("\n").length : 0;
  lineCount.textContent = `${count} ${count === 1 ? "line" : "lines"}`;
}

configInput.addEventListener("input", updateLineCount);
fileInput.addEventListener("change", async () => {
  selectedFiles = [...fileInput.files];
  const file = selectedFiles[0];
  if (!file) return;
  configInput.value = await file.text();
  fileName.textContent = file.name;
  bulkAuditButton.hidden = selectedFiles.length < 2;
  updateLineCount();
});

document.querySelector("#toggle-token").addEventListener("click", (event) => {
  const tokenInput = document.querySelector("#token");
  const visible = tokenInput.type === "text";
  tokenInput.type = visible ? "password" : "text";
  event.currentTarget.setAttribute("aria-label", visible ? "Show token" : "Hide token");
});

document.querySelector("#continue-to-connection").addEventListener("click", () => {
  if (!configInput.reportValidity()) return;
  showError("");
  showSetupStep("connection");
  document.querySelector("#netbox-url").focus();
});

document.querySelector("#back-to-config").addEventListener("click", () => {
  showSetupStep("config");
  configInput.focus();
});

document.querySelectorAll("[data-setup-step]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.disabled) return;
    const step = button.dataset.setupStep;
    if (step === "results" && !comparisonData) return;
    showSetupStep(step);
    resultsSection.hidden = step !== "results";
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setLoading(true);
  showError("");

  const payload = {
    config: configInput.value,
    netbox_url: document.querySelector("#netbox-url").value,
    token: document.querySelector("#token").value,
    device: document.querySelector("#device").value,
    verify_tls: document.querySelector("#verify-tls").checked,
  };
  savePreferences();

  try {
    const response = await fetch("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => item.msg).join("; ")
        : data.detail;
      throw new Error(detail || "The comparison could not be completed.");
    }
    comparisonData = data;
    selectedChanges.clear();
    updateSelectionToolbar();
    activeFilter = "all";
    document.querySelectorAll(".filter").forEach((button) => {
      button.classList.toggle("active", button.dataset.filter === "all");
    });
    resultSearch.value = "";
    renderSummary(data.summary);
    renderSummary(data.vlan_summary, "#vlan-summary");
    renderResults();
    renderVlans();
    renderConfigDiff();
    renderRemediation();
    document.querySelector("#interface-tab-count").textContent = String(data.summary.total);
    document.querySelector("#vlan-tab-count").textContent = String(data.vlan_summary.total);
    document.querySelector('[data-setup-step="results"]').disabled = false;
    showResultTab("interfaces");
    showSetupStep("results");
    resultsSection.hidden = false;
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showError(error.message || "The comparison could not be completed.");
  } finally {
    setLoading(false);
  }
});

function setLoading(loading) {
  submitButton.disabled = loading;
  submitButton.querySelector("span").textContent = loading ? "Comparing…" : "Run comparison";
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

function actionButton(label, resource, source, fields, create = false) {
  const button = element("button", "button button-secondary action-button", label);
  button.type = "button";
  button.addEventListener("click", () => importToNetBox(button, resource, source, fields, create));
  return button;
}

function selectionControl(resource, source, fields, create = false) {
  const identifier = resource === "interface" ? source.name : source.vid;
  const action = changeAction(resource, source, fields, create);
  const key = changeKey(action);
  const label = element("label", "selection-control");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.dataset.changeKey = key;
  checkbox.checked = selectedChanges.has(key);
  checkbox.setAttribute(
    "aria-label",
    create ? `Select adding ${resource} ${identifier}` : `Select importing ${fields.join(", ")}`,
  );
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) {
      selectedChanges.set(key, action);
    } else {
      selectedChanges.delete(key);
    }
    syncSelectionControls();
    updateSelectionToolbar();
  });
  label.append(checkbox, element("span", "", "Select"));
  return label;
}

function changeAction(resource, source, fields, create = false) {
  return { resource, create, fields, [resource]: source };
}

function changeKey(action) {
  const source = action[action.resource];
  const identifier = action.resource === "interface" ? source.name : source.vid;
  return `${action.resource}:${action.create}:${identifier}:${[...action.fields].sort().join(",")}`;
}

function proposedActions(resource, item) {
  const source = item.aruba;
  if (!source || item.status === "match" || item.status === "only_netbox") return [];
  if (item.status === "only_aruba") return [changeAction(resource, source, [], true)];
  return item.differences.map((difference) =>
    changeAction(resource, source, [difference.field.replaceAll(" ", "_")]),
  );
}

function selectAllControl(resource, item) {
  const actions = proposedActions(resource, item);
  if (!actions.length) return null;
  const label = element("label", "selection-control select-all-control");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.dataset.changeKeys = actions.map(changeKey).join("|");
  checkbox.setAttribute(
    "aria-label",
    `Select all proposed changes for ${resource} ${resource === "interface" ? item.name : item.vid}`,
  );
  checkbox.addEventListener("change", () => {
    actions.forEach((action) => {
      const key = changeKey(action);
      if (checkbox.checked) selectedChanges.set(key, action);
      else selectedChanges.delete(key);
    });
    syncSelectionControls();
    updateSelectionToolbar();
  });
  label.append(checkbox, element("span", "", "Select all changes"));
  return label;
}

function syncSelectionControls() {
  document.querySelectorAll("[data-change-key]").forEach((checkbox) => {
    checkbox.checked = selectedChanges.has(checkbox.dataset.changeKey);
  });
  document.querySelectorAll("[data-change-keys]").forEach((checkbox) => {
    const keys = checkbox.dataset.changeKeys.split("|").filter(Boolean);
    const selected = keys.filter((key) => selectedChanges.has(key)).length;
    checkbox.checked = selected === keys.length;
    checkbox.indeterminate = selected > 0 && selected < keys.length;
  });
}

function updateSelectionToolbar() {
  const count = selectedChanges.size;
  document.querySelector("#selected-count").textContent = String(count);
  applySelectedButton.disabled = count === 0;
  clearSelectionButton.disabled = count === 0;
  previewSelectedButton.disabled = count === 0;
}

function connectionPayload() {
  return {
    netbox_url: document.querySelector("#netbox-url").value,
    token: document.querySelector("#token").value,
    verify_tls: document.querySelector("#verify-tls").checked,
  };
}

async function previewSelectedChanges() {
  actionMessage.hidden = true;
  try {
    const response = await fetch("/api/netbox/change-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...connectionPayload(),
        device: document.querySelector("#device").value,
        actions: [...selectedChanges.values()],
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The change plan could not be generated.");
    actionMessage.textContent = data.actions
      .map((item, index) => `${index + 1}. ${item.operation} ${item.resource} ${item.identifier}\n${JSON.stringify(item.changes, null, 2)}`)
      .join("\n\n");
    actionMessage.classList.remove("action-error");
    actionMessage.style.whiteSpace = "pre-wrap";
    actionMessage.hidden = false;
  } catch (error) {
    showActionError(error.message);
  }
}

async function importToNetBox(button, resource, source, fields, create) {
  const operation = create ? `add this ${resource}` : `import ${fields.join(", ")}`;
  if (!window.confirm(`Use the Aruba values to ${operation} in NetBox?`)) return;

  button.disabled = true;
  actionMessage.hidden = true;
  const payload = {
    netbox_url: document.querySelector("#netbox-url").value,
    token: document.querySelector("#token").value,
    device: document.querySelector("#device").value,
    verify_tls: document.querySelector("#verify-tls").checked,
    resource,
    create,
    fields,
    [resource]: source,
  };

  try {
    const response = await fetch("/api/netbox/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The NetBox update failed.");
    actionMessage.textContent = data.message;
    actionMessage.classList.remove("action-error");
    actionMessage.hidden = false;
    form.requestSubmit();
  } catch (error) {
    actionMessage.textContent = error.message || "The NetBox update failed.";
    actionMessage.classList.add("action-error");
    actionMessage.hidden = false;
  } finally {
    button.disabled = false;
  }
}

async function applySelectedChanges() {
  const actions = [...selectedChanges.values()].sort(
    (left, right) => importPriority(left) - importPriority(right),
  );
  if (!actions.length) return;
  if (!window.confirm(`Apply ${actions.length} selected changes to NetBox?`)) return;

  applySelectedButton.disabled = true;
  applySelectedButton.textContent = "Applying…";
  actionMessage.hidden = true;
  openApplyProgress(actions.length);
  const results = [];
  try {
    for (const [index, action] of actions.entries()) {
      const identifier = action.resource === "interface" ? action.interface.name : action.vlan.vid;
      applyProgressCurrent.textContent =
        `Applying ${action.resource} ${identifier} (${index + 1} of ${actions.length})…`;
      const response = await fetch("/api/netbox/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...connectionPayload(),
          device: document.querySelector("#device").value,
          ...action,
        }),
      });
      const data = await response.json();
      results.push({
        success: response.ok,
        message: response.ok ? data.message : data.detail || `Updating ${action.resource} ${identifier} failed.`,
      });
      updateApplyProgress(index + 1, actions.length);
    }
    const failures = results.filter((result) => !result.success);
    const applied = results.length - failures.length;
    actionMessage.textContent = failures.length
      ? `${applied} applied, ${failures.length} failed: ${failures.map((item) => item.message).join("; ")}`
      : `${applied} selected changes were applied to NetBox.`;
    actionMessage.classList.toggle("action-error", failures.length > 0);
    actionMessage.hidden = false;
    finishApplyProgress(applied, failures);
    selectedChanges.clear();
    syncSelectionControls();
    updateSelectionToolbar();
  } catch (error) {
    actionMessage.textContent = error.message || "The NetBox batch update failed.";
    actionMessage.classList.add("action-error");
    actionMessage.hidden = false;
    finishApplyProgress(results.filter((result) => result.success).length, [
      ...results.filter((result) => !result.success),
      { message: actionMessage.textContent },
    ]);
  } finally {
    applySelectedButton.textContent = "Apply selected";
    updateSelectionToolbar();
  }
}

function importPriority(action) {
  if (action.create && action.resource === "vlan") return 0;
  if (action.create && action.interface) {
    return /^(lag|trk)/.test(action.interface.name.replaceAll(" ", "").toLowerCase()) ? 1 : 2;
  }
  if (action.resource === "vlan") return 3;
  if (action.fields.includes("mode")) return 4;
  if (action.fields.some((field) => ["untagged_vlan", "tagged_vlans"].includes(field))) return 6;
  return 5;
}

function openApplyProgress(total) {
  applyProgress.max = total;
  applyProgress.value = 0;
  applyProgressSummary.textContent = `Applying ${total} selected ${total === 1 ? "change" : "changes"} to NetBox.`;
  applyProgressCount.textContent = `0 of ${total} complete`;
  applyProgressPercent.textContent = "0%";
  applyProgressCurrent.textContent = "Connecting to NetBox…";
  applyProgressResults.hidden = true;
  applyProgressResults.replaceChildren();
  closeApplyProgressButton.hidden = true;
  applyProgressDialog.showModal();
}

function updateApplyProgress(completed, total) {
  const percent = Math.round((completed / total) * 100);
  applyProgress.value = completed;
  applyProgressCount.textContent = `${completed} of ${total} complete`;
  applyProgressPercent.textContent = `${percent}%`;
}

function finishApplyProgress(applied, failures) {
  applyProgressCurrent.textContent = failures.length ? "Completed with errors." : "All changes applied.";
  applyProgressResults.textContent = failures.length
    ? `${applied} applied · ${failures.length} failed\n${failures.map((item) => item.message).join("\n")}`
    : `${applied} ${applied === 1 ? "change" : "changes"} applied successfully.`;
  applyProgressResults.classList.toggle("has-errors", failures.length > 0);
  applyProgressResults.hidden = false;
  closeApplyProgressButton.hidden = false;
  closeApplyProgressButton.focus();
}

function renderSummary(summary, selector = "#summary") {
  const items = [
    ["Total", summary.total, ""],
    ["Matches", summary.matches, "match"],
    ["Differences", summary.differences, "different"],
    ["Only Aruba", summary.only_aruba, "missing"],
    ["Only NetBox", summary.only_netbox, "missing"],
  ];
  const container = document.querySelector(selector);
  container.replaceChildren(
    ...items.map(([label, value, className]) => {
      const card = element("div", `summary-card ${className}`);
      card.append(element("span", "", label), element("strong", "", String(value)));
      return card;
    }),
  );
}

function renderVlans() {
  const rows = [];
  comparisonData.vlans.forEach((item) => {
    const row = document.createElement("tr");
    const aruba = item.aruba;
    const netbox = item.netbox;
    const actions = document.createElement("td");
    const detailRow = vlanDetailRowFor(item);
    if (item.status === "only_aruba") {
      actions.append(actionButton("Add to NetBox", "vlan", aruba, [], true));
    }
    const selectAll = selectAllControl("vlan", item);
    if (selectAll) actions.prepend(selectAll);
    const detailButton = element("button", "details-button", "Details");
    detailButton.type = "button";
    detailButton.addEventListener("click", () => {
      const opening = detailRow.hidden;
      detailRow.hidden = !opening;
      detailButton.textContent = opening ? "Hide" : "Details";
    });
    actions.append(detailButton);
    row.append(
      cell(String(item.vid), "interface-name"),
      statusCell(item.status),
      cell(formatVlan(aruba)),
      cell(formatVlan(netbox)),
      differenceCell(item, "VLAN not present"),
      actions,
    );
    rows.push(row, detailRow);
  });
  vlanResultBody.replaceChildren(...rows);
  document.querySelector("#empty-vlans").hidden = comparisonData.vlans.length !== 0;
}

function formatVlan(vlan) {
  if (!vlan) return "—";
  const name = vlan.name || "Unnamed";
  return vlan.description ? `${name} — ${vlan.description}` : name;
}

function vlanDetailRowFor(item) {
  const row = element("tr", "detail-row");
  row.hidden = true;
  const td = document.createElement("td");
  td.colSpan = 6;
  const panel = element("div", "detail-panel");
  ["name", "description"].forEach((field) => {
    const detail = element("div", "detail-item");
    detail.append(
      element("span", "", field),
      element("code", "", `Aruba: ${formatValue(item.aruba?.[field])}`),
      element("code", "", `NetBox: ${formatValue(item.netbox?.[field])}`),
    );
    const isDifferent = item.differences.some((difference) => difference.field === field);
    if (isDifferent && item.aruba && item.netbox) {
      const actions = element("div", "field-actions");
      actions.append(
        selectionControl("vlan", item.aruba, [field]),
        actionButton("Import", "vlan", item.aruba, [field]),
      );
      detail.append(actions);
    }
    panel.append(detail);
  });
  td.append(panel);
  row.append(td);
  return row;
}

function renderConfigDiff() {
  const comparison = comparisonData.config;
  const unavailable = document.querySelector("#config-diff-unavailable");
  const content = document.querySelector("#config-diff-content");
  if (!comparison.available) {
    unavailable.textContent = comparison.message;
    unavailable.hidden = false;
    content.hidden = true;
    return;
  }

  unavailable.hidden = true;
  content.hidden = false;
  const summary = comparison.summary;
  const summaryItems = [
    ["Unchanged", summary.unchanged, "match"],
    ["Changed", summary.changed, "different"],
    ["Current only", summary.current_only, "removed"],
    ["Rendered only", summary.rendered_only, "added"],
  ];
  document.querySelector("#config-summary").replaceChildren(
    ...summaryItems.map(([label, value, className]) => {
      const item = element("span", `config-stat ${className}`);
      item.append(element("strong", "", String(value)), document.createTextNode(` ${label}`));
      return item;
    }),
  );

  const rows = comparison.lines.map((line) => {
    const row = element("tr", `diff-line ${line.status}`);
    row.append(
      cell(line.current_number ? String(line.current_number) : "", "line-number"),
      cell(line.current_text ?? "", "line-code current-code"),
      cell(line.rendered_number ? String(line.rendered_number) : "", "line-number"),
      cell(line.rendered_text ?? "", "line-code rendered-code"),
    );
    return row;
  });
  configDiffBody.replaceChildren(...rows);
}

function renderRemediation() {
  const commands = comparisonData.remediation_commands || [];
  document.querySelector("#remediation-commands").textContent =
    commands.join("\n") || "No switch-side remediation is required.";
}

function renderResults() {
  if (!comparisonData) return;
  const query = resultSearch.value.trim().toLowerCase();
  const filtered = comparisonData.interfaces.filter((item) => {
    const isMissing = item.status === "only_aruba" || item.status === "only_netbox";
    const filterMatch =
      activeFilter === "all" ||
      item.status === activeFilter ||
      (activeFilter === "missing" && isMissing);
    return filterMatch && item.name.toLowerCase().includes(query);
  });

  const rows = [];
  for (const item of filtered) {
    const row = document.createElement("tr");
    row.append(
      cell(item.name, "interface-name"),
      statusCell(item.status),
      differenceCell(item),
    );

    const actionCell = document.createElement("td");
    const detailRow = detailRowFor(item);
    const detailButton = element("button", "details-button", "Details");
    detailButton.type = "button";
    detailButton.addEventListener("click", () => {
      const opening = detailRow.hidden;
      detailRow.hidden = !opening;
      detailButton.textContent = opening ? "Hide" : "Details";
    });
    if (item.status === "only_aruba") {
      actionCell.append(actionButton("Add to NetBox", "interface", item.aruba, [], true));
    }
    const selectAll = selectAllControl("interface", item);
    if (selectAll) actionCell.prepend(selectAll);
    actionCell.append(detailButton);
    row.append(actionCell);
    rows.push(row, detailRow);
  }

  resultBody.replaceChildren(...rows);
  emptyResults.hidden = filtered.length !== 0;
}

function statusCell(status) {
  const labels = {
    match: "Match",
    different: "Different",
    only_aruba: "Only Aruba",
    only_netbox: "Only NetBox",
  };
  const td = document.createElement("td");
  td.append(element("span", `badge ${status}`, labels[status]));
  return td;
}

function differenceCell(item, missingLabel = "Interface not present") {
  const td = document.createElement("td");
  const list = element("div", "diff-list");
  if (item.differences.length) {
    item.differences.forEach((difference) => {
      list.append(element("span", "diff-chip", difference.field));
    });
  } else if (item.status !== "match") {
    list.append(element("span", "", missingLabel));
  }
  td.append(list);
  return td;
}

function detailRowFor(item) {
  const row = element("tr", "detail-row");
  row.hidden = true;
  const td = document.createElement("td");
  td.colSpan = 4;
  const panel = element("div", "detail-panel");
  const fields = [
    "enabled", "description", "mode", "untagged_vlan", "tagged_vlans", "lag",
    "mtu", "speed", "duplex", "type", "mac_address", "mgmt_only", "custom_fields",
  ];
  fields.forEach((field) => {
    const detail = element("div", "detail-item");
    detail.append(
      element("span", "", field.replaceAll("_", " ")),
      element("code", "", `Aruba: ${formatValue(item.aruba?.[field])}`),
      element("code", "", `NetBox: ${formatValue(item.netbox?.[field])}`),
    );
    const isDifferent = item.differences.some(
      (difference) => difference.field.replaceAll(" ", "_") === field,
    );
    if (isDifferent && item.aruba && item.netbox) {
      const actions = element("div", "field-actions");
      actions.append(
        selectionControl("interface", item.aruba, [field]),
        actionButton("Import", "interface", item.aruba, [field]),
      );
      detail.append(actions);
    }
    panel.append(detail);
  });
  td.append(panel);
  row.append(td);
  return row;
}

function formatValue(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  return String(value);
}

function cell(text, className = "") {
  const td = document.createElement("td");
  td.className = className;
  td.textContent = text;
  return td;
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  node.className = className.trim();
  node.textContent = text;
  return node;
}

document.querySelector("#filters").addEventListener("click", (event) => {
  const button = event.target.closest(".filter");
  if (!button) return;
  activeFilter = button.dataset.filter;
  document.querySelectorAll(".filter").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  renderResults();
});

document.querySelector(".result-tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-result-tab]");
  if (tab) showResultTab(tab.dataset.resultTab);
});
document.querySelector(".result-tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  const tabs = [...document.querySelectorAll("[data-result-tab]")];
  const currentIndex = tabs.indexOf(document.activeElement);
  if (currentIndex < 0) return;
  event.preventDefault();
  const direction = event.key === "ArrowRight" ? 1 : -1;
  const nextTab = tabs[(currentIndex + direction + tabs.length) % tabs.length];
  showResultTab(nextTab.dataset.resultTab);
  nextTab.focus();
});

resultSearch.addEventListener("input", renderResults);
applySelectedButton.addEventListener("click", applySelectedChanges);
previewSelectedButton.addEventListener("click", previewSelectedChanges);
clearSelectionButton.addEventListener("click", () => {
  selectedChanges.clear();
  syncSelectionControls();
  updateSelectionToolbar();
});
closeApplyProgressButton.addEventListener("click", () => {
  applyProgressDialog.close();
  form.requestSubmit();
});
document.querySelector("#new-comparison").addEventListener("click", () => {
  resultsSection.hidden = true;
  showSetupStep("config");
  window.scrollTo({ top: 0, behavior: "smooth" });
});

function showActionError(message) {
  actionMessage.textContent = message || "The operation failed.";
  actionMessage.classList.add("action-error");
  actionMessage.hidden = false;
}

document.querySelector("#discover-devices").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/netbox/devices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...connectionPayload(), query: document.querySelector("#device").value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Device discovery failed.");
    document.querySelector("#device-options").replaceChildren(
      ...data.map((device) => {
        const option = document.createElement("option");
        option.value = device.name;
        option.label = [device.site, device.status].filter(Boolean).join(" · ");
        return option;
      }),
    );
    actionMessage.textContent = `${data.length} NetBox device${data.length === 1 ? "" : "s"} found.`;
    actionMessage.classList.remove("action-error");
    actionMessage.hidden = false;
  } catch (error) {
    showActionError(error.message);
  }
});

document.querySelector("#fetch-config").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/config/ssh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host: document.querySelector("#ssh-host").value,
        username: document.querySelector("#ssh-username").value,
        password: document.querySelector("#ssh-password").value || null,
        private_key: document.querySelector("#ssh-private-key").value || null,
        known_hosts: document.querySelector("#ssh-known-hosts").value || null,
        command: document.querySelector("#ssh-command").value,
        sftp_path: document.querySelector("#sftp-path").value || null,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Configuration retrieval failed.");
    configInput.value = data.config;
    fileName.textContent = `${document.querySelector("#ssh-host").value}: running config`;
    updateLineCount();
    savePreferences();
  } catch (error) {
    showActionError(error.message);
  }
});

bulkAuditButton.addEventListener("click", async () => {
  try {
    const audits = await Promise.all(
      selectedFiles.map(async (file) => ({
        device: file.name.replace(/\.(txt|cfg|conf)$/i, ""),
        config: await file.text(),
      })),
    );
    const response = await fetch("/api/compare-bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...connectionPayload(), audits }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Bulk audit failed.");
    comparisonData = {
      summary: {
        total: data.total,
        matches: data.successful - data.devices_with_drift,
        differences: data.devices_with_drift,
        only_aruba: 0,
        only_netbox: data.failed,
      },
      vlan_summary: { total: 0, matches: 0, differences: 0, only_aruba: 0, only_netbox: 0 },
      interfaces: data.results.map((item) => ({
        name: item.device,
        status: !item.success ? "only_netbox" : item.comparison.summary.differences ||
          item.comparison.summary.only_aruba || item.comparison.summary.only_netbox ? "different" : "match",
        differences: item.error ? [{ field: item.error }] : [],
      })),
      vlans: [],
      config: { available: false, message: "Configuration diff is available in individual audits." },
      remediation_commands: data.results.flatMap((item) => item.comparison?.remediation_commands || []),
      bulk: data,
    };
    renderSummary(comparisonData.summary);
    renderSummary(comparisonData.vlan_summary, "#vlan-summary");
    renderResults();
    renderVlans();
    renderConfigDiff();
    renderRemediation();
    document.querySelector("#interface-tab-count").textContent = String(data.total);
    document.querySelector("#vlan-tab-count").textContent = "0";
    document.querySelector('[data-setup-step="results"]').disabled = false;
    showResultTab("interfaces");
    showSetupStep("results");
    resultsSection.hidden = false;
    resultsSection.scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    showActionError(error.message);
  }
});

document.querySelectorAll(".export-report").forEach((button) => {
  button.addEventListener("click", () => exportReport(button.dataset.format));
});

function exportReport(format) {
  if (!comparisonData) return;
  let content;
  let type;
  if (format === "json") {
    content = JSON.stringify(comparisonData, null, 2);
    type = "application/json";
  } else if (format === "csv") {
    const rows = [["resource", "identifier", "status", "differences"]];
    comparisonData.interfaces.forEach((item) =>
      rows.push(["interface", item.name, item.status, item.differences.map((diff) => diff.field).join("; ")]));
    comparisonData.vlans.forEach((item) =>
      rows.push(["vlan", item.vid, item.status, item.differences.map((diff) => diff.field).join("; ")]));
    content = rows.map((row) => row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\n");
    type = "text/csv";
  } else {
    const escape = (value) => String(value).replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[char]);
    const rows = [...comparisonData.interfaces.map((item) => ["Interface", item.name, item.status]),
      ...comparisonData.vlans.map((item) => ["VLAN", item.vid, item.status])];
    content = `<!doctype html><title>SwitchCheck report</title><h1>SwitchCheck report</h1><table border="1"><tr><th>Resource</th><th>Name</th><th>Status</th></tr>${rows.map((row) => `<tr>${row.map((cell) => `<td>${escape(cell)}</td>`).join("")}</tr>`).join("")}</table>`;
    type = "text/html";
  }
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([content], { type }));
  link.download = `switchcheck-report.${format}`;
  link.click();
  URL.revokeObjectURL(link.href);
}

document.querySelector("#copy-remediation").addEventListener("click", async () => {
  await navigator.clipboard.writeText((comparisonData?.remediation_commands || []).join("\n"));
});
