const $ = (id) => document.getElementById(id);
const ui = {
  mensajes: $("mensajes"), form: $("form"), texto: $("texto"), enviar: $("enviar"), archivo: $("archivo"),
  status: $("status"), toast: $("toast"), registro: $("registro"), previewFoto: $("previewFoto"),
  previewImg: $("previewImg"), preview: $("preview"),
};

const config = await fetch("config.json", { cache: "no-store" }).then((r) => r.json());
const API = config.apiUrl.replace(/\/$/, "");
const POLL_MS = 2000;

const estado = {
  sessionId: localStorage.getItem("tallerchat.session") || crypto.randomUUID(),
  cliente: JSON.parse(localStorage.getItem("tallerchat.cliente") || "null"),
  desde: "",
  vistos: new Set(),
  pensando: false,
  foto: null,
  fotosLocales: new Map(),
  ultimoRol: null,
};
localStorage.setItem("tallerchat.session", estado.sessionId);

// ---------- utilidades ----------
function toast(texto) {
  ui.toast.textContent = texto;
  ui.toast.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (ui.toast.hidden = true), 3500);
}

function escapar(texto) {
  return texto.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function formato(texto) {
  return escapar(texto)
    .replace(/\*([^*\n]+)\*/g, "<strong>$1</strong>")
    .replace(/(^|\s)_([^_\n]+)_(?=\s|$)/g, "$1<em>$2</em>")
    .replace(/~([^~\n]+)~/g, "<s>$1</s>");
}

function hora(iso) {
  return new Date(iso).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" });
}

async function api(ruta, opciones = {}) {
  const r = await fetch(`${API}${ruta}`, {
    ...opciones,
    headers: { "content-type": "application/json", ...(opciones.headers || {}) },
  });
  const cuerpo = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(cuerpo.error || `Error ${r.status}`), { status: r.status });
  return cuerpo;
}

// ---------- render ----------
function pintarMensaje({ sk, rol, texto, ts, foto_key }) {
  if (sk && estado.vistos.has(sk)) return;
  if (sk) estado.vistos.add(sk);
  const salida = rol === "cliente";
  const div = document.createElement("div");
  div.className = `msg ${salida ? "msg--out" : "msg--in"}`;
  if (estado.ultimoRol !== rol) div.classList.add("msg--first");
  estado.ultimoRol = rol;

  if (foto_key) {
    const local = estado.fotosLocales.get(foto_key.split("/").pop());
    if (local) {
      const img = document.createElement("img");
      img.src = local;
      img.alt = "Foto del vehículo";
      div.appendChild(img);
    } else {
      const p = document.createElement("div");
      p.className = "msg__foto-placeholder";
      p.textContent = "📷 Foto";
      div.appendChild(p);
    }
  }
  const cuerpo = document.createElement("span");
  cuerpo.innerHTML = formato(texto || "");
  div.appendChild(cuerpo);

  const meta = document.createElement("span");
  meta.className = "msg__meta";
  meta.textContent = hora(ts || new Date().toISOString());
  if (salida) {
    const tick = document.createElement("span");
    tick.className = "msg__tick";
    tick.textContent = "✓✓";
    meta.appendChild(tick);
  }
  div.appendChild(meta);
  ui.mensajes.appendChild(div);
  if (!salida) {
    document.querySelectorAll(".msg__tick").forEach((t) => t.classList.add("msg__tick--read"));
    ui.preview.textContent = (texto || "").replace(/\*/g, "").slice(0, 60);
  }
  ui.mensajes.scrollTop = ui.mensajes.scrollHeight;
}

function mostrarEscribiendo(activo) {
  estado.pensando = activo;
  ui.status.textContent = activo ? "escribiendo…" : "en línea";
  ui.status.classList.toggle("typing", activo);
  ui.enviar.disabled = activo;
  let burbuja = document.querySelector(".typing-bubble");
  if (activo && !burbuja) {
    burbuja = document.createElement("div");
    burbuja.className = "typing-bubble";
    burbuja.innerHTML = "<span></span><span></span><span></span>";
    ui.mensajes.appendChild(burbuja);
    ui.mensajes.scrollTop = ui.mensajes.scrollHeight;
  } else if (!activo && burbuja) {
    burbuja.remove();
  }
}

// ---------- sondeo de mensajes nuevos (simula las notificaciones push) ----------
async function sondear() {
  try {
    const q = new URLSearchParams({ session_id: estado.sessionId });
    if (estado.desde) q.set("desde", estado.desde);
    const { mensajes, estado: est } = await api(`/mensajes?${q}`);
    document.querySelector(".typing-bubble")?.remove();
    for (const m of mensajes) {
      pintarMensaje(m);
      estado.desde = m.sk;
    }
    mostrarEscribiendo(est === "pensando");
  } catch (e) {
    console.warn(e);
  } finally {
    setTimeout(sondear, POLL_MS);
  }
}

// ---------- fotos ----------
async function reducirImagen(file, maximo = 1280) {
  const bitmap = await createImageBitmap(file);
  const escala = Math.min(1, maximo / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * escala);
  canvas.height = Math.round(bitmap.height * escala);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return new Promise((ok) => canvas.toBlob(ok, "image/jpeg", 0.85));
}

async function subirFoto(blob) {
  const { url, fields, foto_id } = await api("/fotos", {
    method: "POST",
    body: JSON.stringify({ session_id: estado.sessionId, content_type: "image/jpeg" }),
  });
  const datos = new FormData();
  Object.entries(fields).forEach(([k, v]) => datos.append(k, v));
  datos.append("file", blob);
  const r = await fetch(url, { method: "POST", body: datos });
  if (!r.ok) throw new Error("No se pudo subir la foto.");
  return foto_id;
}

ui.archivo.addEventListener("change", async () => {
  const file = ui.archivo.files[0];
  ui.archivo.value = "";
  if (!file) return;
  estado.foto = await reducirImagen(file);
  ui.previewImg.src = URL.createObjectURL(estado.foto);
  ui.previewFoto.hidden = false;
  ui.texto.placeholder = "Agrega un comentario (opcional)";
  ui.texto.focus();
});

$("quitarFoto").addEventListener("click", () => {
  estado.foto = null;
  ui.previewFoto.hidden = true;
  ui.texto.placeholder = "Escribe un mensaje";
});

// ---------- enviar ----------
async function enviar(evento) {
  evento?.preventDefault();
  const texto = ui.texto.value.trim();
  if ((!texto && !estado.foto) || estado.pensando) return;
  ui.enviar.disabled = true;
  try {
    let foto_id = null;
    if (estado.foto) {
      foto_id = await subirFoto(estado.foto);
      estado.fotosLocales.set(foto_id, ui.previewImg.src);
    }
    const { sk } = await api("/chat", {
      method: "POST",
      body: JSON.stringify({ session_id: estado.sessionId, texto, foto_id, cliente: estado.cliente }),
    });
    pintarMensaje({ sk, rol: "cliente", texto, ts: new Date().toISOString(),
      foto_key: foto_id ? `fotos/${estado.sessionId}/${foto_id}` : null });
    ui.texto.value = "";
    ui.texto.style.height = "";
    estado.foto = null;
    ui.previewFoto.hidden = true;
    ui.texto.placeholder = "Escribe un mensaje";
    mostrarEscribiendo(true);
  } catch (e) {
    toast(e.message);
    ui.enviar.disabled = false;
  }
}

ui.form.addEventListener("submit", enviar);
ui.texto.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) enviar(e);
});
ui.texto.addEventListener("input", () => {
  ui.texto.style.height = "auto";
  ui.texto.style.height = `${ui.texto.scrollHeight}px`;
});

$("nueva").addEventListener("click", () => {
  if (!confirm("¿Empezar una conversación nueva?")) return;
  localStorage.setItem("tallerchat.session", crypto.randomUUID());
  location.reload();
});

// ---------- registro de identidad simulada ----------
$("registroForm").addEventListener("submit", () => {
  estado.cliente = { nombre: $("nombre").value.trim(), telefono: $("telefono").value.trim() };
  localStorage.setItem("tallerchat.cliente", JSON.stringify(estado.cliente));
});

if (!estado.cliente) {
  ui.registro.showModal();
  ui.registro.addEventListener("cancel", (e) => e.preventDefault());
}
sondear();
