# Downtime IV — Compounding Worksheets & Labels

An **offline**, Windows-first app that generates IV medication **compounding instruction worksheets (A4 PDF)** and **sticky labels (Datamax label PDF)** for downtime scenarios. Runs as a **local HTTP app** (FastAPI + Jinja2), with **Guest** (read-only) and **Admin** (editor) roles. Rules are **YAML data packs** with **SHA-256 version badges**.

### Compute Engine
- [x] ~~Solution medication volume calculations~~
- [x] ~~Powder medication logic: vial calculations, reconstitution volumes~~  
- [x] ~~Concentration validation against medication ranges~~
- [x] ~~Solvent compatibility checking~~
- [x] ~~Safety warnings for out-of-range concentrations and incompatible solvents~~
- [x] ~~Syringe usable volume (`capacity * usable_fraction`)~~
- [x] ~~Round to 0.1 mL precision~~
- [x] ~~Multiple preparations support with batch vial optimization~~
- [x] ~~Pydantic BaseModel conversion with strict validation~~
- [x] ~~Explicit user selection (no auto-selection of medications/containers/solvents)~~
- [x] ~~PHI separation in API responses~~
- [x] ~~Container_empty type support for generic empty containers~~
- [ ] Headroom logic: compute `v_withdraw_ml` vs available headspace.
- [ ] Auto-upsize container selection; surface "Changed to X mL bag" note.
- [ ] Step assembly from `steps_library.yaml` + `sequences.yaml`.

### Rules & Integrity
- [x] ~~Pydantic models with field validation~~
- [x] ~~YAML loaders with duplicate detection~~
- [x] ~~Cross-file validation (solvent references, container compatibility)~~
- [x] ~~SHA-256 integrity checking vs rules_manifest.yaml~~
- [x] ~~Rules badge display in UI (`Rules 2025.09.11 • 07136e`)~~
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
- [PDFs & Printing](#pdfs--printing)
- [Endpoints](#endpoints)
- [Local Development](#local-development)
- [Packaging & Distribution](#packaging--distribution)
- [Editing Workflow (Admin)](#editing-workflow-admin)
- [Backups & Rollback](#backups--rollback)
- [Security, Privacy, Logging](#security-privacy-logging)
- [Testing & Excel Parity](#testing--excel-parity)
- [Milestones & Status](#milestones--status)
- [Project TODO (living)](#project-todo-living)
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
- **Fonts:** Bundled sans (Helvetica-compatible) for consistent layout.

Folder sketch:

```
downtime-iv-system/
├─ app/
│  ├─ main.py                # routes, views
│  ├─ auth.py                # passphrase + signed cookie sessions
│  ├─ rules_loader.py        # YAML → models, cross-checks, hashing, badge
│  ├─ views/                 # home_guest.html, home_admin.html, admin_login.html
│  └─ static/                # optional CSS/JS
├─ rules/                    # active data pack (per machine)
├─ outputs/                  # generated PDFs (gitignored)
├─ sample_rules/             # known-good examples for tests/CI
├─ .env.example
├─ pyproject.toml / requirements.txt
└─ README.md
```

---

## Roles & Access

- **Guest (default):** compute, preview, print. No edits.  
- **Admin:** login via passphrase; sees Editor, Validate & Freeze, Import/Export, Rollback.  
- **Session:** `dv_sess` signed cookie (itsdangerous), idle timeout (default **15 min**, sliding).  
- **.lock:** during edits, `rules/.lock` prevents concurrent changes; auto-expires after 15 min.

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

**containers.yaml** (headspace allowed: `prefill_ml <= capacity_ml`)
```yaml
- id: bag_ns_250
  kind: bag_prefilled
  capacity_ml: 350
  prefill_ml: 250
  solvent: NS

- id: empty_bag_250
  kind: bag_empty
  capacity_ml: 250

- id: syringe_50
  kind: syringe
  capacity_ml: 50
  usable_fraction: 0.8

- id: deltec_cassette_100ml
  kind: container_empty
  capacity_ml: 100.0
```

**Container Types:**
- `bag_prefilled`: Pre-filled bags/bottles with existing solvent. Has `prefill_ml` and `solvent` fields.
- `bag_empty`: Empty bags requiring user-specified solvent. No prefill volume.
- `bottle_prefilled`: Pre-filled bottles with existing solvent, similar to bag_prefilled.
- `syringe`: Syringes with `usable_fraction` (typically 0.8 for 80% usable volume).
- `container_empty`: Generic empty containers (vials, custom containers) requiring user-specified solvent. Behaves like bag_empty but with different naming for pharmacy workflows.

**medications.yaml** (examples abbreviated)
```yaml
- id: OXALIPLATIN
  name: Oxaliplatin
  presentation: solution
  stock: { amount_mg: 100, volume_ml: 20 }
  reconstitution: { required: false }
  conc_mg_per_ml: { min: 0.2, max: 1.0 }
  allowed_solvents: [D5]
  allowed_container_kinds: [bag_prefilled, bag_empty, container_empty]
  stability: { general_hours: 168 }

- id: AZACITIDINE
  name: Azacitidine
  presentation: powder
  stock: { amount_mg: 100 }
  reconstitution: { required: true, diluent: WFI, volume_ml: 4, note: "Cold WFI" }
  conc_mg_per_ml: { min: 25, max: 25 }
  allowed_solvents: [WFI]
  allowed_container_kinds: [syringe]
  stability: { general_hours: 8 }
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

- **Explicit User Selection:** All fields now require explicit user input - no auto-selection of containers, solvents, or medications. Users must choose all parameters to ensure clinical safety and accountability.
- **Multiple Preparations:** Support for batch compounding scenarios (e.g., 3 identical syringes). System optimizes vial usage across all preparations to minimize waste.
- **Pydantic Validation:** ComputeInput and ComputeResult models use Pydantic BaseModel for strict field validation, type checking, and OpenAPI documentation generation.
- **PHI Separation:** Patient Health Information (PHI) is separated from main compute results. ComputeResult excludes patient data; PHI is handled separately via PDFContext for PDF generation only.
- **Rounding:** 0.1 mL (configurable).  
- **Auto-upsize:** if final volume > capacity or headroom insufficient → next suitable container.  
- **Final volume default:** equals container prefill (Admin can enable override per med/session).  
- **Concentration checks:** warn or block based on policy (out-of-range → warn; incompatible solvent → block).  
- **Powder workflow:** compute `n_vials`, `reconst_per_vial_ml`, pooled `stock_total_ml`, and `stock_leftover_ml`.  
- **Batch optimization:** For powder medications, calculates minimum vials needed across all preparations to reduce waste.
- **Steps assembly:** sequence + conditional steps + per-med insertions → render to PDF text blocks.

---

## Multiple Preparations Feature

The system supports **batch compounding scenarios** where pharmacists need to prepare multiple identical preparations (e.g., 3 syringes of the same medication for a patient or multiple patients with the same dosing).

### Key Benefits

- **Vial Optimization**: Automatically calculates the minimum number of vials needed across all preparations to minimize waste
- **Batch Efficiency**: Computes total volumes and doses for the entire batch
- **Cost Savings**: Reduces medication waste by optimizing reconstituted medication usage
- **Time Savings**: Single calculation session for multiple identical preparations

### How It Works

**Input**: Add `num_preparations` field to specify how many identical preparations to make (default: 1)

**For Solution Medications**:
- Calculates total drug volume needed across all preparations
- Determines minimum vials required based on total volume
- Provides per-preparation and total calculations

**For Powder Medications**:
- Calculates total dose needed: `total_dose_mg = dose_mg × num_preparations`
- Optimizes vial count: `vials_needed = ceil(total_dose_mg ÷ vial_strength_mg)`
- Computes reconstitution volumes based on total vial count
- Tracks leftover reconstituted medication to minimize waste

### Example Scenarios

**Scenario 1**: 3 identical 50mL syringes of Paclitaxel 150mg
- Input: `num_preparations = 3`, single preparation requirements
- Output: Total drug volume, optimized vial count, batch instructions

**Scenario 2**: 5 bags of Oxaliplatin for multiple patients with same dosing
- Input: `num_preparations = 5`, standard dose/concentration
- Output: Minimized vial waste, total preparation requirements

---

## PDFs & Printing

- **Worksheet:** A4 portrait with header (hospital + patient fields), body (numbered steps), warnings, footer badge (`Rules <version> • <short>` + timestamp).  
- **Label:** v1 fixed preset (e.g., **100 × 50 mm**) formatted for Datamax; future presets can be added.  
- **Preview:** embed PDF in browser viewer (pdf.js or native).  
- **Export:** saves to `outputs/` with deterministic filenames.

---

## Endpoints

### Core Routes
- `GET /` → Guest home (compute UI).  
- `GET /admin/login` → login form.  
- `POST /admin/login` → validate passphrase → set signed cookie → 303 to `/admin`.  
- `GET /admin` → Admin home (requires fresh session).  
- `POST /admin/logout` → clear session.  

### API Routes  
- `GET /rules/status` → **JSON health check** with integrity status, version, counts, and errors.  
- `POST /compute` → compute numbers & warnings (JSON used by preview).  
- `POST /preview/worksheet` → temp PDF; `POST /export/worksheet` → final PDF.  

### API Models (Pydantic)

#### ComputeInput
**Required fields (all must be explicitly provided):**
```python
patient_id: str          # Patient identifier
patient_name: str        # Patient full name
mrn: str                # Medical record number  
dob: str                # Date of birth (YYYY-MM-DD)
weight_kg: float        # Patient weight in kg
preparation_count: int   # Number of identical preparations (default: 1)
medication_id: str      # Must match medication ID from medications.yaml
dose_mg: float          # Dose amount in mg
total_volume_ml: float  # Total volume for final preparation
container_id: str       # Must match container ID from containers.yaml
solvent_id: str         # Must match solvent ID from solvents.yaml (if needed)
```

**User Selection Requirements:**
- **No auto-selection:** Users must explicitly choose `medication_id`, `container_id`, and `solvent_id`
- **Validation:** All IDs validated against loaded YAML rules with descriptive error messages
- **Type safety:** Pydantic enforces field types and constraints
- **PHI handling:** Patient information included in input but excluded from ComputeResult

#### ComputeResult
**PHI-free response (safe for API responses):**
```python
medication_name: str        # Human-readable medication name
container_name: str         # Human-readable container name
solvent_name: str          # Human-readable solvent name
total_volume_ml: float     # Final preparation volume
concentration_mg_per_ml: float  # Final concentration
steps: List[str]           # Step-by-step instructions
warnings: List[str]        # Safety warnings and alerts
is_powder: bool           # Powder vs solution medication
n_vials: Optional[int]    # Number of vials needed (powder only)
# ... additional calculation fields
```

**PHI Separation:**
- ComputeResult excludes all patient data for API safety
- Patient info handled separately in PDFContext for worksheet generation
- Ensures PHI never appears in JSON API responses or logs  

### Admin Routes (M5)
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

**Prereqs:** Python 3.11, Git. On macOS/Linux adapt activation commands.

```bash
# create & activate venv
python3.11 -m venv downtime
source downtime/bin/activate

# install deps
python -m pip install --upgrade pip
python -m pip install fastapi "uvicorn[standard]" jinja2 python-multipart \
  python-dotenv pyyaml pydantic itsdangerous reportlab

# env
cp .env.example .env
# .env keys:
# ADMIN_PASSPHRASE=...
# SESSION_SECRET=... (long random)
# LOCK_TIMEOUT_MIN=15
# APP_PORT=8765

# run
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

---

## Packaging & Distribution

- **PyInstaller** one-folder exe (Windows): bundles Python runtime + app + fonts; rules live beside exe under `rules/`.  
- **Unsigned pilot** first; later add code-signing if IT requires.  
- **Import Data Pack**: ZIP file → validates → installs → auto-backup old pack.

---

## Editing Workflow (Admin)

1. Enter Admin (passphrase).  
2. Edit meds/containers/solvents/steps via forms or upload YAML.  
3. **Validate**: schema + cross-checks; show **diff** preview.  
4. **Freeze**: write new hashes to manifest, bump version (date), append to `rules/CHANGELOG.txt`.  
5. Editor auto-logout after 15 min idle; `.lock` expires automatically if abandoned.

---

## Backups & Rollback

- Each freeze stores a copy under `rules_backup/<version>/`.  
- One-click rollback to last known good pack.

---

## Security, Privacy, Logging

- **No PHI persisted**: patient fields live in memory/PDF only.  
- **Cookies:** `HttpOnly`, `SameSite=Lax`; localhost in pilot.  
- **Logging:** operational (non-PHI) only—validation errors, admin actions, version used; rotate daily; keep 7 days.  
- **Offline by design:** no third-party calls.

---

## Testing & Excel Parity

- **Golden cases:** 5–10 pharmacy scenarios with expected volumes/warnings.  
- **Parity:** generate from current Excel and compare numerically (± rounding).  
- **Unit tests:** loader (schema + cross-checks), compute edge cases, PDF layout smoke tests.

---

## Milestones & Status

- **M1 – Skeleton & Login** ✅ **COMPLETE**  
  Basic routes, kiosk flow, passphrase auth, signed session, idle timeout.

- **M2 – Rules Loader & Integrity** ✅ **COMPLETE**  
  Pydantic models, cross-checks, SHA-256 badge, startup wiring, JSON status API.  
  **Status**: All rules loading correctly (5 meds, 20 containers, 3 solvents), integrity verification working, badge showing `Rules 2025.09.11 • 07136e`. JSON API at `/rules/status` provides structured health data.

- **M3 – Compute & Steps Hybrid** ✅ **COMPLETE**  
  Volume calculations, concentration validation, step assembly.  
  **Status**: Core compute engine implemented and tested for solution/powder medications with safety warnings. Multiple preparations support added with batch vial optimization for efficient compounding workflows.  
  **Recent Updates**: 
  - ✅ Converted to Pydantic BaseModel for ComputeInput/ComputeResult with strict validation
  - ✅ Implemented explicit user selection - no auto-selection of medications, containers, or solvents
  - ✅ Added PHI separation - patient data excluded from ComputeResult for API safety
  - ✅ Added container_empty type support for generic empty containers/vials
  - ✅ Enhanced field validation with descriptive error messages
  - ✅ OpenAPI documentation generation from Pydantic models

- **M4 – PDFs & Preview** 🔜  
  A4 + label PDFs via ReportLab, embedded preview, footer badge.

- **M5 – Admin Editor** 🔜  
  Forms, Validate & Freeze, diff/changelog, .lock + timeout.

- **M6 – Distribution** 🔜  
  PyInstaller build, Import/Export/Backup/Rollback flows.

- **M7 – Testing & Parity** 🔜  
  Golden cases, parity with Excel, pilot checklist.

**Current Status (as of 2025-09-21):**  
✅ Foundation and API infrastructure complete  
✅ Rules integrity system with JSON health endpoints  
✅ Authentication and session management functional  
✅ Core compute engine working (solution + powder medications)  
✅ Multiple preparations support with batch vial optimization  
✅ Pydantic models with strict validation and PHI separation  
✅ Explicit user selection for all medication/container/solvent choices  
✅ Container type system supporting 5 types including container_empty  
📍 Ready to wire compute engine into web UI and implement PDF generation

---

## Project TODO (living)

### App & UI
- [ ] Guest UI: medication + container + solvent selectors; patient fields (not stored).
- [ ] Multiple preparations input field for batch compounding scenarios.
- [ ] Warning banners for out-of-range concentration / incompatible solvent.
- [ ] PDF preview pane (worksheet/label) with regenerate button.

### Compute Engine
- [ ] Implement syringe usable volume (`capacity * usable_fraction`).
- [ ] Headroom logic: compute `v_withdraw_ml` vs available headspace.
- [ ] Auto-upsize container selection; surface “Changed to X mL bag” note.
- [ ] Powder path: `n_vials`, `reconst_per_vial_ml`, `stock_total_ml`, `stock_leftover_ml`.
- [ ] Round to 0.1 mL; unit-safe arithmetic; block negative/unrealistic results.

### Rules & Integrity
- [x] ~~Pydantic models with field validation~~
- [x] ~~YAML loaders with duplicate detection~~
- [x] ~~Cross-file validation (solvent references, container compatibility)~~
- [x] ~~SHA-256 integrity checking vs rules_manifest.yaml~~
- [x] ~~Rules badge display in UI (`Rules 2025.09.11 • 07136e`)~~
- [x] ~~Startup integrity verification and console logging~~
- [ ] JSON Schema for YAML; friendly errors surfaced in UI.
- [ ] `/editor/validate` + `/editor/freeze` endpoints; write manifest; bump `rules_version`.
- [ ] Rules badge everywhere (page header & PDF footer).

### PDFs
- [ ] ReportLab layouts (shared header/body/footer).
- [ ] A4 worksheet: numbered steps + warnings + signature lines.
- [ ] Label: 100×50 mm (Datamax) layout; later presets.

### Admin
- [ ] .lock creation on edit; auto-expire; show lock owner/time.
- [ ] Import Data Pack (ZIP) → validate → install → backup prior pack.
- [ ] One-click rollback to last good pack.

### Tests
- [ ] Loader unit tests (valid/invalid YAML, cross-checks).
- [ ] Compute golden cases & edge cases.
- [ ] Excel parity harness and report.

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

## Using Copilot in this repo

**Grounding rules**
- Follow the architecture and data-pack schemas defined above.
- Do not invent dependencies; use only the libraries listed in the README.
- Prefer small, testable functions and incremental PR-sized changes.

**Style & safety**
- No PHI persistence. Log operational info only.
- Return clear, actionable errors for YAML issues; do not crash.
- Keep compute functions pure (no hidden global state). Inject inputs explicitly.

**Good prompts to use**
- "Implement `compute_rules_badge(manifest_path, data_paths)` returning `(status, badge_text, version)`; include `sha256_hex(path)` helper and basic tests."
- "Write a step assembler that consumes `sequences.yaml` + `steps_library.yaml` + a `ctx` dict and returns a rendered list; honor each step's `when:` conditions."
- "Add FastAPI `POST /editor/freeze` to recompute hashes in `rules/`, update `rules_manifest.yaml`, bump `rules_version` to `YYYY.MM.DD`, and return a plain-text result (no UI)."
- "Unit tests: containers—reject prefilled where `prefill_ml > capacity_ml`; syringes—`usable_fraction` in (0,1]; messages identical to README."
- "ReportLab: create `render_worksheet_pdf(data, path)` with shared header/footer; stub body; include version badge in the footer."

**Conventions for Copilot**
- Start new functions with a short `SPEC:` comment describing inputs, outputs, and edge cases.
- Use TODO tags for follow-ups, e.g. `# TODO(M3): auto-upsize container`.
- Keep IDs in UPPER_SNAKE; names human-readable.


## Windows packaging with PyInstaller (quick guide)

**Prereqs**
- Windows 10/11, Python 3.11, this repo cloned locally.
- Create a venv and install deps (same as development):

```powershell
python -m venv .venv
. .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
# If no requirements.txt yet:
pip install fastapi "uvicorn[standard]" jinja2 python-multipart python-dotenv pyyaml pydantic itsdangerous reportlab pyinstaller
```

**Entry script (recommended)**
Create a small launcher `run.py` at the repo root to start Uvicorn programmatically:

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

```
