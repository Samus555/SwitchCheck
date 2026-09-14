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
  const key = `${resource}:${create}:${identifier}:${[...fields].sort().join(",")}`;
  const label = element("label", "selection-control");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = selectedChanges.has(key);
  checkbox.setAttribute(
    "aria-label",
    create ? `Select adding ${resource} ${identifier}` : `Select importing ${fields.join(", ")}`,
  );
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) {
      selectedChanges.set(key, {
        resource,
        create,
        fields,
        [resource]: source,
      });
    } else {
      selectedChanges.delete(key);
    }
    updateSelectionToolbar();
  });
  label.append(checkbox, element("span", "", "Select"));
  return label;
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
      .map((item, index) => `${index + 1}. ${item.operation} ${item.resource} ${item.identifier}\n${item.summary}`)
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
  const actions = [...selectedChanges.values()];
  if (!actions.length) return;
  if (!window.confirm(`Apply ${actions.length} selected changes to NetBox?`)) return;

  applySelectedButton.disabled = true;
  applySelectedButton.textContent = "Applying…";
  actionMessage.hidden = true;
  try {
    const response = await fetch("/api/netbox/import-batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        netbox_url: document.querySelector("#netbox-url").value,
        token: document.querySelector("#token").value,
        device: document.querySelector("#device").value,
        verify_tls: document.querySelector("#verify-tls").checked,
        actions,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The NetBox batch update failed.");
    const failures = data.results.filter((result) => !result.success);
    actionMessage.textContent = failures.length
      ? `${data.applied} applied, ${data.failed} failed: ${failures.map((item) => item.message).join("; ")}`
      : `${data.applied} selected changes were applied to NetBox.`;
    actionMessage.classList.toggle("action-error", failures.length > 0);
    actionMessage.hidden = false;
    selectedChanges.clear();
    updateSelectionToolbar();
    form.requestSubmit();
  } catch (error) {
    actionMessage.textContent = error.message || "The NetBox batch update failed.";
    actionMessage.classList.add("action-error");
    actionMessage.hidden = false;
  } finally {
    applySelectedButton.textContent = "Apply selected";
    updateSelectionToolbar();
  }
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
      actions.append(selectionControl("vlan", aruba, [], true));
      actions.append(actionButton("Add to NetBox", "vlan", aruba, [], true));
    }
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
      actionCell.append(selectionControl("interface", item.aruba, [], true));
      actionCell.append(actionButton("Add to NetBox", "interface", item.aruba, [], true));
    }
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

resultSearch.addEventListener("input", renderResults);
applySelectedButton.addEventListener("click", applySelectedChanges);
previewSelectedButton.addEventListener("click", previewSelectedChanges);
clearSelectionButton.addEventListener("click", () => {
  selectedChanges.clear();
  document.querySelectorAll(".selection-control input").forEach((checkbox) => {
    checkbox.checked = false;
  });
  updateSelectionToolbar();
});
document.querySelector("#new-comparison").addEventListener("click", () => {
  resultsSection.hidden = true;
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
