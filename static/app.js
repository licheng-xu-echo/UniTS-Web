const state = {
  inputType: "smiles",
  parsed: null,
  selectedAtoms: new Set(),
  molecules: [],
  jobId: null,
  pollTimer: null,
  samples: [],
  frames: [],
  frameIndex: 0,
  playTimer: null,
  viewer3d: null,
};

const APP_BASE_URL = new URL(".", document.currentScript?.src || window.location.href);

const els = {
  healthStatus: document.getElementById("healthStatus"),
  smilesTab: document.getElementById("smilesTab"),
  xyzTab: document.getElementById("xyzTab"),
  smilesOnly: [...document.querySelectorAll(".smiles-only")],
  xyzOnly: [...document.querySelectorAll(".xyz-only")],
  molName: document.getElementById("molName"),
  smilesInput: document.getElementById("smilesInput"),
  xyzFile: document.getElementById("xyzFile"),
  xyzInput: document.getElementById("xyzInput"),
  chargeInput: document.getElementById("chargeInput"),
  multiInput: document.getElementById("multiInput"),
  atomIndexInput: document.getElementById("atomIndexInput"),
  parseBtn: document.getElementById("parseBtn"),
  clearCurrentBtn: document.getElementById("clearCurrentBtn"),
  addMoleculeBtn: document.getElementById("addMoleculeBtn"),
  graphMeta: document.getElementById("graphMeta"),
  inputViewerMeta: document.getElementById("inputViewerMeta"),
  moleculeGraph: document.getElementById("moleculeGraph"),
  moleculeGraphLarge: document.getElementById("moleculeGraphLarge"),
  inputEmpty: document.getElementById("inputEmpty"),
  reactiveChips: document.getElementById("reactiveChips"),
  moleculeList: document.getElementById("moleculeList"),
  clearMoleculesBtn: document.getElementById("clearMoleculesBtn"),
  startJobBtn: document.getElementById("startJobBtn"),
  stopJobBtn: document.getElementById("stopJobBtn"),
  samplesInput: document.getElementById("samplesInput"),
  batchInput: document.getElementById("batchInput"),
  seedInput: document.getElementById("seedInput"),
  saveTrajectoryInput: document.getElementById("saveTrajectoryInput"),
  trajectoryModeInput: document.getElementById("trajectoryModeInput"),
  nprocInput: document.getElementById("nprocInput"),
  memInput: document.getElementById("memInput"),
  methodInput: document.getElementById("methodInput"),
  basisInput: document.getElementById("basisInput"),
  jobTitle: document.getElementById("jobTitle"),
  jobPercent: document.getElementById("jobPercent"),
  jobProgress: document.getElementById("jobProgress"),
  jobLog: document.getElementById("jobLog"),
  sampleSelect: document.getElementById("sampleSelect"),
  mol3dViewer: document.getElementById("mol3dViewer"),
  viewerCanvas: document.getElementById("viewerCanvas"),
  resultEmpty: document.getElementById("resultEmpty"),
  playBtn: document.getElementById("playBtn"),
  frameSlider: document.getElementById("frameSlider"),
  frameLabel: document.getElementById("frameLabel"),
  downloadLinks: document.getElementById("downloadLinks"),
  toast: document.getElementById("toast"),
};

const covalentRadii = {
  H: 0.31, B: 0.84, C: 0.76, N: 0.71, O: 0.66, F: 0.57, Si: 1.11, P: 1.07,
  S: 1.05, Cl: 1.02, Br: 1.2, I: 1.39, Mg: 1.41, Na: 1.66, K: 2.03, Fe: 1.24,
  Pd: 1.39, Ni: 1.24, Cu: 1.32, Zn: 1.22, Rh: 1.42, Ru: 1.46, Pt: 1.36, Au: 1.36,
};

const elementColors = {
  H: "#ffffff",
  C: "#6b7280",
  N: "#2563eb",
  O: "#dc2626",
  F: "#16a34a",
  B: "#f4a261",
  P: "#f97316",
  S: "#eab308",
  Cl: "#22c55e",
  Br: "#92400e",
  I: "#6d28d9",
  Si: "#94a3b8",
  Mg: "#65a30d",
  Na: "#7c3aed",
  K: "#9333ea",
  Ca: "#84cc16",
  Fe: "#b45309",
  Pd: "#64748b",
  Ni: "#64748b",
  Cu: "#b45309",
  Zn: "#64748b",
  Rh: "#64748b",
  Ru: "#64748b",
  Pt: "#64748b",
  Au: "#d97706",
};

const darkLabelElements = new Set(["H", "B", "S", "Si", "Mg", "Ca"]);

function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.remove("hidden");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => els.toast.classList.add("hidden"), 3600);
}

function makeUrl(path) {
  const clean = String(path).replace(/^\/+/, "");
  return new URL(clean, APP_BASE_URL).toString();
}

async function api(path, options = {}) {
  const response = await fetch(makeUrl(path), {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text };
  }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function setInputType(type) {
  state.inputType = type;
  els.smilesTab.classList.toggle("active", type === "smiles");
  els.xyzTab.classList.toggle("active", type === "xyz");
  els.smilesOnly.forEach((el) => el.classList.toggle("hidden", type !== "smiles"));
  els.xyzOnly.forEach((el) => el.classList.toggle("hidden", type !== "xyz"));
  clearParsedGraph();
}

function currentMoleculePayload() {
  const payload = {
    name: els.molName.value.trim(),
    input_type: state.inputType,
    charge: Number(els.chargeInput.value || 0),
    multiplicity: Number(els.multiInput.value || 1),
    reactive_atom_idx: els.atomIndexInput.value.trim(),
  };
  if (state.inputType === "smiles") payload.smiles = els.smilesInput.value.trim();
  else payload.xyz_text = els.xyzInput.value.trim();
  return payload;
}

function clearParsedGraph() {
  const hadSelectedAtoms = state.selectedAtoms.size > 0;
  state.parsed = null;
  state.selectedAtoms.clear();
  els.atomIndexInput.value = "";
  els.graphMeta.textContent = "No molecule loaded";
  els.inputViewerMeta.textContent = "No input";
  els.addMoleculeBtn.disabled = true;
  els.moleculeGraph.innerHTML = "";
  els.moleculeGraphLarge.innerHTML = "";
  els.inputEmpty.style.display = "flex";
  renderReactiveChips();
  if (hadSelectedAtoms && state.frames.length) drawFrame();
}

function clearCurrent() {
  els.molName.value = "";
  els.smilesInput.value = "";
  els.xyzInput.value = "";
  els.xyzFile.value = "";
  els.chargeInput.value = "0";
  els.multiInput.value = "1";
  clearParsedGraph();
}

function syncSelectedFromInput() {
  state.selectedAtoms = new Set(
    parseAtomIndexText(els.atomIndexInput.value),
  );
  renderReactiveChips();
}

function parseAtomIndexText(value) {
  return String(value)
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => Number(x))
    .filter((x) => Number.isInteger(x) && x >= 0);
}

async function parseCurrentMolecule() {
  try {
    const parsed = await api("api/molecule/parse", {
      method: "POST",
      body: JSON.stringify(currentMoleculePayload()),
    });
    state.parsed = parsed;
    syncSelectedFromInput();
    drawMoleculeGraphs(parsed.graph);
    els.graphMeta.textContent = `${parsed.atom_count} atoms, ${parsed.graph.edges.length} bonds`;
    els.inputViewerMeta.textContent = `${parsed.atom_count} atoms`;
    els.inputEmpty.style.display = "none";
    els.addMoleculeBtn.disabled = false;
  } catch (error) {
    toast(error.message);
    clearParsedGraph();
  }
}

function drawMoleculeGraphs(graph) {
  renderMoleculeGraph(els.moleculeGraph, graph, 720, 420, 36, 18);
  renderMoleculeGraph(els.moleculeGraphLarge, graph, 1100, 520, 64, 24);
}

function renderMoleculeGraph(svg, graph, width, height, pad, radius) {
  svg.innerHTML = "";
  if (!graph || !graph.nodes.length) return;

  const xs = graph.nodes.map((node) => node.x);
  const ys = graph.nodes.map((node) => node.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(1e-6, maxX - minX);
  const spanY = Math.max(1e-6, maxY - minY);
  const scale = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY);

  const points = new Map();
  graph.nodes.forEach((node) => {
    const x = width / 2 + (node.x - (minX + maxX) / 2) * scale;
    const y = height / 2 - (node.y - (minY + maxY) / 2) * scale;
    points.set(node.index, { x, y });
  });

  const make = (tag, attrs = {}) => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
    return el;
  };

  graph.edges.forEach((edge) => {
    const a = points.get(edge.source);
    const b = points.get(edge.target);
    if (!a || !b) return;
    svg.appendChild(make("line", { class: "bond", x1: a.x, y1: a.y, x2: b.x, y2: b.y }));
  });

  graph.nodes.forEach((node) => {
    const point = points.get(node.index);
    const selected = state.selectedAtoms.has(node.index);
    const fill = elementColors[node.symbol] || "#94a3b8";
    const labelFill = darkLabelElements.has(node.symbol) ? "#0f172a" : "#ffffff";
    const group = make("g", {
      class: `atom-node${selected ? " selected" : ""}`,
      transform: `translate(${point.x}, ${point.y})`,
      tabindex: "0",
    });
    group.addEventListener("click", () => toggleAtom(node.index));
    group.appendChild(
      make("circle", {
        r: radius,
        fill,
        stroke: selected ? "#f59e0b" : node.symbol === "H" ? "#64748b" : "#334155",
        "stroke-width": selected ? 4 : 1.6,
      }),
    );
    const label = make("text", { fill: labelFill });
    label.textContent = node.symbol;
    group.appendChild(label);
    const index = make("text", { class: "atom-index" });
    index.textContent = node.index;
    group.appendChild(index);
    if (selected) {
      group.appendChild(
        make("circle", {
          r: radius + 5,
          fill: "none",
          stroke: "#f59e0b",
          "stroke-width": 2,
          "stroke-dasharray": "4 3",
        }),
      );
    }
    svg.appendChild(group);
  });
}

function toggleAtom(index) {
  if (state.selectedAtoms.has(index)) state.selectedAtoms.delete(index);
  else state.selectedAtoms.add(index);
  const sorted = [...state.selectedAtoms].sort((a, b) => a - b);
  els.atomIndexInput.value = sorted.join(",");
  renderReactiveChips();
  if (state.parsed) drawMoleculeGraphs(state.parsed.graph);
  if (state.frames.length) drawFrame();
}

function renderReactiveChips() {
  els.reactiveChips.innerHTML = "";
  [...state.selectedAtoms].sort((a, b) => a - b).forEach((idx) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    const symbol = state.parsed?.graph?.nodes?.find((node) => node.index === idx)?.symbol || "?";
    chip.textContent = `${idx}:${symbol}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "x";
    remove.addEventListener("click", () => toggleAtom(idx));
    chip.appendChild(remove);
    els.reactiveChips.appendChild(chip);
  });
}

function addMolecule() {
  if (!state.parsed) return toast("请先解析分子图");
  const payload = currentMoleculePayload();
  if (!payload.reactive_atom_idx) return toast("请指定 atom index");
  payload.name = payload.name || state.parsed.name || `molecule-${state.molecules.length + 1}`;
  payload.atom_count = state.parsed.atom_count;
  payload.preview_graph = JSON.parse(JSON.stringify(state.parsed.graph));
  state.molecules.push(payload);
  renderMoleculeList();
  toast("Molecule added to queue");
}

function renderMoleculeList() {
  els.startJobBtn.disabled = state.molecules.length === 0;
  if (!state.molecules.length) {
    els.moleculeList.className = "molecule-list empty";
    els.moleculeList.textContent = "No molecules queued";
    return;
  }
  els.moleculeList.className = "molecule-list";
  els.moleculeList.innerHTML = "";
  state.molecules.forEach((mol, idx) => {
    const item = document.createElement("div");
    item.className = "molecule-item";
    const text = document.createElement("div");
    text.innerHTML = `<strong>${escapeHtml(mol.name || `molecule-${idx}`)}</strong>
      <span>${mol.input_type.toUpperCase()} · atoms ${mol.atom_count} · charge ${escapeHtml(mol.charge)} · multi ${escapeHtml(mol.multiplicity)} · rc ${escapeHtml(mol.reactive_atom_idx)}</span>`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      state.molecules.splice(idx, 1);
      renderMoleculeList();
    });
    item.appendChild(text);
    item.appendChild(remove);
    els.moleculeList.appendChild(item);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function startJob() {
  if (!state.molecules.length) return toast("请至少加入一个分子");
  const samples = Math.max(2, Number(els.samplesInput.value || 10));
  const batch = Math.max(2, Number(els.batchInput.value || samples));
  els.samplesInput.value = String(samples);
  els.batchInput.value = String(Math.min(batch, samples));

  const payload = {
    molecules: state.molecules,
    samples_per_molecule: samples,
    batch_size: Math.min(batch, samples),
    seed: els.seedInput.value.trim() || null,
    save_full_trajectory: els.saveTrajectoryInput.checked,
    nproc: Number(els.nprocInput.value || 16),
    mem: els.memInput.value.trim() || "32GB",
    method: els.methodInput.value.trim() || "b3lyp",
    basis: els.basisInput.value.trim() || "def2svp",
    empirical_dispersion: "gd3bj",
  };

  try {
    const job = await api("api/jobs", { method: "POST", body: JSON.stringify(payload) });
    state.jobId = job.id;
    state.samples = [];
    state.frames = [];
    els.startJobBtn.disabled = true;
    els.stopJobBtn.disabled = false;
    els.sampleSelect.disabled = true;
    els.downloadLinks.innerHTML = "";
    clearResultViewer();
    updateJobUi(job);
    state.pollTimer = window.setInterval(pollJob, 1200);
    pollJob();
  } catch (error) {
    toast(error.message);
  }
}

async function cancelJob() {
  if (!state.jobId || els.stopJobBtn.disabled) return;
  els.stopJobBtn.disabled = true;
  try {
    const job = await api(`api/jobs/${state.jobId}/cancel`, { method: "POST" });
    updateJobUi(job);
  } catch (error) {
    toast(error.message);
  }
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await api(`api/jobs/${state.jobId}`);
    updateJobUi(job);
    if (["completed", "failed", "cancelled"].includes(job.status)) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
      els.startJobBtn.disabled = state.molecules.length === 0;
      els.stopJobBtn.disabled = true;
      if (job.status === "completed") await loadSamples();
    }
  } catch (error) {
    window.clearInterval(state.pollTimer);
    els.stopJobBtn.disabled = true;
    toast(error.message);
  }
}

function updateJobUi(job) {
  const pct = Math.max(0, Math.min(100, Math.round((job.progress || 0) * 100)));
  els.jobTitle.textContent = `${job.status}: ${job.message || ""}`;
  els.jobTitle.className = `badge ${
    job.status === "completed" ? "ok" : job.status === "failed" ? "err" : job.status === "cancelled" ? "warn" : "run"
  }`;
  els.jobPercent.textContent = `${pct}%`;
  els.jobProgress.style.width = `${pct}%`;
  els.jobLog.textContent = (job.logs || []).join("\n");
  els.jobLog.scrollTop = els.jobLog.scrollHeight;
  if (job.status === "failed") toast(job.error || "任务失败");
  els.stopJobBtn.disabled = !["queued", "running"].includes(job.status) || job.cancel_requested;
}

async function loadSamples() {
  const data = await api(`api/jobs/${state.jobId}/samples`);
  state.samples = data.samples || [];
  els.sampleSelect.innerHTML = "";
  if (!state.samples.length) {
    const option = document.createElement("option");
    option.textContent = "No result";
    els.sampleSelect.appendChild(option);
    els.sampleSelect.disabled = true;
    return;
  }
  state.samples.forEach((sample, idx) => {
    const option = document.createElement("option");
    option.value = String(idx);
    option.textContent = sample.name;
    els.sampleSelect.appendChild(option);
  });
  els.sampleSelect.disabled = false;
  await selectSample(0);
}

function fileUrl(path) {
  return makeUrl(`api/jobs/${state.jobId}/file?path=${encodeURIComponent(path)}`);
}

async function selectSample(index) {
  const sample = state.samples[index];
  if (!sample) return;
  stopPlayback();
  els.sampleSelect.value = String(index);
  syncGraphToSample(sample);
  const useTrajectory = els.trajectoryModeInput.checked && sample.trajectory_xyz;
  const xyzPath = useTrajectory ? sample.trajectory_xyz : sample.final_xyz;
  const response = await fetch(fileUrl(xyzPath));
  const text = await response.text();
  state.frames = parseXYZFrames(text);
  state.frameIndex = useTrajectory ? Math.max(0, state.frames.length - 1) : 0;
  configureViewerControls();
  drawFrame();
  renderDownloadLinks(sample);
}

function syncGraphToSample(sample) {
  const molecule = state.molecules[sample.molecule_index];
  const graph = molecule?.preview_graph || sample.molecule_graph;
  if (!graph) return;
  const atomCount = molecule?.atom_count || sample.atom_count || graph.nodes.length;
  const reactiveAtoms = molecule
    ? parseAtomIndexText(molecule.reactive_atom_idx)
    : (sample.reactive_atom_idx || []);
  state.selectedAtoms = new Set(reactiveAtoms);
  els.atomIndexInput.value = reactiveAtoms.join(",");
  els.chargeInput.value = String(molecule?.charge ?? sample.charge ?? 0);
  els.multiInput.value = String(molecule?.multiplicity ?? sample.multiplicity ?? 1);
  state.parsed = {
    name: molecule?.name || sample.name,
    graph,
    atom_count: atomCount,
  };
  drawMoleculeGraphs(graph);
  renderReactiveChips();
  els.graphMeta.textContent = `${atomCount} atoms, ${graph.edges.length} bonds`;
  els.inputViewerMeta.textContent = `molecule ${sample.molecule_index} / sample ${sample.sample_index} · ${atomCount} atoms`;
  els.inputEmpty.style.display = "none";
}

function renderDownloadLinks(sample) {
  const links = [
    { label: "Final XYZ", path: sample.final_xyz },
    sample.trajectory_xyz ? { label: "Full trajectory XYZ", path: sample.trajectory_xyz } : null,
    sample.gjf ? { label: "Gaussian GJF", path: sample.gjf } : null,
  ].filter(Boolean);
  els.downloadLinks.innerHTML = "";
  links.forEach((link) => {
    const a = document.createElement("a");
    a.href = fileUrl(link.path);
    a.textContent = link.label;
    els.downloadLinks.appendChild(a);
  });
}

function parseXYZFrames(text) {
  const lines = text.split(/\r?\n/);
  const frames = [];
  let i = 0;
  while (i < lines.length) {
    while (i < lines.length && !lines[i].trim()) i += 1;
    if (i >= lines.length) break;
    const count = Number.parseInt(lines[i].trim(), 10);
    if (!Number.isFinite(count) || count <= 0) break;
    const title = lines[i + 1] || "";
    const atoms = [];
    for (let j = 0; j < count; j += 1) {
      const parts = (lines[i + 2 + j] || "").trim().split(/\s+/);
      if (parts.length >= 4) {
        atoms.push({ symbol: parts[0], x: Number(parts[1]), y: Number(parts[2]), z: Number(parts[3]) });
      }
    }
    if (atoms.length === count) frames.push({ title, atoms });
    i += count + 2;
  }
  return frames;
}

function configureViewerControls() {
  const max = Math.max(0, state.frames.length - 1);
  els.frameSlider.max = String(max);
  els.frameSlider.value = String(state.frameIndex);
  els.frameSlider.disabled = max === 0;
  els.playBtn.disabled = max === 0;
  updateFrameLabel();
}

function updateFrameLabel() {
  const total = state.frames.length;
  els.frameLabel.textContent = total ? `${state.frameIndex + 1}/${total}` : "0/0";
}

function frameToXyz(frame) {
  return `${frame.atoms.length}\n${frame.title || "frame"}\n${frame.atoms
    .map((atom) => `${atom.symbol} ${atom.x.toFixed(8)} ${atom.y.toFixed(8)} ${atom.z.toFixed(8)}`)
    .join("\n")}\n`;
}

function clearResultViewer() {
  stopPlayback();
  state.frames = [];
  els.resultEmpty.style.display = "flex";
  if (state.viewer3d) state.viewer3d.clear();
  const ctx = els.viewerCanvas.getContext("2d");
  ctx.clearRect(0, 0, els.viewerCanvas.width, els.viewerCanvas.height);
  updateFrameLabel();
}

function renderWith3Dmol(frame) {
  if (!window.$3Dmol || !els.mol3dViewer) return false;
  els.viewerCanvas.style.display = "none";
  els.mol3dViewer.style.display = "block";
  if (!state.viewer3d) {
    state.viewer3d = window.$3Dmol.createViewer(els.mol3dViewer, { backgroundColor: "#ffffff" });
  }
  const viewer = state.viewer3d;
  viewer.clear();
  viewer.addModel(frameToXyz(frame), "xyz");
  viewer.setStyle({}, { stick: { radius: 0.16 }, sphere: { scale: 0.28 } });
  const selected = [...state.selectedAtoms];
  if (selected.length) {
    viewer.setStyle(
      { index: selected },
      { stick: { radius: 0.18, color: "orange" }, sphere: { scale: 0.45, color: "orange" } },
    );
    selected.forEach((idx) => {
      const atom = frame.atoms[idx];
      if (!atom) return;
      viewer.addLabel(`R${idx}`, {
        position: { x: atom.x, y: atom.y, z: atom.z },
        fontColor: "#1e293b",
        fontSize: 13,
        backgroundColor: "rgba(255,255,255,0.86)",
        borderColor: "#94a3b8",
        showBackground: true,
      });
    });
  }
  viewer.zoomTo();
  viewer.render();
  return true;
}

function inferBonds(atoms) {
  const bonds = [];
  for (let i = 0; i < atoms.length; i += 1) {
    for (let j = i + 1; j < atoms.length; j += 1) {
      const a = atoms[i];
      const b = atoms[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const dz = a.z - b.z;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
      const ra = covalentRadii[a.symbol] || 0.78;
      const rb = covalentRadii[b.symbol] || 0.78;
      if (dist > 0.35 && dist < (ra + rb) * 1.28) bonds.push([i, j]);
    }
  }
  return bonds;
}

function projectAtoms(atoms, width, height) {
  const angleY = 0.65;
  const angleX = -0.45;
  const cy = Math.cos(angleY);
  const sy = Math.sin(angleY);
  const cx = Math.cos(angleX);
  const sx = Math.sin(angleX);
  const rotated = atoms.map((atom) => {
    const x1 = atom.x * cy + atom.z * sy;
    const z1 = -atom.x * sy + atom.z * cy;
    const y1 = atom.y * cx - z1 * sx;
    const z2 = atom.y * sx + z1 * cx;
    return { ...atom, px: x1, py: y1, pz: z2 };
  });
  const xs = rotated.map((a) => a.px);
  const ys = rotated.map((a) => a.py);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const span = Math.max(1e-6, maxX - minX, maxY - minY);
  const scale = Math.min(width, height) * 0.72 / span;
  const cxp = (minX + maxX) / 2;
  const cyp = (minY + maxY) / 2;
  return rotated.map((atom) => ({
    ...atom,
    sx: width / 2 + (atom.px - cxp) * scale,
    sy: height / 2 - (atom.py - cyp) * scale,
  }));
}

function drawFrame() {
  const frame = state.frames[state.frameIndex];
  els.resultEmpty.style.display = frame ? "none" : "flex";
  if (!frame) return;
  if (renderWith3Dmol(frame)) {
    updateFrameLabel();
    return;
  }

  els.mol3dViewer.style.display = "none";
  els.viewerCanvas.style.display = "block";
  const canvas = els.viewerCanvas;
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);

  const atoms = projectAtoms(frame.atoms, width, height);
  const bonds = inferBonds(frame.atoms);
  ctx.lineCap = "round";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "#7b8d96";
  bonds.forEach(([i, j]) => {
    const a = atoms[i];
    const b = atoms[j];
    ctx.beginPath();
    ctx.moveTo(a.sx, a.sy);
    ctx.lineTo(b.sx, b.sy);
    ctx.stroke();
  });

  atoms
    .map((atom, index) => ({ atom, index }))
    .sort((a, b) => a.atom.pz - b.atom.pz)
    .forEach(({ atom, index }) => {
      const color = state.selectedAtoms.has(index) ? "#f59e0b" : elementColors[atom.symbol] || "#94a3b8";
      const r = atom.symbol === "H" ? 9 : 13;
      ctx.beginPath();
      ctx.arc(atom.sx, atom.sy, r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "#334155";
      ctx.stroke();
      ctx.fillStyle = atom.symbol === "C" || state.selectedAtoms.has(index) ? "#fff" : "#111827";
      ctx.font = "10px system-ui";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(atom.symbol, atom.sx, atom.sy);
    });
  updateFrameLabel();
}

function stopPlayback() {
  if (!state.playTimer) return;
  window.clearInterval(state.playTimer);
  state.playTimer = null;
  els.playBtn.textContent = "Play";
}

function togglePlay() {
  if (!state.frames.length) return;
  if (state.playTimer) {
    stopPlayback();
    return;
  }
  els.playBtn.textContent = "Pause";
  state.playTimer = window.setInterval(() => {
    state.frameIndex = (state.frameIndex + 1) % state.frames.length;
    els.frameSlider.value = String(state.frameIndex);
    drawFrame();
  }, 120);
}

function wireEvents() {
  els.smilesTab.addEventListener("click", () => setInputType("smiles"));
  els.xyzTab.addEventListener("click", () => setInputType("xyz"));
  els.parseBtn.addEventListener("click", parseCurrentMolecule);
  els.clearCurrentBtn.addEventListener("click", clearCurrent);
  els.addMoleculeBtn.addEventListener("click", addMolecule);
  els.clearMoleculesBtn.addEventListener("click", () => {
    state.molecules = [];
    renderMoleculeList();
  });
  els.startJobBtn.addEventListener("click", startJob);
  els.stopJobBtn.addEventListener("click", cancelJob);
  els.smilesInput.addEventListener("input", clearParsedGraph);
  els.xyzInput.addEventListener("input", clearParsedGraph);
  els.sampleSelect.addEventListener("change", (event) => selectSample(Number(event.target.value)));
  els.trajectoryModeInput.addEventListener("change", () => selectSample(Number(els.sampleSelect.value || 0)));
  els.playBtn.addEventListener("click", togglePlay);
  els.frameSlider.addEventListener("input", (event) => {
    stopPlayback();
    state.frameIndex = Number(event.target.value);
    drawFrame();
  });
  els.atomIndexInput.addEventListener("input", () => {
    syncSelectedFromInput();
    if (state.parsed) drawMoleculeGraphs(state.parsed.graph);
    if (state.frames.length) drawFrame();
  });
  els.samplesInput.addEventListener("input", () => {
    const samples = Math.max(2, Number(els.samplesInput.value || 2));
    if (Number(els.batchInput.value || 0) < 2 || Number(els.batchInput.value) > samples) {
      els.batchInput.value = String(samples);
    }
  });
  els.xyzFile.addEventListener("change", async () => {
    const file = els.xyzFile.files[0];
    clearParsedGraph();
    if (!file) return;
    els.xyzInput.value = await file.text();
    if (!els.molName.value.trim()) els.molName.value = file.name.replace(/\.[^.]+$/, "");
  });
}

async function checkHealth() {
  try {
    const health = await api("api/health");
    els.healthStatus.textContent = health.ok ? `UniTS: ${health.default_units_root}` : "Server error";
  } catch {
    els.healthStatus.textContent = "Server unavailable";
  }
}

wireEvents();
renderMoleculeList();
clearResultViewer();
checkHealth();
