const state = {
  apiBase: localStorage.getItem("skud.employee.apiBase") || window.location.origin,
  apiKey: localStorage.getItem("skud.employee.apiKey") || "",
  employeeId: localStorage.getItem("skud.employee.employeeId") || "",
  company: null,
  employees: [],
  rooms: [],
  guests: [],
};

const titles = {
  "my-pass": ["Мой пропуск", "QR пропуск сотрудника"],
  "guest-pass": ["Гостевой пропуск", "Создание пропуска для посетителя"],
  history: ["Мои гости", "Список гостевых пропусков"],
  settings: ["Подключение", "Адрес API и ключ доступа"],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function init() {
  $("#apiBaseInput").value = state.apiBase;
  $("#apiKeyInput").value = state.apiKey;
  bindUi();
  setDefaultGuestDates();
  if (state.apiKey) {
    refreshAll();
  } else {
    showView("settings");
  }
}

function bindUi() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  $("#settingsForm").addEventListener("submit", saveSettings);
  $("#clearSettingsBtn").addEventListener("click", clearSettings);
  $("#refreshBtn").addEventListener("click", refreshAll);
  $("#employeeSelect").addEventListener("change", selectEmployee);
  $("#employeeQrForm").addEventListener("submit", createEmployeeQr);
  $("#guestForm").addEventListener("submit", createGuestPass);
  $("#loadGuestsBtn").addEventListener("click", loadGuests);
}

function showView(viewId) {
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === viewId));
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === viewId));
  $("#viewTitle").textContent = titles[viewId][0];
  $("#viewSubtitle").textContent = titles[viewId][1];
}

async function saveSettings(event) {
  event.preventDefault();
  state.apiBase = cleanBase($("#apiBaseInput").value);
  state.apiKey = $("#apiKeyInput").value.trim();
  localStorage.setItem("skud.employee.apiBase", state.apiBase);
  localStorage.setItem("skud.employee.apiKey", state.apiKey);
  await refreshAll();
  showView("my-pass");
}

function clearSettings() {
  localStorage.removeItem("skud.employee.apiBase");
  localStorage.removeItem("skud.employee.apiKey");
  localStorage.removeItem("skud.employee.employeeId");
  state.apiKey = "";
  state.employeeId = "";
  $("#apiKeyInput").value = "";
  $("#employeeSelect").innerHTML = "";
  showNotice("Подключение сброшено.");
}

function selectEmployee(event) {
  state.employeeId = event.currentTarget.value;
  localStorage.setItem("skud.employee.employeeId", state.employeeId);
  renderEmployeeStatus();
  loadGuests();
}

async function refreshAll() {
  try {
    const [company, employees, rooms] = await Promise.all([
      api("/api/v1/company"),
      api("/api/v1/employees"),
      api("/api/v1/rooms"),
    ]);
    state.company = company;
    state.employees = employees;
    state.rooms = rooms;
    if (!state.employeeId && employees.length) {
      state.employeeId = String(employees[0].id);
    }
    renderCompany();
    renderEmployees();
    renderRooms();
    await loadGuests();
    showNotice("Данные обновлены.");
  } catch (error) {
    showNotice(error.message, true);
    showView("settings");
  }
}

async function loadGuests() {
  if (!state.employeeId || !state.apiKey) return;
  try {
    state.guests = await api(`/api/v1/guests?host_employee_id=${encodeURIComponent(state.employeeId)}&limit=200`);
    renderGuests();
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function createEmployeeQr(event) {
  event.preventDefault();
  if (!state.employeeId) {
    showNotice("Выберите сотрудника.", true);
    return;
  }
  const form = new FormData(event.currentTarget);
  try {
    const pass = await api(`/api/v1/employees/${state.employeeId}/qr-passes`, {
      method: "POST",
      body: JSON.stringify({ ttl_hours: Number(form.get("ttl_hours")) }),
    });
    $("#employeeQrImage").src = absoluteUrl(pass.qr_png_url);
    $("#employeeQrImage").classList.add("visible");
    $("#employeeQrEmpty").classList.add("hidden");
    $("#employeeExpires").textContent = formatDate(pass.expires_at);
    $("#employeePassId").textContent = pass.id;
    $("#employeePayload").value = pass.payload;
    showNotice("QR сотрудника выпущен.");
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function createGuestPass(event) {
  event.preventDefault();
  if (!state.employeeId) {
    showNotice("Выберите сотрудника.", true);
    return;
  }
  const form = new FormData(event.currentTarget);
  const payload = {
    host_employee_id: Number(state.employeeId),
    room_id: Number(form.get("room_id")),
    full_name: form.get("full_name"),
    document_number: form.get("document_number") || null,
    visit_starts_at: localDateToIso(form.get("visit_starts_at")),
    visit_ends_at: localDateToIso(form.get("visit_ends_at")),
  };
  try {
    const result = await api("/api/v1/guest-passes", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#guestQrImage").src = absoluteUrl(result.qr_pass.qr_png_url);
    $("#guestQrImage").classList.add("visible");
    $("#guestQrEmpty").classList.add("hidden");
    $("#guestStatus").textContent = "Создан";
    $("#guestName").textContent = result.guest.full_name;
    $("#guestExpires").textContent = formatDate(result.qr_pass.expires_at);
    event.currentTarget.reset();
    setDefaultGuestDates();
    await loadGuests();
    showNotice("Гостевой QR создан.");
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("X-API-Key", state.apiKey);
  const response = await fetch(`${state.apiBase}${path}`, { ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(data?.detail || response.statusText);
  }
  return data;
}

function renderCompany() {
  $("#companyName").textContent = state.company ? state.company.name : "Компания";
}

function renderEmployees() {
  $("#employeeSelect").innerHTML = state.employees
    .map((employee) => `<option value="${employee.id}">${escapeHtml(employee.full_name)}</option>`)
    .join("");
  if (state.employees.some((employee) => String(employee.id) === String(state.employeeId))) {
    $("#employeeSelect").value = state.employeeId;
  }
  renderEmployeeStatus();
}

function renderEmployeeStatus() {
  const employee = currentEmployee();
  $("#employeeStatus").textContent = employee ? employee.status : "Не выбран";
}

function renderRooms() {
  $("#roomSelect").innerHTML = state.rooms
    .filter((room) => room.status === "active")
    .map((room) => `<option value="${room.id}">${escapeHtml(room.name)}</option>`)
    .join("");
}

function renderGuests() {
  $("#guestsBody").innerHTML =
    state.guests
      .map(
        (guest) => `
          <tr>
            <td>${guest.id}</td>
            <td><strong>${escapeHtml(guest.full_name)}</strong></td>
            <td>${escapeHtml(roomName(guest.room_id))}</td>
            <td>${formatDate(guest.visit_starts_at)}</td>
            <td>${formatDate(guest.visit_ends_at)}</td>
            <td><span class="badge ${guest.status === "active" ? "green" : "red"}">${escapeHtml(guest.status)}</span></td>
          </tr>
        `,
      )
      .join("") || `<tr><td colspan="6" class="muted">Нет гостевых пропусков</td></tr>`;
}

function setDefaultGuestDates() {
  const start = new Date();
  start.setMinutes(start.getMinutes() - start.getTimezoneOffset());
  const end = new Date(start);
  end.setHours(end.getHours() + 4);
  $("[name='visit_starts_at']").value = start.toISOString().slice(0, 16);
  $("[name='visit_ends_at']").value = end.toISOString().slice(0, 16);
}

function currentEmployee() {
  return state.employees.find((employee) => String(employee.id) === String(state.employeeId));
}

function roomName(id) {
  return state.rooms.find((room) => Number(room.id) === Number(id))?.name || `Помещение #${id}`;
}

function cleanBase(value) {
  return (value || window.location.origin).replace(/\/+$/, "");
}

function absoluteUrl(path) {
  if (!path) return "";
  if (/^https?:\/\//i.test(path)) return path;
  return `${state.apiBase}${path.startsWith("/") ? path : `/${path}`}`;
}

function localDateToIso(value) {
  return new Date(value).toISOString();
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
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
