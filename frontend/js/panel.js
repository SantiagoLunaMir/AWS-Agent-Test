const $ = (id) => document.getElementById(id);
const config = await fetch("config.json", { cache: "no-store" }).then((r) => r.json());
const API = config.apiUrl.replace(/\/$/, "");
const REFRESCO_MS = 5000;
const SERVICIOS = {
  diagnostico: "Diagnóstico", afinacion: "Afinación", cambio_aceite: "Cambio de aceite", frenos: "Frenos",
  suspension: "Suspensión", electrico: "Eléctrico", aire_acondicionado: "Aire acondicionado",
  alineacion_balanceo: "Alineación y balanceo", otro: "Otro",
};

const hoyLocal = () => new Date().toLocaleDateString("en-CA");
let fecha = hoyLocal();
// Entrada por enlace (panel.html#clave=...): el fragmento no llega al servidor y se borra de la barra.
const claveEnlace = new URLSearchParams(location.hash.slice(1)).get("clave");
if (claveEnlace) {
  sessionStorage.setItem("panel.clave", claveEnlace.trim());
  history.replaceState(null, "", location.pathname + location.search);
}
window.addEventListener("hashchange", () => { if (location.hash.includes("clave=")) location.reload(); });
let clave = sessionStorage.getItem("panel.clave") || "";

function el(tag, props = {}, ...hijos) {
  const n = Object.assign(document.createElement(tag), props);
  hijos.flat().forEach((h) => n.append(h instanceof Node ? h : document.createTextNode(h ?? "")));
  return n;
}

async function api(ruta, opciones = {}) {
  const r = await fetch(`${API}${ruta}`, {
    ...opciones,
    headers: { "content-type": "application/json", "x-panel-key": clave },
  });
  if (r.status === 401) {
    pedirClave();
    throw new Error("Clave inválida");
  }
  const cuerpo = await r.json();
  if (!r.ok) throw new Error(cuerpo.error || `Error ${r.status}`);
  return cuerpo;
}

function pedirClave() {
  if (!$("clave").open) $("clave").showModal();
}

$("claveForm").addEventListener("submit", () => {
  clave = $("claveInput").value.trim();
  sessionStorage.setItem("panel.clave", clave);
  cargar();
});

function kpis(citas) {
  const cuenta = (e) => citas.filter((c) => c.estado === e).length;
  const datos = [["Citas del día", citas.length], ["Confirmadas", cuenta("confirmada")],
    ["Completadas", cuenta("completada")], ["Canceladas", cuenta("cancelada")]];
  $("kpis").replaceChildren(...datos.map(([t, v]) => el("div", { className: "kpi" }, el("b", {}, String(v)), el("span", {}, t))));
}

function agenda({ citas, mecanicos, horarios }) {
  const activas = citas.filter((c) => c.estado !== "cancelada");
  const cabecera = el("tr", {}, el("th", {}, ""),
    mecanicos.map((m) => el("th", {}, m.nombre, el("small", {}, m.especialidades.join(", ")))));
  const filas = horarios.map((h) => el("tr", {}, el("td", { className: "h" }, h),
    mecanicos.map((m) => {
      const c = activas.find((x) => x.hora === h && x.mecanico_id === m.id);
      if (!c) return el("td", { className: "slot" });
      return el("td", { className: `slot ${c.estado}` }, el("b", {}, `${c.marca} ${c.modelo}`),
        `${SERVICIOS[c.servicio] || c.servicio} · ${c.nombre}`);
    })));
  $("agenda").replaceChildren(el("thead", {}, cabecera), el("tbody", {}, filas));
  if (!horarios.length) $("agenda").replaceChildren(el("caption", {}, "El taller no abre este día."));
}

function tabla(citas) {
  $("vacio").hidden = citas.length > 0;
  $("citas").replaceChildren(...citas.map((c) => {
    const acciones = el("div", { className: "acciones" });
    if (c.foto_key) acciones.append(el("button", { onclick: () => verFoto(c) }, "Foto"));
    if (c.estado === "confirmada") {
      acciones.append(el("button", { onclick: () => cambiar(c, "completada") }, "Completar"),
        el("button", { onclick: () => cambiar(c, "cancelada") }, "Cancelar"));
    }
    return el("tr", {},
      el("td", {}, el("b", {}, c.hora)),
      el("td", {}, c.cita_id),
      el("td", {}, c.nombre, el("small", {}, c.telefono)),
      el("td", {}, `${c.marca} ${c.modelo}`, el("small", {}, [c.tipo, c.color].filter(Boolean).join(" · "))),
      el("td", {}, SERVICIOS[c.servicio] || c.servicio, el("small", {}, c.descripcion || "")),
      el("td", {}, c.mecanico_nombre),
      el("td", {}, el("span", { className: `estado ${c.estado}` }, c.estado)),
      el("td", {}, acciones));
  }));
}

async function verFoto(c) {
  const { url } = await api(`/panel/foto?key=${encodeURIComponent(c.foto_key)}`);
  $("fotoImg").src = url;
  $("fotoInfo").textContent = `${c.marca} ${c.modelo} · ${c.observaciones || "sin observaciones"}`;
  $("fotoDialog").showModal();
}

async function cambiar(c, estado) {
  const verbo = estado === "cancelada" ? "cancelar" : "marcar como completada";
  if (!confirm(`¿Seguro que quieres ${verbo} la cita ${c.cita_id}?`)) return;
  try {
    await api(`/panel/citas/${encodeURIComponent(c.cita_id)}/estado`, { method: "POST", body: JSON.stringify({ estado }) });
    cargar();
  } catch (e) {
    alert(e.message);
  }
}

async function cargar() {
  $("fecha").value = fecha;
  if (!clave) return pedirClave();
  try {
    const datos = await api(`/panel/citas?fecha=${fecha}`);
    kpis(datos.citas);
    agenda(datos);
    tabla(datos.citas);
    $("live").classList.remove("off");
  } catch (e) {
    $("live").classList.add("off");
    console.warn(e);
  }
}

function moverDia(delta) {
  const d = new Date(`${fecha}T12:00:00`);
  d.setDate(d.getDate() + delta);
  fecha = d.toLocaleDateString("en-CA");
  cargar();
}

$("fecha").addEventListener("change", (e) => { fecha = e.target.value || hoyLocal(); cargar(); });
$("anterior").addEventListener("click", () => moverDia(-1));
$("siguiente").addEventListener("click", () => moverDia(1));
$("hoy").addEventListener("click", () => { fecha = hoyLocal(); cargar(); });

// ---------- compartir el chat (enlace con código de acceso + QR) ----------
$("compartir").addEventListener("click", async () => {
  try {
    const { codigo, enlace, qr } = await api("/panel/chat");
    $("qr").src = qr;
    $("codigoChat").textContent = codigo;
    $("enlaceChat").value = enlace;
    $("copiar").textContent = "Copiar enlace";
    $("compartirDialog").showModal();
  } catch (e) {
    if (e.message !== "Clave inválida") alert(e.message);
  }
});
$("copiar").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("enlaceChat").value);
    $("copiar").textContent = "¡Copiado!";
  } catch {
    $("enlaceChat").select();
    $("copiar").textContent = "Cópialo con Ctrl+C";
  }
});

cargar();
setInterval(() => { if (!document.hidden && clave) cargar(); }, REFRESCO_MS);
