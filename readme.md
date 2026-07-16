# Downtime IV — Compounding Worksheets & Labels

An **offline**, Windows-first local app for IV compounding workflows. Current scope includes:
- FastAPI + Jinja2 shell UI (Guest + Admin login)
- Rules loader with cross-checks and **SHA-256 integrity badge**
- `/compute` API with explicit selections, solvent resolution, core volume/concentration math, and capacity checks

PDF generation, step assembly, and the admin editor are **planned** and not yet wired.
The fine-grained checklist and the in-flight milestone spec live in [TODO.md](TODO.md).

### Compute Engine
- [x] Stock concentration calculation for solution meds.
- [x] Stock concentration calculation for powder meds (uses `conc_after_recon_mg_per_ml` when present).
- [x] Explicit user selection (no auto-selection of medications/containers/solvents).
- [x] Solvent resolution for prefilled vs empty/syringe containers, including prefilled-solvent compatibility (hard 422).
- [x] `allowed_container_kinds` enforced per medication (hard 422).
- [x] Signed `container_adjustment_vol_ml` (withdraw headroom / add diluent); negative adjustment rejected for empty containers/syringes, and withdrawal cannot exceed prefill volume.
- [x] Final product volume and concentration from container start volume + adjustment + drug volume; final volume must be > 0 (hard stop).
- [x] Container capacity checks (bags/bottles) and syringe usable volume enforcement — **hard stops (HTTP 422)**, not warnings.
- [x] Pydantic BaseModel inputs/outputs with strict validation.
- [x] PHI separation in API responses (patient fields not echoed).
- [x] `container_empty` type support for generic empty containers.
- [ ] Concentration range validation → warnings.
- [ ] Powder volume math (vials/reconstitution/leftover) in the active compute path.
- [ ] Multiple preparations (`num_preparations`) in the active compute path.
- [ ] Round to 0.1 mL precision (or configurable rounding).
- [ ] Step assembly from `steps_library.yaml` + `sequences.yaml`.
- [ ] Auto-upsize container selection; surface "Changed to X mL bag" note.

### Rules & Integrity
- [x] ~~Pydantic models with field validation~~
- [x] ~~YAML loaders with duplicate detection~~
- [x] ~~Cross-file validation (solvent references, container compatibility)~~
- [x] ~~SHA-256 integrity checking vs rules_manifest.yaml~~
- [x] ~~Rules badge display in UI (e.g., `Rules YYYY.MM.DD • abc123`)~~
- [x] ~~Startup integrity verification and console logging~~
- [x] ~~JSON API endpoint (`GET /rules/status`) with structured health data~~
- [x] ~~RulesStatus Pydantic model for API responses~~
- [ ] JSON Schema for YAML; friendly errors surfaced in UI.
- [ ] `/editor/validate` + `/editor/freeze` endpoints; write manifest; bump `rules_version`.
- [ ] Rules badge everywhere (page header & PDF footer).

> **Clinical safety note**  
> This tool assists trained pharmacy staff. Does **not** replace clinical judgment. Validate outputs via SOP, double-check concentrations/compatibility, and follow local policies.

---

## Table of Contents

- [Goals](#goals)
- [Architecture](#architecture)
- [Roles & Access](#roles--access)
- [Data Pack](#data-pack)
  - [Files](#files)
  - [Sample snippets](#sample-snippets)
  - [Integrity & Version Badge](#integrity--version-badge)
- [Compute & Steps (overview)](#compute--steps-overview)
- [Multiple Preparations (planned)](#multiple-preparations-planned)
- [PDFs & Printing](#pdfs--printing)
- [Endpoints](#endpoints)
- [Local Development](#local-development)
- [Packaging & Distribution](#packaging--distribution)
- [Editing Workflow (Admin)](#editing-workflow-admin)
- [Backups & Rollback](#backups--rollback)
- [Security, Privacy, Logging](#security-privacy-logging)
- [Testing](#testing)
- [Milestones & Status](#milestones--status)
- [Project TODO (living)](TODO.md) — separate file
- [Contributing](#contributing)
- [License](#license)

---

## Goals

- **Downtime resilience:** Works on isolated hospital PCs, no internet needed.  
- **Zero-admin install:** PyInstaller one-folder exe; no DB server.  
- **Safety rails:** Concentration limits, solvent compatibility, and integrity checks.  
- **User-maintained rules:** Human-readable YAML; in-app editor for non-technical staff.  
- **Output parity:** PDFs mirror SOP wording; golden test cases match current Excel outcomes.

---

## Architecture

- **Runtime:** Python 3.11 → PyInstaller (Windows one-folder).  
- **Server/UI:** FastAPI (local `127.0.0.1:<port>`), Jinja2 templates, HTMX (minimal JS).  
- **PDFs:** ReportLab (A4 worksheet + label).  
- **Data:** `rules/` YAML pack + `rules_manifest.yaml` (version + SHA-256 per file).  
- **State:** Stateless sessions via signed cookie; no DB.

Folder sketch:

```
downtime-iv-system/
├─ app/
│  ├─ main.py                # routes, views, /compute
│  ├─ auth.py                # passphrase + signed cookie sessions
│  ├─ rules_loader.py        # YAML → models, cross-checks, hashing, badge
│  ├─ compute.py             # active compute path
│  ├─ compute_request.py     # legacy prototype (not wired)
│  └─ views/                 # home_guest.html, home_admin.html, admin_login.html
├─ rules/                    # active data pack (per machine)
├─ requirements.txt
├─ .env
└─ readme.md
```

---

## Roles & Access

- **Guest (default):** shell UI only; compute UI/preview not wired yet.  
- **Admin:** login via passphrase; placeholder admin page (editor planned).  
- **Session:** `dv_sess` signed cookie (itsdangerous), idle timeout (default **15 min**, sliding).  
- **.lock:** planned for editor concurrency; not implemented yet.

---

## Data Pack

All rules live under `rules/`. Pharmacy staff can update YAML safely via the Admin editor.

### Files

- `solvents.yaml` — NS/D5/WFI; flags for reconstitution/dilution.  
- `containers.yaml` — prefilled bags/bottles (with headspace), empty bags, syringes (usable_fraction), and generic empty containers.  
- `medications.yaml` — presentation (solution/powder), stock, reconstitution, conc range, allowed solvents/container kinds, stability.  
- `steps_library.yaml` — templated step texts with optional `when:` (Jinja).  
- `sequences.yaml` — named ordered lists of steps (e.g., `bag_standard`).  
- `rules_manifest.yaml` — `rules_version` + file→sha256 map.

**Note:** `steps_library.yaml` and `sequences.yaml` are currently loaded only for integrity checking; step assembly is not yet wired.

### Sample snippets

**solvents.yaml**
```yaml
- id: NS
  name: "0.9% Sodium Chloride"
  for_reconstitution: true
  for_dilution: true
- id: D5
  name: "Dextrose 5%"
  for_reconstitution: false
  for_dilution: true
- id: WFI
  name: "Water for Injection"
  for_reconstitution: true
  for_dilution: false
```

**containers.yaml** (headspace allowed: `prefill_volume_ml <= capacity_ml`)
```yaml
- id: bag_ns_250
  name: "250 mL Normal Saline Bag"
  kind: bag_prefilled
  capacity_ml: 350
  prefill_volume_ml: 250
  solvent: NS

- id: bag_empty_250
  name: "250 mL Empty IV Bag"
  kind: bag_empty
  capacity_ml: 250

- id: syringe_50ml
  name: "50 mL Syringe"
  kind: syringe
  capacity_ml: 50.0
  usable_fraction: 0.8

- id: deltec_cassette_100ml
  name: "Deltec Cassette 100 mL"
  kind: container_empty
  capacity_ml: 100.0
```

**Container Types:**
- `bag_prefilled`: Pre-filled bags with existing solvent. Has `prefill_volume_ml` and `solvent` fields. Headspace = `capacity_ml - prefill_volume_ml`.
- `bag_empty`: Empty bags requiring user-specified solvent. No prefill volume.
- `bottle_prefilled`: Pre-filled bottles with existing solvent, similar to bag_prefilled.
- `syringe`: Syringes with `usable_fraction` (typically 0.8 for 80% usable volume). Usable volume = `capacity_ml * usable_fraction`.
- `container_empty`: Generic empty containers (vials, cassettes, etc.) requiring user-specified solvent. Behaves like bag_empty but for custom containers.

**medications.yaml** (examples with new schema)
```yaml
# Example 1: Solution medication with mg units
- id: OXALIPLATIN
  name: Oxaliplatin
  presentation: solution
  stock:
    strength: 100
    unit: mg
    volume_ml: 20
  reconstitution:
    required: false
  conc_limit_mg_per_ml:
    min: 0.2
    max: 1.0
  allowed_solvents: [D5]
  allowed_container_kinds: [bag_prefilled]
  stability:
    general_hours: 168

# Example 2: Powder medication with special reconstitution concentration
- id: TRASTUZUMAB
  name: Trastuzumab
  presentation: powder
  stock:
    strength: 440
    unit: mg
  reconstitution:
    required: true
    diluent: WFI
    volume_ml: 20
    conc_after_recon_mg_per_ml: 21  # Special case: differs from strength/volume
  conc_limit_mg_per_ml:
    min: 1.0
    max: 1.0
  allowed_solvents: [NS]
  allowed_container_kinds: [syringe]
  stability:
    general_hours: 144
```

**steps_library.yaml** (extract)
```yaml
library:
  step.gather_container: "Gather {{ container_name }}."

  step.reconstitute_each:
    when: "{{ presentation == 'powder' and reconst_per_vial_ml is defined }}"
    text: "Add {{ reconst_per_vial_ml|round(1) }} mL {{ reconst_diluent }} to each vial (×{{ n_vials }}). Swirl to dissolve."

  step.prepare_stock:
    text: >
      Prepare stock {{ stock_conc_mg_per_ml|round(1) }} mg/mL
      {% if presentation == 'powder' %}(vials: {{ n_vials }}, total ~{{ stock_total_ml|round(1) }} mL){% endif %}.

  step.prewithdraw:
    when: "{{ v_withdraw_ml|default(0) > 0 }}"
    text: "Withdraw {{ v_withdraw_ml|round(1) }} mL from the container to create headroom."

  step.inject_mix:
    text: "Inject into the container. Invert {{ invert_times|default(10) }} times to mix."

  step.inspect_label:
    text: >
      Inspect the final solution. Affix a label with drug, dose, final concentration,
      total volume, solvent, container, and beyond-use date/time.

  extra.vented_spike: "Use a vented spike if required by SOP."
  extra.light_protect: "Apply light-protective cover if indicated."
  extra.stability_note: "Respect stability limits: {{ stability_note }}."
  extra.shake_to_mix:
    when: "{{ mix_style == 'shake' }}"
    text: "Shake gently to mix."
  extra.cold_storage:
    when: "{{ requires_cold|default(false) }}"
    text: "Store in refrigerator if required."
```

**sequences.yaml**
```yaml
defaults:
  bag_standard:
    - step.gather_container
    - step.reconstitute_each
    - step.prepare_stock
    - step.prewithdraw
    - step.withdraw_drug
    - step.inject_mix
    - step.inspect_label
```

### Integrity & Version Badge

- `rules_manifest.yaml` keeps `rules_version: "YYYY.MM.DD"` and SHA-256 for each file.  
- On startup and pre-export, hashes are recomputed and compared.  
- UI shows badge:  
  - **Match:** `Rules 2025.08.28 • abc123`  
  - **Mismatch:** `Rules 2025.08.28 • MISMATCH` (blocks export).

---

## Compute & Steps (overview)

Active compute flow (`app/compute.py`, wired to `/compute`):
- Explicit IDs for medication/container/solvent (prefilled containers supply solvent; empty/syringe require user solvent).
- Stock concentration calculation for solution and powder meds (powder uses `conc_after_recon_mg_per_ml` when present); mcg strengths normalized to mg via `Stock.strength_mg()`.
- Drug volume from dose ÷ stock concentration; container start volume (prefill or 0).
- **Signed adjustment model**: the pharmacist enters `container_adjustment_vol_ml` directly — negative = withdraw headroom (prefilled containers only), positive = add diluent. There is no target-concentration input; the system reports the resulting concentration instead of solving for one.
- Final volume = start + adjustment + drug volume; must be positive (hard stop).
- Capacity checks (bag/bottle capacity, syringe usable volume) as hard stops.
- Pydantic models with strict validation; `DomainError` returns HTTP 422 with structured detail.
- Output includes core volumes/concentration and `stock_conc_mg_per_ml` (diagnostic, also useful for label/worksheet); powder and multi-prep fields are placeholders for now.

Not yet implemented in the active path (spec in [TODO.md](TODO.md)):
- Concentration range validation (planned as a warning, not a hard stop) and compatibility warnings.
- Powder vial math (vials needed, reconstitution volumes, leftover).
- Multiple preparations scaling (`num_preparations`).
- Rounding to 0.1 mL.
- Step assembly from `steps_library.yaml` + `sequences.yaml` (`when:` gating, Jinja2 `StrictUndefined`, render failures collected into `errors` rather than HTTP 500).

Legacy/prototype note: `app/compute_request.py` contains an older compute prototype with broader math but is not wired to the API and does not match the current rule models.

---

## Multiple Preparations (planned)

Design goal: support **batch compounding** where multiple identical preparations are calculated together.

Current status:
- `num_preparations` exists in `ComputeInput`, but the active compute path does not yet scale volumes/vials.
- A legacy prototype in `app/compute_request.py` explores the intended math, but it is not wired to the API.

Planned behavior:
- Scale dose and drug volumes across all preparations.
- Optimize vial count for powder meds to minimize waste.
- Provide per-prep and total batch outputs.

---

## PDFs & Printing

Planned (not implemented yet):
- **Worksheet:** A4 portrait with header (hospital + patient fields), numbered steps, warnings, footer badge.
- **Label:** v1 fixed preset (e.g., **100 × 50 mm**) formatted for Datamax; future presets can be added.
- **Preview:** embed PDF in browser viewer (pdf.js or native).
- **Export:** save to `outputs/` with deterministic filenames.

---

## Endpoints

### Core Routes
- `GET /` → Guest home (shell UI).  
- `GET /admin/login` → login form.  
- `POST /admin/login` → validate passphrase → set signed cookie → 303 to `/admin`.  
- `GET /admin` → Admin home (requires fresh session).  
- `POST /admin/logout` → clear session.  

### API Routes  
- `GET /rules/status` → **JSON health check** with integrity status, version, counts, and errors.  
- `POST /compute` → compute volumes/concentration with explicit selections; returns `ComputeOutput`.

**Real-time Calculation (planned):**
- Field changes will trigger recalculation via `POST /compute` once the UI is wired.

### API Models (Pydantic)

#### ComputeInput
**Fields (IDs must be explicitly provided):**
```python
medication_id: str      # Must match medication ID from medications.yaml
container_id: str       # Must match container ID from containers.yaml
solvent_id: Optional[str] = None  # Required for empty/syringe containers; must match allowed_solvents
dose_mg: float          # Dose in milligrams (unit conversion handled in Stock.strength_mg())
patient_name: Optional[str] = None    # For PDF only (not persisted)
patient_hrn: Optional[str] = None     # Patient identifier for PDF (not persisted)
container_adjustment_vol_ml: float = 0.0  # mL to add/subtract to adjust final concentration
num_preparations: int = 1             # Number of identical preparations to make
```

**User Input Workflow:**
1. User provides medication, container, solvent, and dose (required)
2. System calculates initial drug volume and concentration in real-time
3. User optionally enters `container_adjustment_vol_ml` to adjust final concentration
4. System recalculates final concentration and volume in real-time as user types
5. Supports both addition (positive values) and subtraction (negative values) of solvent volume

**Notes:**
- `container_adjustment_vol_ml` is applied to final volume; negative values are rejected for empty containers/syringes.
- `num_preparations` is accepted but not yet applied in the active compute path.

**User Selection Requirements:**
- **No auto-selection:** Users must explicitly choose `medication_id` and `container_id` (and `solvent_id` when required)
- **Validation:** All IDs validated against loaded YAML rules; errors return HTTP 422 with structured detail
- **Solvent requirement:** Solvent required for empty/syringe containers; auto-provided for prefilled containers
- **PHI handling:** Patient information included in input but excluded from ComputeOutput

#### DomainError
**Custom exception for domain-specific validation errors:**
```python
@dataclass
class DomainError(Exception):
    code: str              # Machine-readable error code
    message: str           # User-friendly error message
    field: Optional[str] = None      # Which field caused the error
    hint: Optional[str] = None       # Guidance for fixing the error
    context: Optional[dict] = None   # Additional context (IDs, values, etc.)
```

**HTTP 422 Response Example:**
```json
{
  "detail": {
    "code": "incompatible_solvent_selected",
    "message": "Selected solvent is not allowed for this medication.",
    "field": "solvent_id",
    "hint": "Pick from the allowed list of solvents: NS, D5.",
    "context": {"medication_id": "PACLITAXEL", "solvent_id": "WFI"}
  }
}
```

#### ComputeOutput
**Result of computation (PHI-free for API responses):**
```python
# Input echo
dose_mg: float
num_preparations: int
container_adjustment_vol_ml: float

# IDs and names
medication_id: str
medication_name: str
container_id: str
container_name: str
solvent_id: str
solvent_name: str
solvent_source: Literal["container_prefill", "user_selection"]  # Where solvent came from

# Core computed values
drug_volume_ml: Optional[float]  # mL of drug solution to add
final_product_conc_mg_per_ml: Optional[float]  # Final concentration after all adjustments
final_product_vol_ml: Optional[float]  # Final product volume
stock_conc_mg_per_ml: Optional[float]  # Stock concentration for reference

# Powder medication fields
required_num_vials_per_preparation: int
reconst_per_vial_vol_ml: Optional[float]
reconst_vial_conc_mg_per_ml: Optional[float]
reconst_vial_total_vol_ml: Optional[float]
reconst_vial_total_leftover_vol_ml: Optional[float]

# Multiple preparations fields
total_required_drug_volume_ml: Optional[float]
total_vials_needed: int
total_dose_mg_required: Optional[float]

# Safety validation
warnings: List[str]
errors: List[str]
steps: List[str]
concentration_in_range: Optional[bool]
solvent_compatible: Optional[bool]
```

**Placeholders in current output:**
- Powder and multi-prep fields are not yet computed (placeholders only).
- `steps` is empty until step assembly is wired.
- `stock_conc_mg_per_ml` is populated (diagnostic; also used on the label/worksheet later).

**PHI Separation:**
- ComputeOutput excludes all patient data (name, HRN, etc.) for API safety
- Patient info from ComputeInput is used for PDF generation only
- Ensures PHI never appears in JSON API responses or logs  

### Admin Routes (planned)
- `GET /editor/*` → Admin editor pages.  
- `POST /editor/validate` → run schema + cross-checks.  
- `POST /editor/freeze` → recompute hashes, bump version, write manifest + changelog.  
- `POST /datapack/import` → validate + install ZIP; backup current.  
- `POST /rollback` → restore previous pack.

#### Rules Status API Response
```json
{
  "version": "2025.09.11",
  "integrity": "ok|mismatch|missing", 
  "badge": "Rules 2025.09.11 • a1b2c3",
  "counts": {"meds": 5, "containers": 20, "solvents": 3},
  "num_errors": 0,
  "errors": []
}
```

---

## Local Development

**Prereqs:** Python 3.11+, Git. On macOS/Linux adapt activation commands.

```bash
# create & activate venv
python -m venv downtime
source downtime/bin/activate

# install deps
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# env
# edit .env (create if missing)
# .env keys used:
# ADMIN_PASSPHRASE=...
# SESSION_SECRET=... (long random)
# LOCK_TIMEOUT_MIN=15
# APP_PORT=8765

# run
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

---

## Packaging & Distribution

Planned (not wired yet):
- **PyInstaller** one-folder exe (Windows): bundles Python runtime + app + fonts; rules live beside exe under `rules/`.  
- **Unsigned pilot** first; later add code-signing if IT requires.  
- **Import Data Pack**: ZIP file → validates → installs → auto-backup old pack.

---

## Editing Workflow (Admin)

Planned workflow (not implemented yet):
1. Enter Admin (passphrase).  
2. Edit meds/containers/solvents/steps via forms or upload YAML.  
3. **Validate**: schema + cross-checks; show **diff** preview.  
4. **Freeze**: write new hashes to manifest, bump version (date), append to `rules/CHANGELOG.txt`.  
5. Editor auto-logout after 15 min idle; `.lock` expires automatically if abandoned.

---

## Backups & Rollback

Planned:
- Each freeze stores a copy under `rules_backup/<version>/`.  
- One-click rollback to last known good pack.

---

## Security, Privacy, Logging

- **No PHI persisted**: patient fields live in memory/PDF only.  
- **Cookies:** `HttpOnly`, `SameSite=Lax`; localhost in pilot.  
- **Logging:** minimal console output today (rules load + integrity). Structured operational logging and rotation are planned.  
- **Offline by design:** no third-party calls.

---

## Testing

Direction (decided): **pytest is the real suite**; curl is only for quick manual smoke tests.

- **Unit tests (pytest):** positive + negative cases for each medication presentation
  (solution/powder), each container kind (prefilled, empty, syringe), solvent policy errors,
  capacity overflow, invalid adjustments (withdraw from empty, final volume ≤ 0), and
  step rendering (`when:` gates, missing-variable handling).
- **Golden cases:** 5–10 pharmacy scenarios with expected volumes/warnings.
- **Excel parity:** generate from current Excel and compare numerically (± rounding).
- **Loader tests:** schema + cross-checks with valid/invalid YAML; PDF layout smoke tests later.

The concrete test checklist lives in [TODO.md](TODO.md).

---

## Milestones & Status

**M1 – Skeleton & Login** ✅ **COMPLETE**  
Basic routes, passphrase auth, signed session, idle timeout.

**M2 – Rules Loader & Integrity** ✅ **COMPLETE**  
Pydantic models, cross-checks, SHA-256 badge, startup wiring, JSON status API.

**M3 – Compute API (core math + steps)** 🟡 **IN PROGRESS**  
M3.T1 (solvent resolution + stock concentration helpers) done. M3.T2 (core math, reworked around
the manual signed-adjustment design) mostly done: drug volume, start volume, signed adjustment,
final volume/concentration, and hard-stop capacity checks are in. Remaining in T2: concentration
range warning, powder vial math, multi-prep totals, rounding. Then M3.T3: step assembly.
Detailed spec in [TODO.md](TODO.md).

**M4 – PDFs & Preview** 🔜  
Planned. A4 + label PDFs via ReportLab, embedded preview, footer badge.

- **M5 – Admin Editor** 🔜  
  Forms, Validate & Freeze, diff/changelog, .lock + timeout.

- **M6 – Distribution** 🔜  
  PyInstaller build, Import/Export/Backup/Rollback flows.

**M7 – Testing & Parity** 🔜  
Planned.

**Current Status (as of 2026-07-15):**  
✅ Rules loader + integrity badge + `/rules/status` API  
✅ Admin login + signed-cookie sessions (placeholder admin UI)  
✅ `/compute` endpoint with explicit selections, solvent resolution, signed adjustment, final volume/concentration, and hard-stop capacity checks  
📍 Finishing M3.T2 (concentration warning, powder vial math, multi-prep totals, rounding), then M3.T3 step assembly; UI wiring, PDFs, and admin editor after

---

## Project TODO

The living TODO list — including the detailed spec for the milestone currently in flight
(M3.T2 core math and M3.T3 steps assembly) — lives in **[TODO.md](TODO.md)**.

---

## Contributing

- Keep YAML **IDs stable**; prefer `UPPER_SNAKE`.  
- Submit PRs with:
  - updated **sample_rules** (if applicable),
  - unit tests for loader/compute changes,
  - screenshots of PDF changes (if layout affected).

---

## License

Internal pilot. License to be defined before wider distribution.


---

## Windows packaging with PyInstaller (planned)

Planned workflow (not set up in repo yet). This section is a draft.

**Prereqs**
- Windows 10/11, Python 3.11+, this repo cloned locally.
- Create a venv and install deps (same as development):

```powershell
python -m venv .venv
. .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
```

**Entry script (recommended)**
Create a small launcher `run.py` at the repo root to start Uvicorn programmatically (not in repo yet):

```python
# run.py
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", "8765"))
    uvicorn.run("app.main:app", host="127.0.0.1", port=port)
```

**One-folder build command**
Run from the repo root in PowerShell (Windows path separator rules apply to `--add-data`):

```powershell
pyinstaller -y --clean --noconsole \
  --name DowntimeIV \
  --add-data "app\\views;app\\views" \
  --add-data "app\\static;app\\static" \
  --add-data "rules;rules" \
  --add-data "fonts;fonts" \
  run.py
```

Notes:
- On Windows, `--add-data` uses `SRC;DEST`. (On macOS/Linux it is `SRC:DEST`.)
- Include `rules` so a seed data pack is shipped. Admins can later import updates.
- If you see "template not found", ensure the `app\\views` add-data entry is correct.
- If a module is missed at runtime, add a `--hidden-import <module>` (rare with this stack).

**Where files land**
- Output in `dist/DowntimeIV/` (one-folder). Run `DowntimeIV.exe`.
- The bundled data is available relative to the executable. Code that locates `rules/` should first look for a sibling directory next to the executable and fall back to the source layout. (This repo currently uses a dev path; M6 will add a packaged-path fallback.)

**Quick test**
1. Double-click `dist/DowntimeIV/DowntimeIV.exe`.
2. Open `http://127.0.0.1:8765`.
3. Confirm the Rules badge shows and pages render.

**Troubleshooting**
- *Port already in use*: set `APP_PORT` in `.env` before launching.
- *Template not found*: verify `--add-data "app\\views;app\\views"` and paths.
- *Rules mismatch*: ensure the manifest hashes match or run the Admin Freeze step (M5) after editing.
- *Missing fonts*: add them to a `fonts/` folder and include via `--add-data`.
