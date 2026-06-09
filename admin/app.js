const state = {
  apiBase: localStorage.getItem("skud.apiBase") || window.location.origin,
  apiKey: localStorage.getItem("skud.apiKey") || "",
  company: null,
  employees: [],
  rooms: [],
  rules: [],
  scanners: [],
  events: [],
  occupancy: [],
  throughput: [],
  officeTime: [],
};

const titles = {
  dashboard: ["Обзор", "Состояние офиса, проходы и текущая занятость помещений."],
  employees: ["Сотрудники", "Создание, статусы, QR-пропуска и фотографии для распознавания."],
  rooms: ["Помещения", "Кабинеты, переговорные, зоны и текущая занятость."],
  access: ["Доступы", "Связь сотрудников с помещениями и доступными методами прохода."],
  scanners: ["Сканеры", "Точки входа и выхода с QR и Face ID."],
  events: ["Журнал", "Все решения системы по проходам через помещения."],
  setup: ["Настройка", "Первичное создание компании и подключение админского ключа."],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function init() {
  $("#apiBaseInput").value = state.apiBase;
  $("#apiKeyInput").value = state.apiKey;
  bindNavigation();
  bindForms();
  renderConnection();
  if (state.apiKey) {
    refreshAll();
  } else {
    showView("setup");
  }
}

function bindNavigation() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  $$("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewTarget));
  });
  $$("[data-refresh]").forEach((button) => {
    button.addEventListener("click", refreshAll);
  });
}

function bindForms() {
  $("#saveConnectionBtn").addEventListener("click", () => {
    state.apiBase = cleanBase($("#apiBaseInput").value);
    state.apiKey = $("#apiKeyInput").value.trim();
    localStorage.setItem("skud.apiBase", state.apiBase);
    localStorage.setItem("skud.apiKey", state.apiKey);
    refreshAll();
  });

  $("#clearConnectionBtn").addEventListener("click", () => {
    localStorage.removeItem("skud.apiKey");
    state.apiKey = "";
    state.company = null;
    $("#apiKeyInput").value = "";
    renderConnection();
    showNotice("Подключение сброшено.");
  });

  $("#setupForm").addEventListener("submit", setupCompany);
  $("#employeeForm").addEventListener("submit", createEmployee);
  $("#roomForm").addEventListener("submit", createRoom);
  $("#accessForm").addEventListener("submit", saveAccessRule);
  $("#scannerForm").addEventListener("submit", createScanner);
  $("#faceForm").addEventListener("submit", uploadFacePhoto);
  $("#qrForm").addEventListener("submit", createQrPass);
  $("#employeeSearch").addEventListener("input", renderEmployees);
  $("#ruleEmployeeFilter").addEventListener("change", loadRules);
  $("#ruleRoomFilter").addEventListener("change", loadRules);
  $("#resetRuleFilters").addEventListener("click", () => {
    $("#ruleEmployeeFilter").value = "";
    $("#ruleRoomFilter").value = "";
    loadRules();
  });
  $("#loadEventsBtn").addEventListener("click", loadEvents);
}

function showView(viewId) {
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === viewId));
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === viewId));
  $("#viewTitle").textContent = titles[viewId][0];
  $("#viewSubtitle").textContent = titles[viewId][1];
}

function cleanBase(value) {
  return (value || window.location.origin).replace(/\/+$/, "");
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (state.apiKey) {
    headers.set("X-API-Key", state.apiKey);
  }

  const response = await fetch(`${state.apiBase}${path}`, { ...options, headers });
  const text = await response.text();
  const data = parseResponseBody(text);
  if (!response.ok) {
    throw new Error(data?.detail || text || response.statusText);
  }
  return data;
}

function parseResponseBody(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function refreshAll() {
  try {
    await Promise.all([loadCompany(), loadEmployees(), loadRooms()]);
    await Promise.all([loadRules(), loadScanners(), loadEvents(), loadStats()]);
    renderAll();
    showNotice("Данные обновлены.");
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function loadCompany() {
  state.company = await api("/api/v1/company");
  renderConnection();
}

async function loadEmployees() {
  state.employees = await api("/api/v1/employees");
}

async function loadRooms() {
  state.rooms = await api("/api/v1/rooms");
}

async function loadRules() {
  const params = new URLSearchParams();
  const employeeId = $("#ruleEmployeeFilter")?.value;
  const roomId = $("#ruleRoomFilter")?.value;
  if (employeeId) params.set("employee_id", employeeId);
  if (roomId) params.set("room_id", roomId);
  state.rules = await api(`/api/v1/access-rules${params.toString() ? `?${params}` : ""}`);
  renderRules();
}

async function loadScanners() {
  state.scanners = await api("/api/v1/scanners");
}

async function loadEvents() {
  const params = new URLSearchParams({ limit: "200" });
  const employeeId = $("#eventEmployeeFilter")?.value;
  const roomId = $("#eventRoomFilter")?.value;
  const decision = $("#eventDecisionFilter")?.value;
  if (employeeId) params.set("employee_id", employeeId);
  if (roomId) params.set("room_id", roomId);
  if (decision) params.set("decision", decision);
  state.events = await api(`/api/v1/access-events?${params}`);
  renderEvents();
}

async function loadStats() {
  const [occupancy, throughput, officeTime] = await Promise.all([
    api("/api/v1/stats/occupancy"),
    api("/api/v1/stats/throughput"),
    api("/api/v1/stats/office-time"),
  ]);
  state.occupancy = occupancy;
  state.throughput = throughput;
  state.officeTime = officeTime;
}

function renderAll() {
  renderConnection();
  renderSelects();
  renderEmployees();
  renderRooms();
  renderRules();
  renderScanners();
  renderEvents();
  renderStats();
}

function renderConnection() {
  $("#connectionApi").textContent = state.apiBase || "-";
  $("#connectionCompany").textContent = state.company ? `${state.company.name} (${state.company.slug})` : "-";
  $("#connectionKey").textContent = state.apiKey ? `${state.apiKey.slice(0, 14)}...` : "-";
  $("#companyLabel").textContent = state.company?.name || "Компания не выбрана";
}

function renderSelects() {
  $$("[data-employee-select]").forEach((select) => {
    const current = select.value;
    const allowEmpty = select.id || select.closest(".filters") || select.closest("#setup");
    select.innerHTML = `${allowEmpty ? '<option value="">Все сотрудники</option>' : ""}${state.employees
      .map((employee) => `<option value="${employee.id}">${escapeHtml(employee.full_name)} #${employee.id}</option>`)
      .join("")}`;
    select.value = state.employees.some((employee) => String(employee.id) === current) ? current : "";
  });

  $$("[data-room-select]").forEach((select) => {
    const current = select.value;
    const allowEmpty = select.id || select.closest(".filters") || select.closest("#setup");
    select.innerHTML = `${allowEmpty ? '<option value="">Все помещения</option>' : ""}${state.rooms
      .map((room) => `<option value="${room.id}">${escapeHtml(room.name)} (${escapeHtml(room.code)})</option>`)
      .join("")}`;
    select.value = state.rooms.some((room) => String(room.id) === current) ? current : "";
  });
}

function renderEmployees() {
  const query = ($("#employeeSearch").value || "").toLowerCase();
  const employees = state.employees.filter((employee) => {
    const text = `${employee.full_name} ${employee.external_id || ""} ${employee.position || ""} ${employee.email || ""}`.toLowerCase();
    return text.includes(query);
  });
  $("#employeesBody").innerHTML = employees
    .map(
      (employee) => `
        <tr>
          <td>${employee.id}</td>
          <td><strong>${escapeHtml(employee.full_name)}</strong></td>
          <td>${escapeHtml(employee.external_id || "-")}</td>
          <td>${escapeHtml(employee.position || "-")}</td>
          <td>${escapeHtml([employee.email, employee.phone].filter(Boolean).join(" / ") || "-")}</td>
          <td>${statusBadge(employee.status)}</td>
          <td>
            <div class="row-actions">
              <button class="button" data-employee-status="${employee.id}" data-status="${employee.status === "active" ? "suspended" : "active"}">
                ${employee.status === "active" ? "Отключить" : "Активировать"}
              </button>
            </div>
          </td>
        </tr>
      `,
    )
    .join("");

  $$("[data-employee-status]").forEach((button) => {
    button.addEventListener("click", () => updateEmployeeStatus(button.dataset.employeeStatus, button.dataset.status));
  });
}

function renderRooms() {
  $("#roomsBody").innerHTML = state.rooms
    .map(
      (room) => `
        <tr>
          <td>${room.id}</td>
          <td><strong>${escapeHtml(room.name)}</strong><div class="muted">${escapeHtml(room.description || "")}</div></td>
          <td>${escapeHtml(room.code)}</td>
          <td>${room.capacity ?? "-"}</td>
          <td>${statusBadge(room.status)}</td>
          <td>
            <div class="row-actions">
              <button class="button" data-room-status="${room.id}" data-status="${room.status === "active" ? "inactive" : "active"}">
                ${room.status === "active" ? "Выключить" : "Включить"}
              </button>
            </div>
          </td>
        </tr>
      `,
    )
    .join("");

  $$("[data-room-status]").forEach((button) => {
    button.addEventListener("click", () => updateRoomStatus(button.dataset.roomStatus, button.dataset.status));
  });
}

function renderRules() {
  $("#rulesBody").innerHTML = state.rules
    .map((rule) => {
      const employee = employeeName(rule.employee_id);
      const room = roomName(rule.room_id);
      const period = [formatDate(rule.valid_from), formatDate(rule.valid_until)].filter(Boolean).join(" - ") || "-";
      return `
        <tr>
          <td>${rule.id}</td>
          <td>${escapeHtml(employee)}</td>
          <td>${escapeHtml(room)}</td>
          <td>${methodBadges(rule.allowed_methods)}</td>
          <td>${escapeHtml(period)}</td>
          <td>${rule.is_active ? statusBadge("active") : statusBadge("inactive")}</td>
          <td>
            <div class="row-actions">
              <button class="button" data-rule-toggle="${rule.id}" data-active="${rule.is_active ? "false" : "true"}">
                ${rule.is_active ? "Отключить" : "Включить"}
              </button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  $$("[data-rule-toggle]").forEach((button) => {
    button.addEventListener("click", () => updateRuleActive(button.dataset.ruleToggle, button.dataset.active === "true"));
  });
}

function renderScanners() {
  $("#scannersBody").innerHTML = state.scanners
    .map(
      (scanner) => `
        <tr>
          <td>${scanner.id}</td>
          <td><strong>${escapeHtml(scanner.name)}</strong></td>
          <td>${escapeHtml(roomName(scanner.room_id))}</td>
          <td>${escapeHtml(scanner.direction)}</td>
          <td>${methodBadges(scanner.allowed_methods)}</td>
          <td>${statusBadge(scanner.status)}</td>
          <td>${formatDate(scanner.last_seen_at) || "-"}</td>
        </tr>
      `,
    )
    .join("");
}

function renderEvents() {
  const fullRows = state.events
    .map(
      (event) => `
        <tr>
          <td>${event.id}</td>
          <td>${formatDate(event.occurred_at)}</td>
          <td>${escapeHtml(event.employee_id ? employeeName(event.employee_id) : "-")}</td>
          <td>${escapeHtml(event.room_id ? roomName(event.room_id) : "-")}</td>
          <td>${event.scanner_id ?? "-"}</td>
          <td>${methodBadges([event.method])}</td>
          <td>${decisionBadge(event.decision)}</td>
          <td>${escapeHtml(event.reason || "-")}</td>
        </tr>
      `,
    )
    .join("");
  const recentRows = state.events
    .slice(0, 8)
    .map(
      (event) => `
        <tr>
          <td>${formatDate(event.occurred_at)}</td>
          <td>${methodBadges([event.method])}</td>
          <td>${decisionBadge(event.decision)}</td>
          <td>${escapeHtml(event.reason || "-")}</td>
        </tr>
      `,
    )
    .join("");
  $("#eventsBody").innerHTML = fullRows || emptyRow(8);
  $("#recentEventsBody").innerHTML = recentRows || emptyRow(4);
}

function renderStats() {
  const inside = state.occupancy.reduce((sum, room) => sum + Number(room.current_count || 0), 0);
  const today = new Date().toISOString().slice(0, 10);
  const todayEvents = state.events.filter((event) => event.occurred_at?.slice(0, 10) === today).length;

  $("#metricEmployees").textContent = state.employees.length;
  $("#metricRooms").textContent = state.rooms.length;
  $("#metricInside").textContent = inside;
  $("#metricToday").textContent = todayEvents;

  const occupancyHtml = state.occupancy.map(renderOccupancyItem).join("") || emptyState("Нет помещений");
  $("#occupancyList").innerHTML = occupancyHtml;
  $("#roomOccupancySummary").innerHTML = occupancyHtml;

  const max = Math.max(1, ...state.throughput.map((item) => Number(item.granted_entries || 0)));
  $("#throughputBars").innerHTML =
    state.throughput
      .slice(0, 12)
      .map((item) => {
        const value = Number(item.granted_entries || 0);
        return `
          <div class="bar-row">
            <span>${escapeHtml(item.day)} / ${escapeHtml(roomName(item.room_id))}</span>
            <div class="bar-track"><div class="bar-fill" style="width: ${(value / max) * 100}%"></div></div>
            <strong>${value}</strong>
          </div>
        `;
      })
      .join("") || emptyState("Нет проходов");
}

function renderOccupancyItem(room) {
  const people = room.employees?.map((employee) => employee.full_name).join(", ") || "Никого";
  return `
    <div class="occupancy-item">
      <div>
        <div class="occupancy-name">${escapeHtml(room.room_name)}</div>
        <div class="occupancy-people">${escapeHtml(people)}</div>
      </div>
      <span class="badge ${room.current_count ? "green" : "blue"}">${room.current_count}</span>
    </div>
  `;
}

async function setupCompany(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  state.apiBase = cleanBase($("#apiBaseInput").value);
  const bootstrapToken = form.get("bootstrap_token");
  try {
    const response = await fetch(`${state.apiBase}/api/v1/setup/company`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Bootstrap-Token": bootstrapToken,
      },
      body: JSON.stringify({
        name: form.get("name"),
        slug: form.get("slug"),
      }),
    });
    const text = await response.text();
    const data = parseResponseBody(text);
    if (!response.ok) throw new Error(data?.detail || text || response.statusText);
    state.apiKey = data.admin_api_key;
    $("#apiKeyInput").value = state.apiKey;
    localStorage.setItem("skud.apiBase", state.apiBase);
    localStorage.setItem("skud.apiKey", state.apiKey);
    formElement.reset();
    await refreshAll();
    showView("dashboard");
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function createEmployee(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const payload = formObject(formElement);
  await submitAndRefresh("/api/v1/employees", payload, formElement);
}

async function createRoom(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const payload = formObject(formElement);
  if (payload.capacity === "") delete payload.capacity;
  await submitAndRefresh("/api/v1/rooms", payload, formElement);
}

async function saveAccessRule(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const allowed = methodsFromForm(formElement);
  if (!allowed.length) {
    showNotice("Выберите хотя бы один метод доступа.", true);
    return;
  }
  const payload = {
    employee_id: Number(form.get("employee_id")),
    room_id: Number(form.get("room_id")),
    allowed_methods: allowed,
    valid_from: localDateToIso(form.get("valid_from")),
    valid_until: localDateToIso(form.get("valid_until")),
    is_active: form.get("is_active") === "on",
  };
  await submitAndRefresh("/api/v1/access-rules", payload, formElement);
}

async function createScanner(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const allowed = methodsFromForm(formElement);
  if (!allowed.length) {
    showNotice("Выберите хотя бы один метод сканера.", true);
    return;
  }
  const payload = {
    name: form.get("name"),
    room_id: Number(form.get("room_id")),
    direction: form.get("direction"),
    status: form.get("status"),
    allowed_methods: allowed,
  };
  try {
    const result = await api("/api/v1/scanners", { method: "POST", body: JSON.stringify(payload) });
    $("#scannerTokenOutput").value = result.scanner_token;
    formElement.reset();
    await refreshAll();
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function uploadFacePhoto(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const employeeId = form.get("employee_id");
  const data = new FormData();
  data.append("file", form.get("file"));
  try {
    const result = await api(`/api/v1/employees/${employeeId}/face-photos`, { method: "POST", body: data });
    showNotice(`Фото загружено: ${result.quality_status}.`);
    formElement.reset();
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function createQrPass(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const employeeId = form.get("employee_id");
  try {
    const result = await api(`/api/v1/employees/${employeeId}/qr-passes`, {
      method: "POST",
      body: JSON.stringify({ ttl_hours: Number(form.get("ttl_hours") || 12) }),
    });
    $("#qrPayloadOutput").value = result.payload;
    showNotice("QR-пропуск выпущен.");
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function updateEmployeeStatus(id, status) {
  await patchAndRefresh(`/api/v1/employees/${id}`, { status });
}

async function updateRoomStatus(id, status) {
  await patchAndRefresh(`/api/v1/rooms/${id}`, { status });
}

async function updateRuleActive(id, isActive) {
  await patchAndRefresh(`/api/v1/access-rules/${id}`, { is_active: isActive });
}

async function submitAndRefresh(path, payload, form) {
  try {
    await api(path, { method: "POST", body: JSON.stringify(cleanPayload(payload)) });
    form.reset();
    await refreshAll();
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function patchAndRefresh(path, payload) {
  try {
    await api(path, { method: "PATCH", body: JSON.stringify(payload) });
    await refreshAll();
  } catch (error) {
    showNotice(error.message, true);
  }
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function cleanPayload(payload) {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== ""));
}

function methodsFromForm(form) {
  const methods = [];
  if ($("[name='method_qr']", form).checked) methods.push("qr");
  if ($("[name='method_face']", form).checked) methods.push("face");
  return methods;
}

function localDateToIso(value) {
  if (!value) return null;
  return new Date(value).toISOString();
}

function employeeName(id) {
  return state.employees.find((employee) => Number(employee.id) === Number(id))?.full_name || `Сотрудник #${id}`;
}

function roomName(id) {
  return state.rooms.find((room) => Number(room.id) === Number(id))?.name || `Помещение #${id}`;
}

function statusBadge(status) {
  const color = status === "active" ? "green" : status === "suspended" || status === "inactive" ? "red" : "blue";
  return `<span class="badge ${color}">${escapeHtml(status)}</span>`;
}

function decisionBadge(decision) {
  return `<span class="badge ${decision === "granted" ? "green" : "red"}">${escapeHtml(decision)}</span>`;
}

function methodBadges(methods = []) {
  return methods.map((method) => `<span class="badge ${method === "face" ? "yellow" : "blue"}">${escapeHtml(method)}</span>`).join(" ");
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function emptyRow(columns) {
  return `<tr><td colspan="${columns}" class="muted">Нет данных</td></tr>`;
}

function emptyState(text) {
  return `<div class="muted">${escapeHtml(text)}</div>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showNotice(message, isError = false) {
  const notice = $("#notice");
  notice.textContent = message;
  notice.classList.toggle("error", isError);
  notice.classList.remove("hidden");
  window.clearTimeout(showNotice.timer);
  showNotice.timer = window.setTimeout(() => notice.classList.add("hidden"), 4200);
}

init();
