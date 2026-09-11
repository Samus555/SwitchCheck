const form = document.querySelector("#compare-form");
const configInput = document.querySelector("#config");
const fileInput = document.querySelector("#config-file");
const fileName = document.querySelector("#file-name");
const lineCount = document.querySelector("#line-count");
const errorBox = document.querySelector("#error");
const resultsSection = document.querySelector("#results");
const resultBody = document.querySelector("#result-body");
const vlanResultBody = document.querySelector("#vlan-result-body");
const configDiffBody = document.querySelector("#config-diff-body");
const emptyResults = document.querySelector("#empty-results");
const submitButton = document.querySelector("#submit-button");
const resultSearch = document.querySelector("#result-search");

let comparisonData = null;
let activeFilter = "all";

function updateLineCount() {
  const count = configInput.value ? configInput.value.split("\n").length : 0;
  lineCount.textContent = `${count} ${count === 1 ? "line" : "lines"}`;
}

configInput.addEventListener("input", updateLineCount);
fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  configInput.value = await file.text();
  fileName.textContent = file.name;
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
  const rows = comparisonData.vlans.map((item) => {
    const row = document.createElement("tr");
    const aruba = item.aruba;
    const netbox = item.netbox;
    const differences = item.differences.map((difference) => difference.field).join(", ");
    row.append(
      cell(String(item.vid), "interface-name"),
      statusCell(item.status),
      cell(formatVlan(aruba)),
      cell(formatVlan(netbox)),
      cell(differences || (item.status === "match" ? "No drift detected" : "VLAN not present")),
    );
    return row;
  });
  vlanResultBody.replaceChildren(...rows);
  document.querySelector("#empty-vlans").hidden = rows.length !== 0;
}

function formatVlan(vlan) {
  if (!vlan) return "—";
  const name = vlan.name || "Unnamed";
  return vlan.description ? `${name} — ${vlan.description}` : name;
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
    const button = element("button", "details-button", "Details");
    button.type = "button";
    button.addEventListener("click", () => {
      const opening = detailRow.hidden;
      detailRow.hidden = !opening;
      button.textContent = opening ? "Hide" : "Details";
    });
    actionCell.append(button);
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

function differenceCell(item) {
  const td = document.createElement("td");
  const list = element("div", "diff-list");
  if (item.differences.length) {
    item.differences.forEach((difference) => {
      list.append(element("span", "diff-chip", difference.field));
    });
  } else if (item.status === "match") {
    list.append(element("span", "", "No drift detected"));
  } else {
    list.append(element("span", "", "Interface not present"));
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
  const fields = ["enabled", "description", "mode", "untagged_vlan", "tagged_vlans"];
  fields.forEach((field) => {
    const detail = element("div", "detail-item");
    detail.append(
      element("span", "", field.replaceAll("_", " ")),
      element("code", "", `Aruba: ${formatValue(item.aruba?.[field])}`),
      element("code", "", `NetBox: ${formatValue(item.netbox?.[field])}`),
    );
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
document.querySelector("#new-comparison").addEventListener("click", () => {
  resultsSection.hidden = true;
  window.scrollTo({ top: 0, behavior: "smooth" });
});
