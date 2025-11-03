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
        # read file in 8kb chunks (8192 bytes)
        for chunk in iter(lambda: f.read(8192), b""):
            hash.update(chunk)
    return hash.hexdigest()

# ---------------------------
# Part 2: Constants / allowed enums type definitions
# ---------------------------

ContainerKind = Literal["bag_prefilled", "bag_empty", "bottle_prefilled", "syringe", "container_empty"]
Presentation = Literal["solution", "powder"]

ALLOWED_CONTAINER_KINDS: set[str] = set(get_args(ContainerKind)) # pulling value from ContainerKind and convert into a iterable set for later usage

# ---------------------------
# Part 3: Pydantic Models Classes (strict)
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
    Container rule: bags, bottles, syringes, and generic empty containers with their properties.
    """
    model_config = ConfigDict(extra="forbid")
    
    id: str
    name: str  # NEW: Human-readable name
    kind: ContainerKind
    capacity_ml: float
    usable_fraction: float = 1.0  # For syringes, typically 0.8
    prefill_ml: Optional[float] = None
    solvent: Optional[str] = None  # For prefilled containers

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
        
        elif self.kind in {"bag_empty", "container_empty"}:
            if self.prefill_ml is not None:
                errs.append(f"{self.kind} must not define prefill_ml")
            if self.solvent is not None:
                errs.append(f"{self.kind} must not define solvent")
        
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
    - Solution: strength + volume → stock concentration known.
    - Powder: strength only → stock volume depends on reconstitution.
    """
    model_config = ConfigDict(extra="forbid")

    strength: float
    unit: Literal["mg", "mcg"]
    volume_ml: Optional[float] = None

    @field_validator("strength")
    @classmethod
    def _amt_positive(cls, volume: float) -> float:
        if volume <= 0:
            raise ValueError("stock.strength must be > 0")
        return volume

    @field_validator("volume_ml")
    @classmethod
    def _vol_positive_if_present(cls, volume: Optional[float]) -> Optional[float]:
        if volume is None:
            return volume
        if volume <= 0:
            raise ValueError("stock.volume_ml must be > 0 when provided")
        return volume
    
    @field_validator("unit", before=True)
    @classmethod
    def _unit_must_be_valid(cls, unit: str) -> str:
        unit_str = unit.strip().lower()
        if not unit_str:
            raise ValueError("stock.unit must be provided")
        if unit_str not in {"mg", "mcg"}:
            raise ValueError("stock.unit must be one of: 'mg', 'mcg'")
        return unit_str
    
    def strength_mg(self) -> float:
        """
        Return strength expressed in milligrams, regardless of original unit.
        - mg → mg (unchanged)
        - mcg → divide by 1000
        """
        if self.unit == "mg":
            return self.strength
        else:  # mcg
            return self.strength / 1000.0


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
    conc_after_recon_mg_per_ml: Optional[float] = None

    @field_validator("volume_ml")
    @classmethod
    def _vol_positive_if_present(cls, volume: Optional[float]) -> Optional[float]:
        if volume is None:
            return volume
        if volume <= 0:
            raise ValueError("reconstitution.volume_ml must be > 0 when provided")
        return volume

    @field_validator("conc_after_recon_mg_per_ml")
    @classmethod
    def _conc_positive_if_present(cls, conc: Optional[float]) -> Optional[float]:
        if conc is None:
            return conc
        if conc <= 0:
            raise ValueError("reconstitution.conc_after_recon_mg_per_ml must be > 0 when provided")
        return conc


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
    conc_limit_mg_per_ml: ConcRange
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
            if (self.reconstitution.diluent is not None) or (self.reconstitution.volume_ml is not None):
                errs.append("solution meds must not define reconstitution.diluent/volume_ml")
        return errs


# ---------------------------
# Part 4: YAML helpers / loaders
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


def _index_by_id(items_list: List[BaseModel], kind: str) -> Dict[str, BaseModel]:
    """
    Internal Function.
    Build a dict by 'id'; raise on duplicates for early detection.
    """
    index: Dict[str, BaseModel] = {}
    for object in items_list:
        # check for duplicate IDs
        if object.id in index:
            raise ValueError(f"Duplicate {kind} id: {object.id}")
        # add non-duplicate into list sorted by id as key (O(1) constant time search/access speed)
        # dict[key] = value
        index[object.id] = object
    return index


def load_solvents(path: Path) -> Dict[str, Solvent]:
    """
    Parse solvents.yaml into {id -> Solvent}.
    """
    raw_data = load_yaml(path)
    # Validate it's a list "instance"
    if not isinstance(raw_data, list):
        raise ValueError(f"{path.name} must be a YAML list")
    
    # Convert each YAML item to Pydantic object
    items_list: List[Solvent] = []
    for index, data_row in enumerate(raw_data):
        try:
            # create a solvent object with yaml dictionary (row)
            items_list.append(Solvent.model_validate(data_row))
        except ValidationError as e:
            raise ValueError(f"solvents[{index}] invalid: {e}") from e
    return _index_by_id(items_list, "solvent")


def load_containers(path: Path) -> Dict[str, Container]:
    """
    Parse containers.yaml into {id -> Container} and run intrinsic shape checks.
    """
    raw_data = load_yaml(path)
    if not isinstance(raw_data, list):
        raise ValueError(f"{path.name} must be a YAML list")
    items_list: List[Container] = []
    intrinsic_errors_list: List[str] = [] # List variable to collect all errors
    for index, row in enumerate(raw_data):
        try:
            container = Container.model_validate(row)
            msgs = container.intrinsic_checks() # Call the business logic validation from Container class
            if msgs:
                intrinsic_errors_list.extend([f"container {container.id}: {m}" for m in msgs])
            items_list.append(container)
        except ValidationError as e:
            raise ValueError(f"containers[{index}] invalid: {e}") from e
    if intrinsic_errors_list:
        # We raise here so the user fixes container shapes before cross-checks.
        raise ValueError("Container shape errors:\n- " + "\n- ".join(intrinsic_errors_list))
    return _index_by_id(items_list, "container")


def load_medications(path: Path) -> Dict[str, Medication]:
    """
    Parse medications.yaml into {id -> Medication} and run intrinsic checks.
    """
    raw_data = load_yaml(path)
    if not isinstance(raw_data, list):
        raise ValueError(f"{path.name} must be a YAML list")
    items_list: List[Medication] = []
    errors_list: List[str] = []
    for index, row in enumerate(raw_data):
        try:
            med = Medication.model_validate(row)
            msgs = med.intrinsic_checks() # Complex business logic check
            if msgs:
                errors_list.extend([f"med {med.id}: {msg}" for msg in msgs])
            items_list.append(med)
        except ValidationError as e:
            raise ValueError(f"medications[{index}] invalid: {e}") from e
    if errors_list:
        # Raise with a consolidated message; easier to fix in one pass.
        raise ValueError("Medication intrinsic errors:\n- " + "\n- ".join(errors_list))
    return _index_by_id(items_list, "medication")


# ---------------------------
# Part 5: Cross-File Validation
# ---------------------------

def cross_check(
    meds_list: Dict[str, Medication],
    solvents_list: Dict[str, Solvent],
    containers_list: Dict[str, Container],
) -> List[str]:
    """
    Validate relationships *between* different YAML files are correct. 
    We return a list of human-friendly errors so the UI can display them without crashing.
    """
    error_list: List[str] = []

    # 1) Check every med.allowed_solvents must exist
    for med in meds_list.values():
        for solvent in med.allowed_solvents:
            if solvent not in solvents_list:
                error_list.append(f"med {med.id}: unknown solvent {solvent!r}")

    # 2) Check every med.allowed_container_kinds must be one of the allowed kinds
    #    (already enforced by the model, but we keep a defensive check here).
    for med in meds_list.values():
        # add to variable any meds with unmatched container kind
        unknown_kind = [kind for kind in med.allowed_container_kinds if kind not in ALLOWED_CONTAINER_KINDS]
        
        # add error message to error list if any unmatched/unknown container kind found
        if unknown_kind:
            error_list.append(f"med {med.id}: unknown container kinds {unknown_kind}")

    # 3) Check that powder meds must reference a valid reconstitution diluent
    for med in meds_list.values():
        if med.presentation == "powder":
            diluent = med.reconstitution.diluent
            # append to error list if there is no diluent assigned or if it is not an approved diluent
            if not diluent or diluent not in solvents_list:
                error_list.append(f"med {med.id}: reconstitution.diluent {diluent!r} not in solvents")

    # 4) Check that prefilled containers must reference an existing solvent
    for container in containers_list.values():
        if container.kind in {"bag_prefilled", "bottle_prefilled"}:
            if not container.solvent or container.solvent not in solvents_list:
                error_list.append(f"container {container.id}: solvent {container.solvent!r} not in solvents")

    # 5) Syringe capacity rule: usable volume = cap * usable_fraction
    for container in containers_list.values():
        if container.kind == "syringe":
            if container.usable_fraction is None or not (0 < container.usable_fraction <= 1):
                error_list.append(f"container {container.id}: usable_fraction must be in (0,1]")

    return error_list


# ---------------------------
# Part 6: Integrity System
# ---------------------------
def compute_rules_badge(manifest_path: Path, data_paths: dict[str, Path]) -> tuple[str, str, str, dict[str, str]]:
    """
    Compare actual_manifest file hashes vs rules_manifest. 
    Return: (status, badge_text, rules_version, actual_manifest_hashes)
    - status: "ok" | "mismatch" | "missing"
    - badge_text: "Rules {rules_version} • <short>" or "... • MISMATCH"
    - rules_version: from manifest (or "unknown" if absent)
    - actual_manifest_hashes: map of filename -> sha256 (for printing/writing later)
    """
    # Read manifest
    try:
        manifest = load_yaml(manifest_path)
    except Exception:
        # No manifest or bad YAML
        rules_version = "unknown"
        actual_manifest = {name: sha256_hex(p) for name, p in data_paths.items() if p.exists()}
        return "missing", "Rules - manifest missing" , rules_version, actual_manifest

    rules_version = str(manifest.get("rules_version", "unknown"))
    expected: dict[str, str] = manifest.get("files", {}) or {}

    # Compute actual_manifest hashes
    actual_manifest: dict[str, str] = {}
    mismatches: list[str] = []
    for name, path in data_paths.items():
        # check if file exists
        if not path.exists():
            mismatches.append(f"{name}: missing")
            continue

        # If file exist, calculate SHA-256 hash of file content
        actual_manifest[name] = sha256_hex(path)
        expected_manifest = expected.get(name)

        # Compare hashes (normalize by stripping "sha256:" prefix if present)
        actual_hex = actual_manifest[name].lower().strip()
        expected_hex = expected_manifest.replace("sha256:", "").lower().strip() if expected_manifest else ""
        
        if not expected_manifest or expected_hex != actual_hex:
            mismatches.append(f"{name}: mismatch")

    if mismatches:
        return "mismatch", f"Rules {rules_version} • MISMATCH", rules_version, actual_manifest

    # All good → short hash from the manifest file itself
    short = sha256_hex(manifest_path)[:6]
    return "ok", f"Rules {rules_version} • {short}", rules_version, actual_manifest


# ---------------------------
# Part 7: In-memory state object
# ---------------------------

# Dataclass use for automatic init and string representation for debugging
@dataclass
class RulesState:
    """
    The in-memory state the app will use.
    - counts: quick visibility in logs
    - maps: fast lookups by id
    - errors: cross-check output (empty means OK)
    - integrity: fast single source of truth whether rules are ready
    - rules_version/badge are filled in T3 (manifest/hash)
    """
    meds: Dict[str, Medication]
    containers: Dict[str, Container]
    solvents: Dict[str, Solvent]
    counts: Tuple[int, int, int]
    errors: List[str]
    integrity: Literal["ok","mismatch","missing"] = "missing" # added for eash quick glance as to whether rules are ready
    rules_version: Optional[str] = None          # T3 fills from rules_manifest.yaml
    badge_text: str = "Rules — not loaded" # T3 sets real badge



# ---------------------------
# Part 8: The Main Initializer
# ---------------------------
def init_rules(rules_dir: Path) -> RulesState:
    """
    - Load all rules files from rules_dir
    - Run cross-checks
    - Return a RulesState.
    \nThis function raises on hard parse/shape errors, but *does not* raise on
    cross-check issues; those are returned in state.errors for the UI to display.
    """

    # Set/define all the yaml file paths
    solvents_path = rules_dir / "solvents.yaml"
    containers_path = rules_dir / "containers.yaml"
    meds_path = rules_dir / "medications.yaml"
    # steps_library.yaml and sequences.yaml are parsed in M3 when we assemble steps

    # Load each YAML file via its respective load functions, returns Dict[str, Class]
    solvents = load_solvents(solvents_path)
    containers = load_containers(containers_path)
    meds = load_medications(meds_path)

    # Run cross-validation, storing errors list in errs variable
    errs = cross_check(meds, solvents, containers)

    # Create the main State object, assigning all the different class and errors list as part of it
    state = RulesState(
        meds=meds,
        containers=containers,
        solvents=solvents,
        counts=(len(meds), len(containers), len(solvents)),
        errors=errs,
        # rules_version/badge will be populated in T3
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

    # Check integrity, comparing the current file hashes vs rules_manifest recorded
    status, badge, rules_version, actual_manifest = compute_rules_badge(manifest_path, data_paths)
    
    # updating the main state object properties with what's retrieved from rules_manifest
    state.rules_version = rules_version 
    state.badge_text = badge
    state.integrity = status
    # Optional: print actual_manifest hashes to help you build/refresh the manifest
    if status != "ok":
        print(f"[rules] Integrity: {status.upper()}, version = {rules_version}") # print MISMATCH or MISSING
        for k, v in actual_manifest.items():
            print(f"  {k}: sha256:{v}")
    else:
        print(f"[rules] Integrity: OK, version = {rules_version} • {badge}")

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