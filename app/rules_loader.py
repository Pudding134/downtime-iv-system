"""
app/rules_loader.py
T2.1 — Rules Loader: parse YAML files into strict models and run cross-checks.

What this file does (M2.T2 scope):
1) Read YAML files from rules/ (solvents, containers, medications, steps_library, sequences).
2) Validate structure with Pydantic (fail early on bad shapes).
3) Build indexed maps by ID for quick lookup.
4) Run cross-checks to catch logical errors (e.g., unknown solvent, bad syringe fraction).
5) Provide a simple init_rules(rules_dir) entry point you’ll call on app startup.

NOT covered here (comes in T3):
- SHA-256 hashing and rules_manifest.yaml comparison (integrity badge).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, get_args

import yaml
from pydantic import BaseModel, Field, ValidationError, ConfigDict, field_validator

# for integrity checking of SHA-256 hashes - detect file tempering
import hashlib 
def sha256_hex(path: Path) -> str:
    """Return SHA-256 hex digest of a file (streamed)."""
    hash = hashlib.sha256()
    with path.open("rb") as f:
        # read file in 8kb chunks
        for chunk in iter(lambda: f.read(8192), b""):
            hash.update(chunk)
    return hash.hexdigest()

# ---------------------------
# Constants / allowed enums
# ---------------------------

ContainerKind = Literal["bag_prefilled", "bag_empty", "bottle_prefilled", "syringe"]
Presentation = Literal["solution", "powder"]

ALLOWED_CONTAINER_KINDS: set[str] = set(get_args(ContainerKind)) # pulling value from ContainerKind and convert into a iterable set

# ---------------------------
# Pydantic models (strict)
# ---------------------------

class Solvent(BaseModel):
    """
    A diluent/solvent. We keep two booleans because WFI is often allowed for
    reconstitution but not for bag dilution.
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    for_reconstitution: bool = Field(default=True)
    for_dilution: bool = Field(default=True)


class Container(BaseModel):
    """
    A container 'shape' the protocol may use.
    - bag_prefilled / bottle_prefilled have a fixed prefill (capacity == prefill).
    - bag_empty has capacity but no prefill.
    - syringe has capacity and a usable fraction (e.g., 0.8).
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: ContainerKind
    capacity_ml: float
    prefill_ml: Optional[float] = None
    solvent: Optional[str] = None
    usable_fraction: Optional[float] = None

    @field_validator("capacity_ml")
    @classmethod
    # @classmethod makes the first parameter the class object (cls) instead of an instance (self).
    # granted we don't have to call it exclusively as it is "self"
    def _cap_positive(cls, capacity_ml: float) -> float:
        if capacity_ml <= 0:
            raise ValueError("capacity_ml must be > 0")
        return capacity_ml

    @field_validator("usable_fraction")
    @classmethod
    def _frac_if_present(cls, fraction_value: Optional[float]) -> Optional[float]:
        if fraction_value is None:
            return fraction_value
        if not (0 < fraction_value <= 1):
            raise ValueError("usable_fraction must be in (0, 1]")
        return fraction_value

    def intrinsic_checks(self) -> List[str]:
        """
        Shape-only checks that don't require other files (no solvent existence yet).
        We collect messages instead of raising to give a friendlier batch report.
        """
        errs: List[str] = []
        if self.kind in {"bag_prefilled", "bottle_prefilled"}:
            if self.prefill_ml is None:
                errs.append("prefill_ml required for prefilled containers")
            elif self.prefill_ml > self.capacity_ml:
                errs.append("prefill_ml must be <= capacity_ml for prefilled containers")
            if not self.solvent:
                errs.append("solvent required for prefilled containers")
        elif self.kind == "bag_empty":
            if self.prefill_ml is not None:
                errs.append("bag_empty must not define prefill_ml")
            if self.solvent is not None:
                errs.append("bag_empty must not define solvent")
        elif self.kind == "syringe":
            if self.usable_fraction is None:
                errs.append("syringe must define usable_fraction (e.g., 0.8)")
            if self.prefill_ml is not None:
                errs.append("syringe must not define prefill_ml")
            if self.solvent is not None:
                errs.append("syringe must not define solvent")
        return errs


class Stock(BaseModel):
    """
    The commercial stock pack, not the final product:
    - Solution: amount+volume → stock concentration known.
    - Powder: amount only → stock volume depends on reconstitution.
    """
    model_config = ConfigDict(extra="forbid")

    amount_mg: float
    volume_ml: Optional[float] = None

    @field_validator("amount_mg")
    @classmethod
    def _amt_positive(cls, volume: float) -> float:
        if volume <= 0:
            raise ValueError("stock.amount_mg must be > 0")
        return volume

    @field_validator("volume_ml")
    @classmethod
    def _vol_positive_if_present(cls, volume: Optional[float]) -> Optional[float]:
        if volume is None:
            return volume
        if volume <= 0:
            raise ValueError("stock.volume_ml must be > 0 when provided")
        return volume


class Reconstitution(BaseModel):
    """
    Reconstitution rules for powder meds.
    - For solution meds, required=False.
    """
    model_config = ConfigDict(extra="forbid")

    required: bool
    diluent: Optional[str] = None
    volume_ml: Optional[float] = None
    note: Optional[str] = None

    @field_validator("volume_ml")
    @classmethod
    def _vol_positive_if_present(cls, volume: Optional[float]) -> Optional[float]:
        if volume is None:
            return volume
        if volume <= 0:
            raise ValueError("reconstitution.volume_ml must be > 0 when provided")
        return volume


class ConcRange(BaseModel):
    """
    Allowed final concentration range (mg/mL).
    Some drugs have a fixed value → min == max.
    """
    model_config = ConfigDict(extra="forbid")

    min: float
    max: float

    @field_validator("min", "max")
    @classmethod
    def _positive(cls, volume: float) -> float:
        if volume <= 0:
            raise ValueError("concentration values must be > 0")
        return volume

    @field_validator("max") # to attach the validation and error to "max" field specifically
    @classmethod
    def _min_le_max(cls, max_conc: float, info) -> float:
        # info.data contains the other fields already validated
        min_conc = info.data.get("min")
        # is not None check is added in case min is missing, though Pydantic should catch that first
        if min_conc is not None and max_conc < min_conc:
            raise ValueError("conc_mg_per_ml.max must be >= min")
        return max_conc


class Stability(BaseModel):
    """
    Storage stability. One of:
    - general_hours: a single BUD for all solvents,
    - by_solvent_hours: dict mapping solvent → hours.
      (You may supply both; by_solvent takes precedence when used.)
    """
    model_config = ConfigDict(extra="forbid")

    general_hours: Optional[int] = None
    by_solvent_hours: Optional[Dict[str, int]] = None

    @field_validator("general_hours")
    @classmethod
    def _gen_pos(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if value <= 0:
            raise ValueError("stability.general_hours must be > 0")
        return value

    @field_validator("by_solvent_hours")
    @classmethod
    def _map_pos(cls, value: Optional[Dict[str, int]]) -> Optional[Dict[str, int]]:
        if value is None:
            return value
        for diluent, hours in value.items():
            if hours <= 0:
                raise ValueError(f"stability.by_solvent_hours[{diluent!r}] must be > 0")
        return value


class Medication(BaseModel):
    """
    Medication rule. Presentation drives reconstitution expectations.
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    presentation: Presentation
    stock: Stock
    reconstitution: Reconstitution
    conc_mg_per_ml: ConcRange
    allowed_solvents: List[str]
    allowed_container_kinds: List[ContainerKind]
    stability: Stability

    @field_validator("allowed_container_kinds")
    @classmethod
    def _kinds_valid(cls, container_kinds_list: List[str]) -> List[str]:
        invalid_container_kinds = [kind for kind in container_kinds_list if kind not in ALLOWED_CONTAINER_KINDS]
        if invalid_container_kinds:
            raise ValueError(f"unknown container kinds: {invalid_container_kinds}")
        return container_kinds_list

    def intrinsic_checks(self) -> List[str]:
        """
        Internal consistency checks that do not need other files.
        """
        errs: List[str] = []

        if self.presentation == "powder":
            if not self.reconstitution.required:
                errs.append("powder meds must have reconstitution.required = true")
            if not self.reconstitution.diluent:
                errs.append("powder meds must set reconstitution.diluent")
            if self.reconstitution.volume_ml is None:
                errs.append("powder meds must set reconstitution.volume_ml")
            if self.stock.volume_ml is not None:
                errs.append("powder meds should not define stock.volume_ml (volume is from reconstitution)")
        else:  # solution
            if self.reconstitution.required:
                errs.append("solution meds must not require reconstitution")
            if self.stock.volume_ml is None:
                errs.append("solution meds must define stock.volume_ml")

        return errs


# ---------------------------
# YAML helpers / loaders
# ---------------------------

def load_yaml(path: Path) -> Any:
    """
    Read a YAML file with safe loader. Returns Python objects (dict/list/...).
    Raises a clear error if file is missing or invalid.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Rules file not found: {path}") from e

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {path}: {e}") from e

    if data is None:
        # Empty file is considered an error for our rules.
        raise ValueError(f"Empty YAML in {path}")
    return data


def _index_by_id(items: List[BaseModel], kind: str) -> Dict[str, BaseModel]:
    """
    Build a dict by 'id'; raise on duplicates for early detection.
    """
    idx: Dict[str, BaseModel] = {}
    for obj in items:
        if obj.id in idx:
            raise ValueError(f"Duplicate {kind} id: {obj.id}")
        idx[obj.id] = obj
    return idx


def load_solvents(path: Path) -> Dict[str, Solvent]:
    """
    Parse solvents.yaml into {id -> Solvent}.
    """
    raw = load_yaml(path)
    if not isinstance(raw, list):
        raise ValueError(f"{path.name} must be a YAML list")
    items: List[Solvent] = []
    for i, row in enumerate(raw):
        try:
            items.append(Solvent.model_validate(row))
        except ValidationError as e:
            raise ValueError(f"solvents[{i}] invalid: {e}") from e
    return _index_by_id(items, "solvent")


def load_containers(path: Path) -> Dict[str, Container]:
    """
    Parse containers.yaml into {id -> Container} and run intrinsic shape checks.
    """
    raw = load_yaml(path)
    if not isinstance(raw, list):
        raise ValueError(f"{path.name} must be a YAML list")
    items: List[Container] = []
    intrinsic_errors: List[str] = []
    for i, row in enumerate(raw):
        try:
            c = Container.model_validate(row)
            msgs = c.intrinsic_checks()
            if msgs:
                intrinsic_errors.extend([f"container {c.id}: {m}" for m in msgs])
            items.append(c)
        except ValidationError as e:
            raise ValueError(f"containers[{i}] invalid: {e}") from e
    if intrinsic_errors:
        # We raise here so the user fixes container shapes before cross-checks.
        raise ValueError("Container shape errors:\n- " + "\n- ".join(intrinsic_errors))
    return _index_by_id(items, "container")


def load_medications(path: Path) -> Dict[str, Medication]:
    """
    Parse medications.yaml into {id -> Medication} and run intrinsic checks.
    """
    raw = load_yaml(path)
    if not isinstance(raw, list):
        raise ValueError(f"{path.name} must be a YAML list")
    items: List[Medication] = []
    all_errs: List[str] = []
    for i, row in enumerate(raw):
        try:
            m = Medication.model_validate(row)
            msgs = m.intrinsic_checks()
            if msgs:
                all_errs.extend([f"med {m.id}: {msg}" for msg in msgs])
            items.append(m)
        except ValidationError as e:
            raise ValueError(f"medications[{i}] invalid: {e}") from e
    if all_errs:
        # Raise with a consolidated message; easier to fix in one pass.
        raise ValueError("Medication intrinsic errors:\n- " + "\n- ".join(all_errs))
    return _index_by_id(items, "medication")


# ---------------------------
# Cross-checks across files
# ---------------------------

def cross_check(
    meds: Dict[str, Medication],
    solvents: Dict[str, Solvent],
    containers: Dict[str, Container],
) -> List[str]:
    """
    Validate relationships *between* files. We return a list of human-friendly
    errors so the UI can display them without crashing.
    """
    errs: List[str] = []

    # 1) Every med.allowed_solvents must exist
    for m in meds.values():
        for s in m.allowed_solvents:
            if s not in solvents:
                errs.append(f"med {m.id}: unknown solvent {s!r}")

    # 2) Every med.allowed_container_kinds must be one of the allowed kinds
    #    (already enforced by the model, but we keep a defensive check here).
    for m in meds.values():
        unk = [k for k in m.allowed_container_kinds if k not in ALLOWED_CONTAINER_KINDS]
        if unk:
            errs.append(f"med {m.id}: unknown container kinds {unk}")

    # 3) Powder meds must reference a valid reconstitution diluent
    for m in meds.values():
        if m.presentation == "powder":
            dil = m.reconstitution.diluent
            if not dil or dil not in solvents:
                errs.append(f"med {m.id}: reconstitution.diluent {dil!r} not in solvents")

    # 4) Prefilled containers must reference an existing solvent
    for c in containers.values():
        if c.kind in {"bag_prefilled", "bottle_prefilled"}:
            if not c.solvent or c.solvent not in solvents:
                errs.append(f"container {c.id}: solvent {c.solvent!r} not in solvents")

    # 5) Syringe capacity rule: usable volume = cap * usable_fraction
    for c in containers.values():
        if c.kind == "syringe":
            if c.usable_fraction is None or not (0 < c.usable_fraction <= 1):
                errs.append(f"container {c.id}: usable_fraction must be in (0,1]")

    return errs


def compute_rules_badge(manifest_path: Path, data_paths: dict[str, Path]) -> tuple[str, str, str, dict[str, str]]:
    """
    Compare actual file hashes vs manifest. Return:
      (status, badge_text, rules_version, actual_hashes)
    status: "ok" or "mismatch"
    badge_text: "Rules {version} • <short>" or "... • MISMATCH"
    rules_version: from manifest (or "unknown" if absent)
    actual_hashes: map of filename -> sha256 (for printing/writing later)
    """
    # Read manifest
    try:
        manifest = load_yaml(manifest_path)
    except Exception:
        # No manifest or bad YAML
        version = "unknown"
        actual = {name: sha256_hex(p) for name, p in data_paths.items() if p.exists()}
        return "mismatch", f"Rules {version} • MISMATCH", version, actual

    version = str(manifest.get("rules_version", "unknown"))
    expected: dict[str, str] = manifest.get("files", {}) or {}

    # Compute actual hashes
    actual: dict[str, str] = {}
    mismatches: list[str] = []
    for name, path in data_paths.items():
        if not path.exists():
            mismatches.append(f"{name}: missing")
            continue
        actual[name] = sha256_hex(path)
        exp = expected.get(name)
        if not exp or exp.lower().strip() != actual[name].lower().strip():
            mismatches.append(f"{name}: mismatch")

    if mismatches:
        return "mismatch", f"Rules {version} • MISMATCH", version, actual

    # All good → short hash from the manifest file itself
    short = sha256_hex(manifest_path)[:6]
    return "ok", f"Rules {version} • {short}", version, actual


# ---------------------------
# In-memory state & initializer
# ---------------------------

@dataclass
class RulesState:
    """
    The in-memory state the app will use.
    - counts: quick visibility in logs
    - maps: fast lookups by id
    - errors: cross-check output (empty means OK)
    - version/badge are filled in T3 (manifest/hash)
    """
    meds: Dict[str, Medication]
    containers: Dict[str, Container]
    solvents: Dict[str, Solvent]
    counts: Tuple[int, int, int]
    errors: List[str]
    version: Optional[str] = None          # T3 fills from rules_manifest.yaml
    badge_text: str = "Rules — not loaded" # T3 sets real badge


def init_rules(rules_dir: Path) -> RulesState:
    """
    Load all rules files from rules_dir, run cross-checks, and return a RulesState.
    This function raises on hard parse/shape errors, but *does not* raise on
    cross-check issues; those are returned in state.errors for the UI to display.
    """
    solvents_path = rules_dir / "solvents.yaml"
    containers_path = rules_dir / "containers.yaml"
    meds_path = rules_dir / "medications.yaml"
    # steps_library.yaml and sequences.yaml are parsed in M3 when we assemble steps

    solvents = load_solvents(solvents_path)
    containers = load_containers(containers_path)
    meds = load_medications(meds_path)

    errs = cross_check(meds, solvents, containers)

    state = RulesState(
        meds=meds,
        containers=containers,
        solvents=solvents,
        counts=(len(meds), len(containers), len(solvents)),
        errors=errs,
        # version/badge will be populated in T3
    )

        # --- Integrity badge (T3) ---
    manifest_path = rules_dir / "rules_manifest.yaml"
    data_paths = {
        "solvents.yaml": rules_dir / "solvents.yaml",
        "containers.yaml": rules_dir / "containers.yaml",
        "medications.yaml": rules_dir / "medications.yaml",
        "steps_library.yaml": rules_dir / "steps_library.yaml",
        "sequences.yaml": rules_dir / "sequences.yaml",
    }
    status, badge, version, actual = compute_rules_badge(manifest_path, data_paths)
    state.version = version
    state.badge_text = badge
    # Optional: print actual hashes to help you build/refresh the manifest
    if status != "ok":
        print("[rules] Integrity: MISMATCH")
        for k, v in actual.items():
            print(f"  {k}: sha256:{v}")
    else:
        print(f"[rules] Integrity: OK • {badge}")

    # Minimal console output so you see what's loaded during dev
    print(f"[rules] Loaded: {state.counts[0]} meds, {state.counts[1]} containers, {state.counts[2]} solvents")
    if state.errors:
        print("[rules] Cross-checks: FAIL")
        for e in state.errors:
            print(f"  - {e}")
    else:
        print("[rules] Cross-checks: OK")

    return state


# ---------------------------
# CLI helper (optional for quick dev)
# ---------------------------

if __name__ == "__main__":
    # Allow: python -m app.rules_loader  (run from repo root)
    base = Path(__file__).resolve().parent.parent / "rules"
    rs = init_rules(base)
    # Exit code 0/1 based on cross-checks could be added for CI later.