const state = {
  apiBase: localStorage.getItem("skud.employee.apiBase") || window.location.origin,
  token: localStorage.getItem("skud.employee.token") || "",
  companySlug: localStorage.getItem("skud.employee.companySlug") || "",
  me: null,
  guests: [],
  events: [],
};

const titles = {
  overview: ["Обзор", "Профиль, уровни доступа и последние проходы."],
  pass: ["Мой QR", "Быстрый выпуск личного пропуска."],
  guests: ["Гости", "Приглашения и гостевые QR-коды."],
  logs: ["Логи", "История ваших проходов по помещениям."],
  settings: ["Настройки", "Смена пароля и управление сессией."],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function init() {
  $("#apiBaseInput").value = state.apiBase;
  $("[name='company_slug']").value = state.companySlug;
  bind();
  setDefaultGuestDates();
  if (state.token) {
    showDashboard();
    refreshAll();
  }
}

function bind() {
  $("#loginForm").addEventListener("submit", login);
  $("#logoutBtn").addEventListener("click", logout);
  $("#refreshBtn").addEventListener("click", refreshAll);
  $("#employeeQrForm").addEventListener("submit", createEmployeeQr);
  $("#guestForm").addEventListener("submit", createGuestPass);
  $("#loadGuestsBtn").addEventListener("click", loadGuests);
  $("#passwordForm").addEventListener("submit", changePassword);
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  $$("[data-view-target]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.viewTarget)));
}

async function login(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  state.apiBase = cleanBase(form.get("apiBase"));
  state.companySlug = form.get("company_slug").trim();
  try {
    const response = await fetch(`${state.apiBase}/api/v1/employee/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company_slug: state.companySlug,
        login: form.get("login"),
        password: form.get("password"),
      }),
    });
    const text = await response.text();
    const data = parseBody(text);
    if (!response.ok) throw new Error(data?.detail || text || response.statusText);
    state.token = data.employee_token;
    localStorage.setItem("skud.employee.apiBase", state.apiBase);
    localStorage.setItem("skud.employee.companySlug", state.companySlug);
    localStorage.setItem("skud.employee.token", state.token);
    showDashboard();
    await refreshAll();
  } catch (error) {
    showNotice(error.message, true);
  }
}

function logout() {
  localStorage.removeItem("skud.employee.token");
  state.token = "";
  state.me = null;
  $("#dashboardView").classList.add("hidden");
  $("#loginView").classList.remove("hidden");
}

function showDashboard() {
  $("#loginView").classList.add("hidden");
  $("#dashboardView").classList.remove("hidden");
}

function showView(viewId) {
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === viewId));
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === viewId));
  $("#viewTitle").textContent = titles[viewId][0];
  $("#viewSubtitle").textContent = titles[viewId][1];
}

async function refreshAll() {
  try {
    state.me = await api("/api/v1/employee/me");
    const [events, guests] = await Promise.all([api("/api/v1/employee/access-events?limit=100"), api("/api/v1/employee/guests?limit=100")]);
    state.events = events;
    state.guests = guests;
    render();
  } catch (error) {
    showNotice(error.message, true);
    if (String(error.message).includes("token")) logout();
  }
}

async function loadGuests() {
  state.guests = await api("/api/v1/employee/guests?limit=100");
  renderGuests();
}

async function createEmployeeQr(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const pass = await api("/api/v1/employee/qr-passes", {
      method: "POST",
      body: JSON.stringify({ ttl_hours: Number(form.get("ttl_hours")) }),
    });
    $("#employeeQrImage").src = absoluteUrl(pass.qr_png_url);
    $("#employeeQrImage").classList.add("visible");
    $("#employeeQrEmpty").classList.add("hidden");
    $("#employeeExpires").textContent = formatDate(pass.expires_at);
    $("#employeePassId").textContent = pass.id;
    $("#employeePayload").value = pass.payload;
    showNotice("QR-пропуск выпущен.");
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function createGuestPass(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  try {
    const result = await api("/api/v1/employee/guest-passes", {
      method: "POST",
      body: JSON.stringify({
        room_id: Number(form.get("room_id")),
        full_name: form.get("full_name"),
        document_number: form.get("document_number") || null,
        visit_starts_at: localDateToIso(form.get("visit_starts_at")),
        visit_ends_at: localDateToIso(form.get("visit_ends_at")),
      }),
    });
    $("#guestQrImage").src = absoluteUrl(result.qr_pass.qr_png_url);
    $("#guestQrImage").classList.add("visible");
    $("#guestQrEmpty").classList.add("hidden");
    $("#guestName").textContent = result.guest.full_name;
    $("#guestExpires").textContent = formatDate(result.qr_pass.expires_at);
    formElement.reset();
    setDefaultGuestDates();
    await loadGuests();
    showNotice("Гостевой QR создан.");
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function changePassword(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  try {
    await api("/api/v1/employee/change-password", {
      method: "POST",
      body: JSON.stringify({
        current_password: form.get("current_password"),
        new_password: form.get("new_password"),
      }),
    });
    formElement.reset();
    showNotice("Пароль изменен.");
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  headers.set("Authorization", `Bearer ${state.token}`);
  const response = await fetch(`${state.apiBase}${path}`, { ...options, headers });
  const text = await response.text();
  const data = parseBody(text);
  if (!response.ok) throw new Error(data?.detail || text || response.statusText);
  return data;
}

function render() {
  const employee = state.me.employee;
  $("#miniName").textContent = employee.full_name;
  $("#miniCompany").textContent = state.me.company.name;
  $("#profileName").textContent = employee.full_name;
  $("#profileMeta").textContent = [employee.position, employee.email, employee.phone].filter(Boolean).join(" • ") || "Профиль сотрудника";
  $("#profileStatus").textContent = employee.status;
  $("#profileExternal").textContent = employee.external_id ? `ID ${employee.external_id}` : `#${employee.id}`;
  const photo = employee.photo_url ? absoluteUrl(employee.photo_url) : "";
  $("#profilePhoto").src = photo || avatarData(employee.full_name);
  $("#miniPhoto").src = photo || avatarData(employee.full_name);
  renderAccess();
  renderRooms();
  renderEvents();
  renderGuests();
}

function renderAccess() {
  $("#accessCards").innerHTML =
    state.me.access_rules
      .map(
        (rule) => `
          <article class="access-card glass">
            <span>${escapeHtml(rule.room_code || "room")}</span>
            <strong>${escapeHtml(rule.room_name || `Помещение #${rule.room_id}`)}</strong>
            <p>${rule.allowed_methods.map((method) => method.toUpperCase()).join(" + ")}</p>
          </article>
        `,
      )
      .join("") || `<div class="empty-card glass">Нет активных доступов</div>`;
}

function renderRooms() {
  const roomIds = new Set(state.me.access_rules.filter((rule) => rule.is_active).map((rule) => Number(rule.room_id)));
  $("#roomSelect").innerHTML = state.me.access_rules
    .filter((rule) => roomIds.has(Number(rule.room_id)))
    .map((rule) => `<option value="${rule.room_id}">${escapeHtml(rule.room_name || `Помещение #${rule.room_id}`)}</option>`)
    .join("");
}

function renderEvents() {
  const html = state.events.map(renderEvent).join("") || `<div class="empty-card">Пока нет проходов</div>`;
  $("#eventsList").innerHTML = html;
  $("#recentEvents").innerHTML = state.events.slice(0, 5).map(renderEvent).join("") || `<div class="empty-card">Пока нет проходов</div>`;
}

function renderGuests() {
  $("#guestsList").innerHTML =
    state.guests
      .map(
        (guest) => `
          <article class="event-item">
            <div>
              <strong>${escapeHtml(guest.full_name)}</strong>
              <span>${escapeHtml(guest.room_name || `Помещение #${guest.room_id}`)} • ${formatDate(guest.visit_starts_at)} - ${formatDate(guest.visit_ends_at)}</span>
            </div>
            <span class="chip">${escapeHtml(guest.status)}</span>
          </article>
        `,
      )
      .join("") || `<div class="empty-card">Гостей пока нет</div>`;
}

function renderEvent(event) {
  return `
    <article class="event-item">
      <div>
        <strong>${escapeHtml(event.room_name || `Помещение #${event.room_id}`)}</strong>
        <span>${formatDate(event.occurred_at)} • ${escapeHtml(event.method)} • ${escapeHtml(event.reason)}</span>
      </div>
      <span class="chip ${event.decision === "granted" ? "success" : "danger"}">${escapeHtml(event.decision)}</span>
    </article>
  `;
}

function setDefaultGuestDates() {
  const startInput = $("[name='visit_starts_at']");
  const endInput = $("[name='visit_ends_at']");
  if (!startInput || !endInput) return;
  const start = new Date();
  start.setMinutes(start.getMinutes() - start.getTimezoneOffset());
  const end = new Date(start);
  end.setHours(end.getHours() + 4);
  startInput.value = start.toISOString().slice(0, 16);
  endInput.value = end.toISOString().slice(0, 16);
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

function parseBody(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function avatarData(name) {
  const initials = String(name || "S")
    .split(" ")
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240"><defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1"><stop stop-color="#3478f6"/><stop offset="1" stop-color="#7c5cff"/></linearGradient></defs><rect width="240" height="240" rx="64" fill="url(#g)"/><text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" font-family="Montserrat,Arial" font-size="76" font-weight="800" fill="white">${initials}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
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
  showNotice.timer = window.setTimeout(() => notice.classList.add("hidden"), 4500);
}

init();
