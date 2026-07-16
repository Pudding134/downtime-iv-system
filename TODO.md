# Project TODO (living)

> The human-facing project overview lives in [readme.md](readme.md).
> This file tracks *what's next* and the detailed spec for the milestone in flight.
> Update checkboxes as work lands; move finished specs into the readme if they become permanent design docs.

**Where I am now (2026-07-15):** M3 (Compute Engine), reworking **M3.T2 core math** around the
manual signed-adjustment design, then moving into **M3.T3 steps assembly**.

---

## Current work: M3.T2 — Core math with manual signed adjustment

Design recap: the pharmacist no longer enters a target concentration. Instead they enter a
signed `container_adjustment_vol_ml` — negative = withdraw headroom (prefilled only),
positive = add diluent. Empty containers/syringes start at 0 mL, so withdrawing is rejected.

Per-preparation math flow:

- [x] `stock_conc_mg_per_ml` (solution + powder, with `conc_after_recon_mg_per_ml` override)
- [x] `drug_volume_ml = dose_mg / stock_conc`
- [x] `container_start_volume_ml` (prefill for prefilled containers, else 0)
- [x] Signed adjustment applied; negative adjustment rejected for empty containers/syringes
- [x] `final_product_vol_ml = start + adjustment + drug_volume` (must be > 0, hard stop)
- [x] `final_product_conc_mg_per_ml = dose_mg / final_product_vol_ml`
- [x] Capacity checks — bag/bottle capacity and syringe usable volume — as hard stops (422)
- [ ] Concentration range check vs `conc_limit_mg_per_ml` → **warning**, not error
- [ ] Powder vial math: vials needed (ceil on mg per vial), recon volume per vial,
      recon concentration, pooled volume, leftover volume
- [ ] Totals: scale dose, drug volume, vials, etc. by `num_preparations`
- [ ] Round volumes to 0.1 mL (or configurable rounding); block negative/unrealistic results
- [x] ~~Fix `ComputeOutput.container_start_vol` field declaration (chained `=` instead of `:` annotation);
      renamed to `container_start_vol_ml` per unit-suffix convention~~

Keep `stock_conc_mg_per_ml` exposed in `ComputeOutput` (diagnostic; useful for label/worksheet).

## Next: M3.T3 — Steps assembly

- [ ] Load steps library + sequences from YAML into the compute path
- [ ] Choose base sequence by `med.prep_profile`
- [ ] Apply med-specific insertions
- [ ] Evaluate `when:` gates via Jinja2 context
- [ ] Render templates with `StrictUndefined`; render failures go to the `errors` list, not HTTP 500
- [ ] Return `ComputeOutput.steps: List[str]`

Template context variables:
`v_drug_ml`, `v_adjust_ml` (signed) plus derived `v_withdraw_ml` / `v_add_ml`,
`final_volume_ml`, `final_conc_mg_per_ml`, and powder variables
(`n_vials`, `reconst_per_vial_ml`, leftover, etc.).

## Testing direction

Prefer **pytest** over ad-hoc curl scripts (curl stays for quick manual smoke tests).
Cover positive + negative cases for:

- [ ] Each presentation type (solution / powder)
- [ ] Each container kind (prefilled bag/bottle, empty bag, `container_empty`, syringe)
- [ ] Solvent policy errors (missing, not allowed, provided for prefilled)
- [ ] Capacity overflow (bag/bottle capacity, syringe usable fraction)
- [ ] Invalid adjustment (withdraw from empty, final volume ≤ 0)
- [ ] Step rendering: `when:` gates and missing-variable handling

---

## Backlog by area

### App & UI
- [ ] Guest UI: medication + container + solvent selectors; patient fields (not stored)
- [ ] Multiple preparations input field for batch compounding scenarios
- [ ] Warning banners for out-of-range concentration / incompatible solvent
- [ ] PDF preview pane (worksheet/label) with regenerate button

### Compute Engine
- [x] ~~Unit support for medications (mg/mcg)~~
- [x] ~~Special reconstitution concentration support~~
- [x] ~~Syringe usable volume (`capacity * usable_fraction`)~~
- [x] ~~Container capacity checks as hard stops (422)~~
- [x] ~~Signed container adjustment with empty-container guard~~
- [ ] Concentration range validation + warnings
- [ ] Solvent compatibility warnings (policy-driven)
- [ ] Headroom logic: compute `v_withdraw_ml` vs available headspace
- [ ] Auto-upsize container selection; surface "Changed to X mL bag" note
- [ ] Powder path: `n_vials`, `reconst_per_vial_ml`, `stock_total_ml`, `stock_leftover_ml`
- [ ] Multiple preparations scaling (`num_preparations`)
- [ ] Round to 0.1 mL; unit-safe arithmetic
- [ ] Step assembly from `steps_library.yaml` + `sequences.yaml`

### Rules & Integrity
- [x] ~~Pydantic models, YAML loaders, cross-file validation~~
- [x] ~~SHA-256 integrity checking + badge + startup verification~~
- [x] ~~`GET /rules/status` JSON health endpoint~~
- [ ] JSON Schema for YAML; friendly errors surfaced in UI
- [ ] `/editor/validate` + `/editor/freeze` endpoints; write manifest; bump `rules_version`
- [ ] Rules badge everywhere (page header & PDF footer)

### PDFs
- [ ] ReportLab layouts (shared header/body/footer)
- [ ] A4 worksheet: numbered steps + warnings + signature lines
- [ ] Label: 100×50 mm (Datamax) layout; later presets

### Admin
- [ ] `.lock` creation on edit; auto-expire; show lock owner/time
- [ ] Import Data Pack (ZIP) → validate → install → backup prior pack
- [ ] One-click rollback to last good pack

### Tests
- [ ] Loader unit tests (valid/invalid YAML, cross-checks)
- [ ] Compute golden cases & edge cases (pytest)
- [ ] Excel parity harness and report
