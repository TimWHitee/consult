const state = {
  apiBase: localStorage.getItem("skud.scanner.apiBase") || window.location.origin,
  scannerToken: localStorage.getItem("skud.scanner.token") || "",
  qrStream: null,
  faceStream: null,
  qrTimer: null,
  detector: null,
  qrScanBusy: false,
  qrMode: "idle",
};

const $ = (selector) => document.querySelector(selector);

function init() {
  $("#apiBaseInput").value = state.apiBase;
  $("#scannerTokenInput").value = state.scannerToken;
  renderConnection();
  $("#saveConnectionBtn").addEventListener("click", saveConnection);
  $("#startQrCameraBtn").addEventListener("click", startQrCamera);
  $("#stopQrCameraBtn").addEventListener("click", stopQrCamera);
  $("#sendQrBtn").addEventListener("click", verifyQrPayload);
  $("#startFaceCameraBtn").addEventListener("click", startFaceCamera);
  $("#captureFaceBtn").addEventListener("click", captureFace);
  $("#sendFaceFileBtn").addEventListener("click", verifyFaceFile);
}

function saveConnection() {
  state.apiBase = cleanBase($("#apiBaseInput").value);
  state.scannerToken = $("#scannerTokenInput").value.trim();
  localStorage.setItem("skud.scanner.apiBase", state.apiBase);
  localStorage.setItem("skud.scanner.token", state.scannerToken);
  renderConnection();
}

function renderConnection() {
  $("#connectionStatus").textContent = state.scannerToken ? "Connected" : "Not connected";
}

async function startQrCamera() {
  stopQrCamera();
  state.detector = "BarcodeDetector" in window ? new BarcodeDetector({ formats: ["qr_code"] }) : null;
  state.qrMode = state.detector ? "browser" : "server";
  state.qrScanBusy = false;
  state.qrStream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
    audio: false,
  });
  $("#qrVideo").srcObject = state.qrStream;
  await $("#qrVideo").play();
  $("#qrStatus").textContent = state.qrMode === "browser" ? "browser scanning" : "server scanning";
  state.qrTimer = window.setInterval(scanQrFrame, 450);
}

async function scanQrFrame() {
  const video = $("#qrVideo");
  if (video.readyState < 2 || state.qrScanBusy) return;
  try {
    state.qrScanBusy = true;
    const payload = state.detector ? await detectQrInBrowser(video) : await detectQrOnServer(video);
    if (payload) {
      $("#qrPayloadInput").value = payload;
      $("#qrStatus").textContent = "detected";
      stopQrCamera();
      await verify({ method: "qr", qr_payload: payload, raw_subject: "camera_qr" });
    }
  } catch (error) {
    $("#qrStatus").textContent = "scan error";
    renderResult({ decision: "denied", reason: error.message });
    if (state.qrMode === "server" && /decoder|install|unavailable/i.test(error.message)) {
      stopQrCamera();
    }
  } finally {
    state.qrScanBusy = false;
  }
}

async function detectQrInBrowser(video) {
  const codes = await state.detector.detect(video);
  return codes.find((code) => code.rawValue)?.rawValue || "";
}

async function detectQrOnServer(video) {
  if (!state.scannerToken) {
    throw new Error("scanner_token_required");
  }
  const canvas = $("#qrCanvas");
  const scale = Math.min(1, 960 / (video.videoWidth || 960));
  canvas.width = Math.max(320, Math.round((video.videoWidth || 960) * scale));
  canvas.height = Math.max(240, Math.round((video.videoHeight || 540) * scale));
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);

  const response = await fetch(`${state.apiBase}/api/v1/scanner/decode-qr`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Scanner-Token": state.scannerToken,
    },
    body: JSON.stringify({ image_base64: canvas.toDataURL("image/jpeg", 0.82) }),
  });
  const text = await response.text();
  const data = parseBody(text);
  if (response.status === 422) return "";
  if (!response.ok) throw new Error(data?.detail || text || response.statusText);
  return data?.qr_payload || "";
}

function stopQrCamera() {
  if (state.qrTimer) window.clearInterval(state.qrTimer);
  state.qrTimer = null;
  state.detector = null;
  state.qrMode = "idle";
  state.qrScanBusy = false;
  if (state.qrStream) {
    state.qrStream.getTracks().forEach((track) => track.stop());
    state.qrStream = null;
  }
  $("#qrVideo").srcObject = null;
  if ($("#qrStatus").textContent.includes("scanning")) $("#qrStatus").textContent = "idle";
}

async function verifyQrPayload() {
  const qrPayload = $("#qrPayloadInput").value.trim();
  if (!qrPayload) {
    renderResult({ decision: "denied", reason: "qr_payload_required" });
    return;
  }
  await verify({ method: "qr", qr_payload: qrPayload, raw_subject: "manual_qr" });
}

async function startFaceCamera() {
  if (state.faceStream) return;
  state.faceStream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
    audio: false,
  });
  $("#faceVideo").srcObject = state.faceStream;
}

async function captureFace() {
  if (!state.faceStream) await startFaceCamera();
  const video = $("#faceVideo");
  const canvas = $("#faceCanvas");
  canvas.width = video.videoWidth || 1280;
  canvas.height = video.videoHeight || 720;
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  await verify({ method: "face", face_image_base64: canvas.toDataURL("image/jpeg", 0.9), raw_subject: "camera_face" });
}

async function verifyFaceFile() {
  const file = $("#faceFileInput").files[0];
  if (!file) {
    renderResult({ decision: "denied", reason: "face_image_required" });
    return;
  }
  await verify({ method: "face", face_image_base64: await fileToDataUrl(file), raw_subject: "uploaded_face" });
}

async function verify(payload) {
  if (!state.scannerToken) {
    renderResult({ decision: "denied", reason: "scanner_token_required" });
    return;
  }
  try {
    const response = await fetch(`${state.apiBase}/api/v1/scanner/verify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Scanner-Token": state.scannerToken,
      },
      body: JSON.stringify(payload),
    });
    const text = await response.text();
    const data = parseBody(text);
    if (!response.ok) throw new Error(data?.detail || text || response.statusText);
    renderResult(data);
  } catch (error) {
    renderResult({ decision: "denied", reason: error.message });
  }
}

function renderResult(result) {
  const panel = $("#resultPanel");
  const decision = result.decision || "waiting";
  panel.classList.toggle("granted", decision === "granted");
  panel.classList.toggle("denied", decision === "denied");
  $("#decisionText").textContent = decision;
  $("#reasonText").textContent = result.reason || "-";
  $("#eventIdText").textContent = result.event_id || "-";
  $("#employeeText").textContent = result.employee_id || "-";
  $("#roomText").textContent = result.room_id || "-";
  $("#scannerText").textContent = result.scanner_id || "-";
  $("#unlockText").textContent = result.unlock_seconds ? `${result.unlock_seconds}s` : "-";
  $("#confidenceText").textContent = result.confidence ? Number(result.confidence).toFixed(3) : "-";
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function parseBody(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function cleanBase(value) {
  return (value || window.location.origin).replace(/\/+$/, "");
}

init();
