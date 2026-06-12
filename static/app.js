async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

function escapeHtml(str) {
  return (str ?? "").toString()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function vitalsToBadges(vitals) {
  const entries = Object.entries(vitals || {});
  if (entries.length === 0) return "";
  return entries.map(([k, v]) => `<span class="badge text-bg-secondary me-1">${escapeHtml(k.toUpperCase())}: ${escapeHtml(v)}</span>`).join("");
}

function renderAlert(kind, msg) {
  return `
    <div class="alert alert-${kind} alert-dismissible fade show" role="alert">
      ${msg}
      <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    </div>
  `;
}

function renderCard(item, patient) {
  const who = item.reported_by
    ? `Reportó: <span class="fw-semibold">${escapeHtml(item.reported_by)}</span>`
    : `Publicado por: <span class="fw-semibold">${escapeHtml(item.posted_by)}</span>`;

  const shift = `${escapeHtml(item.shift_label)}${item.unit ? ` · ${escapeHtml(item.unit)}` : ""}`;

  return `
    <div class="card shadow-sm">
      <div class="card-body">
        <div class="d-flex flex-wrap justify-content-between gap-2 align-items-baseline">
          <div>
            <div class="h5 mb-1">${escapeHtml(patient)}</div>
            <div class="text-muted">${escapeHtml(item.sent_at)} · ${escapeHtml(item.shift.toUpperCase())} · ${shift}</div>
          </div>
          <div class="text-muted small">${who}</div>
        </div>

        <div class="mt-3">${vitalsToBadges(item.vitals)}</div>
        <div class="mt-2">${escapeHtml(item.note || "(sin detalle)")}</div>
      </div>
    </div>
  `;
}

/* -------------------------
   Typeahead (sugerencias)
-------------------------- */
let lastSuggestions = [];
let activeIndex = -1;

function showSuggestions(container) {
  container.classList.remove("d-none");
}

function hideSuggestions(container) {
  container.classList.add("d-none");
  container.innerHTML = "";
  lastSuggestions = [];
  activeIndex = -1;
}

function renderSuggestions(container, suggestions) {
  container.innerHTML = "";
  suggestions.forEach((name, idx) => {
    const a = document.createElement("a");
    a.href = "#";
    a.className = "list-group-item list-group-item-action typeahead-item";
    a.setAttribute("role", "option");
    a.dataset.index = String(idx);
    a.innerHTML = escapeHtml(name);
    container.appendChild(a);
  });
}

function setActive(container, idx) {
  activeIndex = idx;
  const items = Array.from(container.querySelectorAll(".typeahead-item"));
  items.forEach((el, i) => {
    el.classList.toggle("active", i === idx);
  });

  // ensure visible
  if (idx >= 0 && idx < items.length) {
    items[idx].scrollIntoView({ block: "nearest" });
  }
}

async function refreshSuggestions(q, container) {
  const query = (q || "").trim();

  // Mostrar desde 1 letra
  if (!query || query.length < 1) {
    hideSuggestions(container);
    return;
  }

  const data = await fetchJSON(`/api/patients?query=${encodeURIComponent(query)}&limit=10`);
  const patients = data.patients || [];
  lastSuggestions = patients;
  activeIndex = -1;

  if (patients.length === 0) {
    hideSuggestions(container);
    return;
  }

  renderSuggestions(container, patients);
  showSuggestions(container);
}

function pickSuggestion(input, container, idx, runSearchFn) {
  if (idx < 0 || idx >= lastSuggestions.length) return;
  input.value = lastSuggestions[idx];
  hideSuggestions(container);
  // opcional: ejecutar búsqueda inmediatamente
  runSearchFn();
}

/* -------------------------
   Buscar partes
-------------------------- */
async function runSearch() {
  const patient = document.getElementById("patientInput").value.trim();
  const limit = document.getElementById("limitSelect").value;
  const daysBack = document.getElementById("daysBackSelect").value;

  const alerts = document.getElementById("alerts");
  const results = document.getElementById("results");
  alerts.innerHTML = "";
  results.innerHTML = "";

  if (!patient) {
    alerts.innerHTML = renderAlert("warning", "Escribí un paciente para buscar.");
    return;
  }

  const url = `/api/reports?patient=${encodeURIComponent(patient)}&limit=${encodeURIComponent(limit)}&days_back=${encodeURIComponent(daysBack)}`;
  const data = await fetchJSON(url);

  if (!data.found) {
    const sug = (data.suggestions || []).length
      ? `<div class="mt-2">Sugerencias: ${(data.suggestions || []).map(s => `<button class="btn btn-sm btn-outline-secondary me-1 suggestion-btn" data-patient="${escapeHtml(s)}">${escapeHtml(s)}</button>`).join("")}</div>`
      : "";
    alerts.innerHTML = renderAlert("danger", `${escapeHtml(data.message || "No encontrado.")}${sug}`);
    return;
  }

  (data.items || []).forEach(item => {
    results.insertAdjacentHTML("beforeend", renderCard(item, data.patient || patient));
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("patientInput");
  const btn = document.getElementById("searchBtn");
  const sugg = document.getElementById("patientSuggestions");
  const alerts = document.getElementById("alerts");

  // Click en sugerencia de alerta (cuando es ambiguo)
  alerts.addEventListener("click", (e) => {
    const sugBtn = e.target.closest(".suggestion-btn");
    if (sugBtn) {
      input.value = sugBtn.dataset.patient;
      runSearch();
    }
  });

  let t = null;
  input.addEventListener("input", () => {
    clearTimeout(t);
    t = setTimeout(() => refreshSuggestions(input.value, sugg), 120);
  });

  // Click en sugerencia
  sugg.addEventListener("mousedown", (e) => {
    // mousedown para que no se pierda el foco antes de seleccionar
    const item = e.target.closest(".typeahead-item");
    if (!item) return;
    e.preventDefault();
    const idx = Number(item.dataset.index);
    pickSuggestion(input, sugg, idx, runSearch);
  });

  // Navegación teclado
  input.addEventListener("keydown", (e) => {
    const visible = !sugg.classList.contains("d-none");
    if (e.key === "ArrowDown" && visible) {
      e.preventDefault();
      const next = Math.min(activeIndex + 1, lastSuggestions.length - 1);
      setActive(sugg, next);
      return;
    }
    if (e.key === "ArrowUp" && visible) {
      e.preventDefault();
      const prev = Math.max(activeIndex - 1, 0);
      setActive(sugg, prev);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (visible && activeIndex >= 0) {
        pickSuggestion(input, sugg, activeIndex, runSearch);
      } else {
        hideSuggestions(sugg);
        runSearch();
      }
      return;
    }
    if (e.key === "Escape" && visible) {
      e.preventDefault();
      hideSuggestions(sugg);
    }
  });

  // Ocultar al hacer click afuera
  document.addEventListener("click", (e) => {
    if (e.target === input || sugg.contains(e.target)) return;
    hideSuggestions(sugg);
  });

  // Botón buscar
  btn.addEventListener("click", () => {
    hideSuggestions(sugg);
    runSearch();
  });
});
