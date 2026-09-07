#!/usr/bin/env python3
"""Small Web UI server for UniTS transition-state generation.

This server intentionally avoids extra web framework dependencies so it can run
inside the existing UniTS conda environment.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from rdkit import Chem
from rdkit.Chem import rdDepictor

try:
    from openbabel import openbabel as ob
except Exception:  # pragma: no cover - import error is reported by API.
    ob = None


PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PROJECT_ROOT / "static"
DATA_ROOT = PROJECT_ROOT / "data"
JOB_ROOT = DATA_ROOT / "jobs"
DEFAULT_UNITS_ROOT = PROJECT_ROOT.parent / "UniTS"
MODEL_TYPE = "units_hiegnn"
CKPT_FILE = "best_full_model.pth"
DIFFUSION_STEPS = 1000

JOBS: dict[str, "JobState"] = {}
JOBS_LOCK = threading.Lock()


@dataclass
class JobState:
    id: str
    request: dict[str, Any]
    root: Path
    status: str = "queued"
    progress: float = 0.0
    message: str = "Queued"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    current_molecule: int = 0
    total_molecules: int = 0
    cancel_requested: bool = False
    current_process: Any = None


class JobCancelled(RuntimeError):
    pass


def json_response(handler: SimpleHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: SimpleHTTPRequestHandler, text: str, status: int = 200) -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler: SimpleHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def parse_int_list(value: str | list[int]) -> list[int]:
    if isinstance(value, list):
        return [int(v) for v in value]
    if not value or not value.strip():
        return []
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def molecule_to_graph(mol: Chem.Mol) -> dict[str, Any]:
    display_mol = Chem.Mol(mol)
    try:
        rdDepictor.Compute2DCoords(display_mol)
    except Exception:
        pass

    conf = display_mol.GetConformer() if display_mol.GetNumConformers() else None
    nodes = []
    for atom in display_mol.GetAtoms():
        idx = atom.GetIdx()
        if conf is not None:
            pos = conf.GetAtomPosition(idx)
            x, y = float(pos.x), float(pos.y)
        else:
            angle = 2 * math.pi * idx / max(1, display_mol.GetNumAtoms())
            x, y = math.cos(angle), math.sin(angle)
        nodes.append(
            {
                "index": idx,
                "symbol": atom.GetSymbol(),
                "atomic_num": atom.GetAtomicNum(),
                "x": x,
                "y": y,
            }
        )

    edges = []
    for bond in display_mol.GetBonds():
        edges.append(
            {
                "source": bond.GetBeginAtomIdx(),
                "target": bond.GetEndAtomIdx(),
                "order": str(bond.GetBondType()),
            }
        )
    return {"nodes": nodes, "edges": edges, "atom_count": display_mol.GetNumAtoms()}


def mol_from_smiles(smiles: str) -> Chem.Mol:
    # Match UniTS' CLI dataset construction, which parses SMILES with
    # sanitize=False before extracting graph features.
    mol = Chem.MolFromSmiles(smiles, sanitize=False)
    if mol is None:
        raise ValueError("RDKit failed to parse the SMILES input")
    mol.UpdatePropertyCache(strict=False)
    return mol


def align_xyz_mol_with_units(mol: Chem.Mol) -> Chem.Mol:
    """Mirror units.infer_xyz.xyz2mol so preview indices match inference."""
    units_root = Path(os.environ.get("UNITS_ROOT") or DEFAULT_UNITS_ROOT).resolve()
    if str(units_root) not in sys.path:
        sys.path.insert(0, str(units_root))
    try:
        from units.data import update_mol_info
    except Exception:
        return mol
    try:
        aligned_mol, _ = update_mol_info(mol)
        aligned_mol.UpdatePropertyCache(strict=False)
        return aligned_mol
    except Exception:
        return mol


def mol_from_xyz_text(xyz_text: str) -> Chem.Mol:
    if ob is None:
        raise RuntimeError("Open Babel is not available in this Python environment")
    if not xyz_text.strip():
        raise ValueError("XYZ text is empty")

    xyz_to_obmol = ob.OBConversion()
    if not xyz_to_obmol.SetInFormat("xyz"):
        raise RuntimeError("Open Babel does not support xyz input")
    obmol = ob.OBMol()
    if not xyz_to_obmol.ReadString(obmol, xyz_text):
        raise ValueError("Open Babel failed to read the XYZ input")
    if obmol.NumAtoms() == 0:
        raise ValueError("XYZ input contains no atoms")

    ob_to_sdf = ob.OBConversion()
    if not ob_to_sdf.SetOutFormat("sdf"):
        raise RuntimeError("Open Babel does not support sdf output")
    mol_block = ob_to_sdf.WriteString(obmol)
    mol = Chem.MolFromMolBlock(mol_block, sanitize=False, removeHs=False, strictParsing=False)
    if mol is None:
        raise ValueError("RDKit failed to convert the XYZ input into a molecule")
    if mol.GetNumAtoms() != obmol.NumAtoms():
        raise ValueError(
            "Atom count mismatch after xyz conversion: "
            f"Open Babel={obmol.NumAtoms()}, RDKit={mol.GetNumAtoms()}"
        )
    Chem.rdmolops.AssignStereochemistryFrom3D(mol)
    mol.UpdatePropertyCache(strict=False)
    return align_xyz_mol_with_units(mol)


def parse_molecule_payload(payload: dict[str, Any]) -> dict[str, Any]:
    input_type = str(payload.get("input_type", "smiles")).lower()
    if input_type == "smiles":
        smiles = str(payload.get("smiles", "")).strip()
        if not smiles:
            raise ValueError("SMILES is required")
        mol = mol_from_smiles(smiles)
        name = payload.get("name") or f"SMILES ({mol.GetNumAtoms()} atoms)"
    elif input_type == "xyz":
        xyz_text = str(payload.get("xyz_text", "")).strip()
        mol = mol_from_xyz_text(xyz_text)
        name = payload.get("name") or f"XYZ ({mol.GetNumAtoms()} atoms)"
    else:
        raise ValueError("input_type must be 'smiles' or 'xyz'")

    graph = molecule_to_graph(mol)
    return {
        "name": name,
        "input_type": input_type,
        "graph": graph,
        "atom_count": graph["atom_count"],
    }


def validate_job_request(payload: dict[str, Any]) -> dict[str, Any]:
    molecules = payload.get("molecules")
    if not isinstance(molecules, list) or not molecules:
        raise ValueError("At least one molecule is required")

    samples_per_molecule = int(payload.get("samples_per_molecule", 10))
    batch_size = int(payload.get("batch_size", samples_per_molecule))
    if samples_per_molecule <= 0:
        raise ValueError("samples_per_molecule must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    normalized = []
    for idx, mol_req in enumerate(molecules):
        input_type = str(mol_req.get("input_type", "smiles")).lower()
        if input_type not in {"smiles", "xyz"}:
            raise ValueError(f"molecule {idx}: input_type must be 'smiles' or 'xyz'")

        parsed = parse_molecule_payload(mol_req)
        reactive_atoms = parse_int_list(mol_req.get("reactive_atom_idx", ""))
        if not reactive_atoms:
            raise ValueError(f"molecule {idx}: reactive_atom_idx is required")
        if len(reactive_atoms) > 20:
            raise ValueError(f"molecule {idx}: at most 20 reactive atoms are supported")
        atom_count = parsed["atom_count"]
        for atom_idx in reactive_atoms:
            if atom_idx < 0 or atom_idx >= atom_count:
                raise ValueError(
                    f"molecule {idx}: atom index {atom_idx} is out of range for {atom_count} atoms"
                )

        item = {
            "name": str(mol_req.get("name") or parsed["name"]),
            "input_type": input_type,
            "reactive_atom_idx": reactive_atoms,
            "charge": int(mol_req.get("charge", 0)),
            "multiplicity": int(mol_req.get("multiplicity", 1)),
            "atom_count": parsed["atom_count"],
            "graph": parsed["graph"],
        }
        if input_type == "smiles":
            item["smiles"] = str(mol_req.get("smiles", "")).strip()
        else:
            item["xyz_text"] = str(mol_req.get("xyz_text", "")).strip()
        normalized.append(item)

    units_root = Path(payload.get("units_root") or os.environ.get("UNITS_ROOT", DEFAULT_UNITS_ROOT)).resolve()
    model_path = units_root / "units" / "model_path" / MODEL_TYPE / CKPT_FILE
    if not model_path.is_file():
        raise FileNotFoundError(f"UniTS-Lib HiEGNN checkpoint not found: {model_path}")

    return {
        "molecules": normalized,
        "samples_per_molecule": samples_per_molecule,
        "batch_size": min(batch_size, samples_per_molecule),
        "seed": payload.get("seed"),
        "save_full_trajectory": bool(payload.get("save_full_trajectory", True)),
        "units_root": str(units_root),
        "gaussian": {
            "nproc": int(payload.get("nproc", 16)),
            "mem": str(payload.get("mem", "32GB")),
            "method": str(payload.get("method", "b3lyp")),
            "basis": str(payload.get("basis", "def2svp")),
            "empirical_dispersion": str(payload.get("empirical_dispersion", "gd3bj")),
        },
    }


def append_log(job: JobState, line: str) -> None:
    clean = line.strip()
    if not clean:
        return
    with JOBS_LOCK:
        job.logs.append(clean)
        job.logs = job.logs[-400:]
        job.message = clean[-300:]


def terminate_process(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        proc.terminate()

    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def cancel_job(job: JobState) -> None:
    with JOBS_LOCK:
        if job.status not in {"queued", "running"}:
            return
        job.cancel_requested = True
        job.message = "Cancelling..."
        proc = job.current_process
    append_log(job, "[INFO] cancellation requested")
    if proc is not None:
        terminate_process(proc)


def run_subprocess(
    job: JobState,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    mol_idx: int,
    progress_start: float | None = None,
    progress_span: float | None = None,
) -> None:
    with JOBS_LOCK:
        if job.cancel_requested:
            raise JobCancelled("Job cancelled")
    append_log(job, "$ " + " ".join(cmd))
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **popen_kwargs,
    )
    with JOBS_LOCK:
        job.current_process = proc
        should_cancel = job.cancel_requested
    if should_cancel:
        terminate_process(proc)
    assert proc.stdout is not None
    latest_step = 0
    for raw_line in proc.stdout:
        line = raw_line.replace("\r", "\n")
        for part in line.splitlines():
            append_log(job, part)
            for match in re.finditer(r"(\d+)it\s*\[", part):
                latest_step = max(latest_step, min(DIFFUSION_STEPS, int(match.group(1))))
                if progress_start is not None and progress_span is not None:
                    progress = progress_start + (latest_step / DIFFUSION_STEPS) * progress_span
                else:
                    progress = (mol_idx + latest_step / DIFFUSION_STEPS) / max(1, job.total_molecules)
                with JOBS_LOCK:
                    job.progress = min(0.98, progress)
    return_code = proc.wait()
    with JOBS_LOCK:
        if job.current_process is proc:
            job.current_process = None
        was_cancelled = job.cancel_requested
    if was_cancelled:
        raise JobCancelled("Job cancelled")
    if return_code != 0:
        raise RuntimeError(f"Command failed with exit code {return_code}: {' '.join(cmd)}")


def build_env(units_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    old_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(units_root) + (os.pathsep + old_pythonpath if old_pythonpath else "")
    return env


def run_job(job: JobState) -> None:
    request = job.request
    units_root = Path(request["units_root"]).resolve()
    env = build_env(units_root)
    raw_root = job.root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)

    with JOBS_LOCK:
        job.status = "running"
        job.total_molecules = len(request["molecules"])
        job.progress = 0.0
        job.message = "Starting UniTS inference"

    try:
        processed_molecules = 0
        groups = [
            (input_type, [(idx, mol) for idx, mol in enumerate(request["molecules"]) if mol["input_type"] == input_type])
            for input_type in ("smiles", "xyz")
        ]

        for batch_idx, (input_type, items) in enumerate((group for group in groups if group[1])):
            with JOBS_LOCK:
                if job.cancel_requested:
                    raise JobCancelled("Job cancelled")
            molecule_indexes = [idx for idx, _ in items]
            with JOBS_LOCK:
                job.current_molecule = molecule_indexes[0]
                job.message = (
                    f"Running {input_type.upper()} batch {batch_idx + 1}: "
                    f"{len(items)} molecule(s)"
                )

            batch_dir = raw_root / f"batch_{batch_idx:03d}_{input_type}"
            input_dir = batch_dir / "input"
            output_dir = batch_dir / "output"
            input_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            (batch_dir / "index_map.json").write_text(
                json.dumps(molecule_indexes, indent=2),
                encoding="utf-8",
            )

            common = [
                "--reactive_atom_idx",
                *[",".join(str(v) for v in mol_req["reactive_atom_idx"]) for _, mol_req in items],
                "--charge",
                *[str(mol_req["charge"]) for _, mol_req in items],
                "--multi",
                *[str(mol_req["multiplicity"]) for _, mol_req in items],
                "--model_type",
                MODEL_TYPE,
                "--ckpt_file",
                CKPT_FILE,
                "--num_samples",
                str(request["samples_per_molecule"]),
                "--batch_size",
                str(request["batch_size"]),
                "--output_dir",
                str(output_dir),
                "--save_full_trajectory",
                "True" if request["save_full_trajectory"] else "False",
            ]
            if request.get("seed") not in {None, ""}:
                common.extend(["--seed", str(int(request["seed"]))])

            if input_type == "smiles":
                cmd = [
                    sys.executable,
                    "-m",
                    "units.infer_smiles",
                    "--smiles",
                    *[mol_req["smiles"] for _, mol_req in items],
                    *common,
                ]
            else:
                xyz_paths = []
                for molecule_index, mol_req in items:
                    xyz_path = input_dir / f"molecule_{molecule_index:03d}.xyz"
                    xyz_path.write_text(mol_req["xyz_text"].strip() + "\n", encoding="utf-8")
                    xyz_paths.append(str(xyz_path))
                cmd = [
                    sys.executable,
                    "-m",
                    "units.infer_xyz",
                    "--xyz",
                    *xyz_paths,
                    *common,
                ]

            progress_start = processed_molecules / max(1, job.total_molecules)
            progress_span = len(items) / max(1, job.total_molecules)
            run_subprocess(
                job,
                cmd,
                cwd=units_root,
                env=env,
                mol_idx=molecule_indexes[0],
                progress_start=progress_start,
                progress_span=progress_span,
            )
            generate_gjf_files(job, units_root, env, items, output_dir, request["gaussian"])
            processed_molecules += len(items)
            with JOBS_LOCK:
                job.progress = min(0.98, processed_molecules / max(1, job.total_molecules))

        with JOBS_LOCK:
            job.status = "completed"
            job.progress = 1.0
            job.message = "Completed"
            job.finished_at = time.time()
    except JobCancelled:
        with JOBS_LOCK:
            job.status = "cancelled"
            job.message = "Cancelled"
            job.finished_at = time.time()
            job.current_process = None
            job.logs.append("[INFO] job cancelled")
    except Exception as exc:
        with JOBS_LOCK:
            job.status = "failed"
            job.error = str(exc)
            job.message = str(exc)
            job.finished_at = time.time()
            job.current_process = None
            job.logs.append(traceback.format_exc())


def generate_gjf_files(
    job: JobState,
    units_root: Path,
    env: dict[str, str],
    items: list[tuple[int, dict[str, Any]]],
    output_dir: Path,
    gaussian: dict[str, Any],
) -> None:
    for reaction_idx, (molecule_index, mol_req) in enumerate(items):
        reaction_dir = output_dir / f"reaction_{reaction_idx:03d}"
        if not reaction_dir.is_dir():
            append_log(job, f"[WARN] missing output directory: {reaction_dir}")
            continue
        with JOBS_LOCK:
            job.current_molecule = molecule_index
        for xyz_path in sorted(reaction_dir.glob("gen_*.xyz")):
            if xyz_path.name.endswith("_traj.xyz") or xyz_path.name == "gen_all.xyz":
                continue
            gjf_path = xyz_path.with_suffix(".gjf")
            cmd = [
                sys.executable,
                "-m",
                "units.xyz2gjf",
                "--xyz",
                str(xyz_path),
                "--output",
                str(gjf_path),
                "--task_type",
                "direct_ts",
                "--nproc",
                str(gaussian["nproc"]),
                "--mem",
                str(gaussian["mem"]),
                "--method",
                str(gaussian["method"]),
                "--basis",
                str(gaussian["basis"]),
                "--empirical_dispersion",
                str(gaussian["empirical_dispersion"]),
                "--charge",
                str(mol_req["charge"]),
                "--multiplicity",
                str(mol_req["multiplicity"]),
            ]
            run_subprocess(job, cmd, cwd=units_root, env=env, mol_idx=molecule_index)


def relative_to_job(job: JobState, path: Path) -> str:
    return path.resolve().relative_to(job.root.resolve()).as_posix()


def collect_reaction_samples(
    job: JobState,
    reaction_dir: Path,
    molecule_index: int,
    molecule_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    if not reaction_dir.is_dir():
        return samples
    for xyz_path in sorted(reaction_dir.glob("gen_*.xyz")):
        if xyz_path.name.endswith("_traj.xyz") or xyz_path.name == "gen_all.xyz":
            continue
        stem = xyz_path.stem
        try:
            sample_index = int(stem.split("_")[-1])
        except ValueError:
            sample_index = len(samples)
        traj_path = xyz_path.with_name(f"{stem}_traj.xyz")
        gjf_path = xyz_path.with_suffix(".gjf")
        molecule_name = molecule_meta.get("name") or f"molecule {molecule_index}"
        samples.append(
            {
                "molecule_index": molecule_index,
                "sample_index": sample_index,
                "name": f"{molecule_name} / sample {sample_index}",
                "final_xyz": relative_to_job(job, xyz_path),
                "trajectory_xyz": relative_to_job(job, traj_path) if traj_path.is_file() else None,
                "gjf": relative_to_job(job, gjf_path) if gjf_path.is_file() else None,
                "molecule_graph": molecule_meta.get("graph"),
                "reactive_atom_idx": molecule_meta.get("reactive_atom_idx", []),
                "charge": molecule_meta.get("charge", 0),
                "multiplicity": molecule_meta.get("multiplicity", 1),
                "atom_count": molecule_meta.get("atom_count"),
            }
        )
    return samples


def collect_samples(job: JobState) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    raw_root = job.root / "raw"
    request_molecules = job.request.get("molecules", [])

    for batch_dir in sorted(raw_root.glob("batch_*")):
        index_map_path = batch_dir / "index_map.json"
        if not index_map_path.is_file():
            continue
        try:
            index_map = json.loads(index_map_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            index_map = []
        output_dir = batch_dir / "output"
        for reaction_idx, molecule_index in enumerate(index_map):
            molecule_meta = (
                request_molecules[molecule_index]
                if isinstance(molecule_index, int) and molecule_index < len(request_molecules)
                else {}
            )
            reaction_dir = output_dir / f"reaction_{reaction_idx:03d}"
            samples.extend(collect_reaction_samples(job, reaction_dir, molecule_index, molecule_meta))

    for mol_dir in sorted(raw_root.glob("molecule_*")):
        try:
            molecule_index = int(mol_dir.name.split("_")[-1])
        except ValueError:
            molecule_index = len(samples)
        molecule_meta = (
            request_molecules[molecule_index]
            if molecule_index < len(request_molecules)
            else {}
        )
        reaction_dir = mol_dir / "output" / "reaction_000"
        samples.extend(collect_reaction_samples(job, reaction_dir, molecule_index, molecule_meta))

    return sorted(samples, key=lambda item: (item["molecule_index"], item["sample_index"]))


def safe_job_file(job: JobState, rel_path: str) -> Path:
    rel_path = unquote(rel_path).lstrip("/")
    target = (job.root / rel_path).resolve()
    root = job.root.resolve()
    if target != root and root not in target.parents:
        raise ValueError("invalid file path")
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target


class UniTSWebHandler(SimpleHTTPRequestHandler):
    server_version = "UniTS-Web/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), format % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/health":
                return json_response(
                    self,
                    {
                        "ok": True,
                        "project_root": str(PROJECT_ROOT),
                        "default_units_root": str(DEFAULT_UNITS_ROOT),
                    },
                )
            if path.startswith("/api/jobs/"):
                return self.handle_job_get(path, parsed.query)
            return self.serve_static(path)
        except Exception as exc:
            return json_response(self, {"error": str(exc)}, status=500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = read_json(self)
            if parsed.path == "/api/molecule/parse":
                parsed_mol = parse_molecule_payload(payload)
                return json_response(self, parsed_mol)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
                with JOBS_LOCK:
                    job = JOBS.get(parts[2])
                if job is None:
                    return json_response(self, {"error": "job not found"}, status=404)
                cancel_job(job)
                return json_response(self, serialize_job(job))
            if parsed.path == "/api/jobs":
                request = validate_job_request(payload)
                job_id = uuid.uuid4().hex[:12]
                job_root = JOB_ROOT / job_id
                job_root.mkdir(parents=True, exist_ok=True)
                (job_root / "request.json").write_text(
                    json.dumps(request, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                job = JobState(id=job_id, request=request, root=job_root)
                with JOBS_LOCK:
                    JOBS[job_id] = job
                thread = threading.Thread(target=run_job, args=(job,), daemon=True)
                thread.start()
                return json_response(self, serialize_job(job), status=202)
            return json_response(self, {"error": "not found"}, status=404)
        except Exception as exc:
            return json_response(self, {"error": str(exc)}, status=400)

    def handle_job_get(self, path: str, query: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 3:
            return json_response(self, {"error": "job id is required"}, status=404)
        job_id = parts[2]
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if job is None:
            job_root = JOB_ROOT / job_id
            if not job_root.is_dir():
                return json_response(self, {"error": "job not found"}, status=404)
            request_path = job_root / "request.json"
            request = json.loads(request_path.read_text(encoding="utf-8")) if request_path.is_file() else {}
            job = JobState(id=job_id, request=request, root=job_root, status="completed")
            job.progress = 1.0

        if len(parts) == 3:
            return json_response(self, serialize_job(job))
        if len(parts) == 4 and parts[3] == "samples":
            return json_response(self, {"samples": collect_samples(job)})
        if len(parts) == 4 and parts[3] == "file":
            params = parse_qs(query)
            rel = params.get("path", [""])[0]
            file_path = safe_job_file(job, rel)
            return self.serve_file(file_path)
        return json_response(self, {"error": "not found"}, status=404)

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            target = STATIC_ROOT / "index.html"
        else:
            rel = unquote(path).lstrip("/")
            target = (STATIC_ROOT / rel).resolve()
            if STATIC_ROOT.resolve() not in target.parents and target != STATIC_ROOT.resolve():
                return text_response(self, "Forbidden", HTTPStatus.FORBIDDEN)
        if not target.is_file():
            return text_response(self, "Not found", HTTPStatus.NOT_FOUND)
        self.serve_file(target)

    def serve_file(self, target: Path) -> None:
        content_type = "application/octet-stream"
        suffix = target.suffix.lower()
        if suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif suffix == ".svg":
            content_type = "image/svg+xml"
        elif suffix in {".xyz", ".gjf", ".log"}:
            content_type = "text/plain; charset=utf-8"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if suffix in {".xyz", ".gjf", ".sdf"}:
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.end_headers()
        self.wfile.write(body)


def serialize_job(job: JobState) -> dict[str, Any]:
    with JOBS_LOCK:
        return {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "message": job.message,
            "error": job.error,
            "logs": job.logs[-120:],
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "current_molecule": job.current_molecule,
            "total_molecules": job.total_molecules,
            "cancel_requested": job.cancel_requested,
        }


def main() -> None:
    global DEFAULT_UNITS_ROOT
    parser = argparse.ArgumentParser(description="Run the UniTS-Web server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--units-root", default=str(DEFAULT_UNITS_ROOT))
    args = parser.parse_args()

    DEFAULT_UNITS_ROOT = Path(args.units_root).resolve()
    JOB_ROOT.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.host, args.port), UniTSWebHandler)
    print(f"UniTS-Web running at http://{args.host}:{args.port}")
    print(f"Using UniTS root: {DEFAULT_UNITS_ROOT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
