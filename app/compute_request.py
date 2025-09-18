"""
app/compute.py
M3 — Compute Engine: volume calculations, concentration validation, step assembly.

What this file does:
1) Take user inputs (medication, container, dose, patient info)
2) Calculate volumes, concentrations, and safety checks
3) Generate step-by-step instructions from rules
4) Return everything needed for PDF generation

Key functions:
- compute_protocol(): main entry point
- calculate_volumes(): core math for solution/powder meds
- validate_safety(): concentration and compatibility checks
- assemble_steps(): generate instructions from steps_library + sequences
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Dict, Any
from pathlib import Path
import math

from .rules_loader import RulesState, Medication, Container, Solvent


# ---------------------------
# Part 1: Input/Output Models
# ---------------------------

class ComputeInput(BaseModel):
    """
    User's selections for a compounding request.
    Patient fields are for PDF only (not persisted).
    """
    medication_id: str
    container_id: Optional[str] = None  # System can auto-select if not given
    solvent: Optional[str] = None  # Depends on container/medication
    dose_mg: float = Field(gt=0, description="Dose in milligrams")
    num_preparations: int = Field(1, ge=1, le=100, description="Number of identical preparations")
    final_volume_ml: Optional[float] = Field(None, gt=0, description="Target volume (uses container capacity if not specified)")
    patient_name: Optional[str] = None
    patient_hrn: Optional[str] = None
    target_conc_mg_per_ml: Optional[float] = Field(None, gt=0, description="Target concentration if specified")

    class Config:
        json_schema_extra = {
            "example": {
                "medication_id": "PACLITAXEL",
                "dose_mg": 150.0,
                "num_preparations": 3,
                "patient_name": "John Doe",
                "patient_hrn": "MRN12345"
            }
        }

    @model_validator(mode='after')
    def validate_requirements(self):
        """Custom validation for business rules"""
        # Add custom validation logic here later if needed
        # e.g., powder medications might require certain fields
        return self


class ComputeResult(BaseModel):
    """
    Everything needed to generate PDFs and display results.
    Uses individual field echo instead of full input object for better security.
    """
    # Individual input echo (NO PHI) - Safe for logging/caching
    medication_id: str
    dose_mg: float
    num_preparations: int
    container_id: Optional[str] = None
    final_volume_ml: Optional[float] = None
    target_conc_mg_per_ml: Optional[float] = None
    
    # Resolved human-readable names for UI display
    medication_name: str = ""
    container_name: str = ""  # CHANGED: Use simple container name from rules
    solvent_name: str = ""
    
    # Resolved objects from rules (for internal processing)
    medication: Optional[Medication] = None
    container: Optional[Container] = None
    final_solvent: Optional[Solvent] = None
    
    # Core computed values
    final_concentration_mg_per_ml: float = 0.0
    drug_volume_ml: float = 0.0           # mL of drug solution to add
    withdraw_volume_ml: float = 0.0       # mL to withdraw for headroom (if any)
    
    # Safety and validation
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    
    # For powder medications
    n_vials: int = 0
    reconst_per_vial_ml: float = 0.0
    stock_conc_mg_per_ml: float = 0.0  # after reconstitution
    stock_total_ml: float = 0.0        # total reconstituted volume
    stock_leftover_ml: float = 0.0     # unused reconstituted volume
    
    # Multiple preparations scaling
    total_drug_volume_ml: float = 0.0        # drug_volume_ml × num_preparations
    total_vials_needed: int = 0              # Total vials across all preparations
    total_dose_mg: float = 0.0               # dose_mg × num_preparations
    
    # Safety flags
    concentration_in_range: bool = True
    solvent_compatible: bool = True
    
    # Container adjustment (if auto-upsized)
    container_changed: bool = False
    original_container_id: Optional[str] = None

    class Config:
        # Allow arbitrary types for Medication, Container, Solvent objects
        arbitrary_types_allowed = True
        json_schema_extra = {
            "example": {
                "medication_id": "PACLITAXEL",
                "medication_name": "Paclitaxel Injection",
                "dose_mg": 150.0,
                "num_preparations": 3,
                "container_id": "syringe_50ml",
                "container_description": "50 mL Syringe",
                "final_concentration_mg_per_ml": 3.0,
                "drug_volume_ml": 25.0,
                "total_drug_volume_ml": 75.0,
                "concentration_in_range": True
            },
            "description": "IV compounding calculation result with safety validations",
            "sensitive": False  # No PHI in this model
        }


# NEW: Separate model for PDF generation that includes PHI
class PDFContext(BaseModel):
    """
    Patient information for PDF generation only.
    Kept separate to minimize PHI exposure in API responses.
    """
    patient_name: Optional[str] = None
    patient_hrn: Optional[str] = None
    generated_at: Optional[str] = None  # ISO timestamp
    pharmacist_id: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "sensitive": True,  # Mark as containing PHI
            "description": "Patient information for PDF generation - contains PHI"
        }


class WorksheetData(BaseModel):
    """
    Complete data package for PDF worksheet generation.
    Combines calculation results with patient information.
    """
    compute_result: ComputeResult
    pdf_context: PDFContext
    
    class Config:
        json_schema_extra = {
            "sensitive": True,  # Contains PHI via pdf_context
            "description": "Complete worksheet data including patient information"
        }
# ---------------------------
# Part 2: Core Math Functions
# ---------------------------

def round_to_decimal(value: float, places: int = 1) -> float:
    """
    Round to specified decimal places (default 0.1 mL).
    Uses proper rounding (not truncation).
    """
    multiplier = 10 ** places
    return round(value * multiplier) / multiplier


def calculate_solution_volumes(
    med: Medication,
    container: Container,
    dose_mg: float,
    target_volume_ml: float,
    num_preparations: int = 1
) -> tuple[float, float, float, int, List[str]]:
    """
    SPEC: Calculate volumes for solution medications.
    
    Args:
        med: Medication object (must be solution type)
        container: Target container
        dose_mg: Required dose in mg
        target_volume_ml: Desired final volume
        num_preparations: Number of preparations to make
    
    Returns:
        (drug_volume_ml, withdraw_volume_ml, final_conc_mg_per_ml, n_vials, warnings)
    
    Logic:
        1. Stock concentration = med.stock.amount_mg / med.stock.volume_ml
        2. Drug volume needed = dose_mg / stock_concentration
        3. Final concentration = dose_mg / target_volume_ml
        4. Withdraw volume = max(0, drug_volume - available_headroom)
        5. Calculate vials needed for all preparations
    """
    warnings = []
    
    # Stock concentration (mg/mL)
    stock_conc = med.stock.amount_mg / med.stock.volume_ml
    
    # Volume of drug solution needed per preparation (mL)
    drug_volume_ml = dose_mg / stock_conc
    drug_volume_ml = round_to_decimal(drug_volume_ml)
    
    # Total drug volume needed for all preparations
    total_drug_volume_ml = drug_volume_ml * num_preparations
    
    # Calculate number of vials needed
    # Each vial provides med.stock.volume_ml of solution
    n_vials = math.ceil(total_drug_volume_ml / med.stock.volume_ml)
    
    # Final concentration after dilution
    final_conc_mg_per_ml = dose_mg / target_volume_ml
    
    # Calculate headroom needed
    if container.kind in {"bag_prefilled", "bottle_prefilled"}:
        # Prefilled containers: available headroom = capacity - prefill
        available_headroom = container.capacity_ml - container.prefill_ml
        withdraw_volume_ml = max(0, drug_volume_ml - available_headroom)
    elif container.kind == "bag_empty":
        # Empty bag: no withdrawal needed (we add solvent as needed)
        withdraw_volume_ml = 0.0
    elif container.kind == "syringe":
        # Syringe: check against usable capacity
        usable_capacity = container.capacity_ml * container.usable_fraction
        if drug_volume_ml > usable_capacity:
            warnings.append(f"Drug volume ({drug_volume_ml} mL) exceeds syringe usable capacity ({usable_capacity} mL)")
        withdraw_volume_ml = 0.0
    else:
        warnings.append(f"Unknown container kind: {container.kind}")
        withdraw_volume_ml = 0.0
    
    withdraw_volume_ml = round_to_decimal(withdraw_volume_ml)
    
    # Validation
    if drug_volume_ml <= 0:
        warnings.append("Drug volume is zero or negative")
    if final_conc_mg_per_ml <= 0:
        warnings.append("Final concentration is zero or negative")
    if n_vials <= 0:
        warnings.append("Number of vials is zero or negative")
    
    return drug_volume_ml, withdraw_volume_ml, final_conc_mg_per_ml, n_vials, warnings


def calculate_powder_volumes(
    med: Medication,
    container: Container,
    dose_mg: float,
    target_volume_ml: float,
    num_preparations: int = 1
) -> tuple[int, float, float, float, float, float, float, List[str]]:
    """
    SPEC: Calculate volumes for powder medications requiring reconstitution.
    
    Returns:
        (n_vials, reconst_per_vial_ml, stock_conc_mg_per_ml, stock_total_ml, 
         drug_volume_ml, withdraw_volume_ml, final_conc_mg_per_ml, warnings)
    """
    warnings = []
    
    # Calculate total dose needed for all preparations
    total_dose_mg = dose_mg * num_preparations
    
    # How many vials do we need for all preparations?
    n_vials = math.ceil(total_dose_mg / med.stock.amount_mg)
    
    # Reconstitution volume per vial
    reconst_per_vial_ml = med.reconstitution.volume_ml
    
    # Stock concentration after reconstitution (mg/mL)
    stock_conc_mg_per_ml = med.stock.amount_mg / reconst_per_vial_ml
    
    # Total reconstituted volume
    stock_total_ml = n_vials * reconst_per_vial_ml
    
    # Volume of reconstituted drug needed for all preparations
    total_drug_volume_ml = total_dose_mg / stock_conc_mg_per_ml
    total_drug_volume_ml = round_to_decimal(total_drug_volume_ml)
    
    # Volume needed per single preparation
    drug_volume_ml = dose_mg / stock_conc_mg_per_ml
    drug_volume_ml = round_to_decimal(drug_volume_ml)
    
    # Leftover reconstituted volume
    stock_leftover_ml = stock_total_ml - total_drug_volume_ml
    
    # Final concentration
    final_conc_mg_per_ml = dose_mg / target_volume_ml
    
    # Calculate withdrawal (same logic as solutions)
    if container.kind in {"bag_prefilled", "bottle_prefilled"}:
        available_headroom = container.capacity_ml - container.prefill_ml
        withdraw_volume_ml = max(0, drug_volume_ml - available_headroom)
    else:
        withdraw_volume_ml = 0.0
    
    withdraw_volume_ml = round_to_decimal(withdraw_volume_ml)
    
    # Validation
    if n_vials <= 0:
        warnings.append("Number of vials is zero or negative")
    if stock_leftover_ml > 0:
        warnings.append(f"Leftover reconstituted volume: {round_to_decimal(stock_leftover_ml)} mL will be discarded")
    
    return (n_vials, reconst_per_vial_ml, stock_conc_mg_per_ml, stock_total_ml,
            drug_volume_ml, withdraw_volume_ml, final_conc_mg_per_ml, warnings)


# ---------------------------
# Part 3: Safety Validation
# ---------------------------

def validate_concentration(
    final_conc: float,
    med: Medication
) -> tuple[bool, List[str]]:
    """
    SPEC: Check if final concentration is within allowed range.
    """
    warnings = []
    in_range = True
    
    min_conc = med.conc_mg_per_ml.min
    max_conc = med.conc_mg_per_ml.max
    
    if final_conc < min_conc:
        in_range = False
        warnings.append(f"⚠️ Concentration {final_conc:.2f} mg/mL below minimum {min_conc} mg/mL")
    elif final_conc > max_conc:
        in_range = False
        warnings.append(f"⚠️ Concentration {final_conc:.2f} mg/mL exceeds maximum {max_conc} mg/mL")
    
    return in_range, warnings


def validate_solvent_compatibility(
    med: Medication,
    solvent: Solvent,
    use_case: str  # "dilution" or "reconstitution"
) -> tuple[bool, List[str]]:
    """
    SPEC: Check medication-solvent compatibility.
    """
    warnings = []
    compatible = True
    
    # Check if solvent is in medication's allowed list
    if solvent.id not in med.allowed_solvents:
        compatible = False
        warnings.append(f"⚠️ {solvent.name} not approved for {med.name}")
        return compatible, warnings
    
    # Check solvent's usage flags
    if use_case == "reconstitution" and not solvent.for_reconstitution:
        compatible = False
        warnings.append(f"⚠️ {solvent.name} not approved for reconstitution")
    elif use_case == "dilution" and not solvent.for_dilution:
        compatible = False
        warnings.append(f"⚠️ {solvent.name} not approved for dilution")
    
    return compatible, warnings


# ---------------------------
# Part 4: Auto-selection Logic (NEW)
# ---------------------------

def auto_select_container(
    med: Medication,
    dose_mg: float,
    rules_state: RulesState,
    preferred_kind: Optional[str] = None
) -> Optional[Container]:
    """
    Auto-select appropriate container when not specified by user.
    
    Logic:
    1. Filter containers by medication's allowed kinds
    2. Calculate required volumes 
    3. Select smallest container that fits
    4. Prefer specified kind if given
    """
    # TODO: Implement smart container selection
    # For now, return None to require explicit selection
    return None


def auto_select_solvent(
    med: Medication,
    container: Container,
    rules_state: RulesState,
    use_case: str = "dilution"
) -> Optional[Solvent]:
    """
    Auto-select appropriate solvent when not specified.
    
    Logic:
    1. If container is prefilled, use container's solvent
    2. If empty container, use first compatible solvent from med.allowed_solvents
    3. Validate compatibility
    """
    if container.kind in {"bag_prefilled", "bottle_prefilled"}:
        # Use container's solvent
        if container.solvent and container.solvent in rules_state.solvents:
            return rules_state.solvents[container.solvent]
    
    # For empty containers, use first allowed solvent
    for solvent_id in med.allowed_solvents:
        if solvent_id in rules_state.solvents:
            solvent = rules_state.solvents[solvent_id]
            # Check if appropriate for use case
            if use_case == "reconstitution" and solvent.for_reconstitution:
                return solvent
            elif use_case == "dilution" and solvent.for_dilution:
                return solvent
    
    return None


# ---------------------------
# Part 5: Main Compute Function
# ---------------------------

def compute_protocol(
    compute_input: ComputeInput,
    rules_state: RulesState
) -> ComputeResult:
    """
    SPEC: Main computation entry point.
    
    Takes user input, runs all calculations and validations,
    returns complete result for PDF generation.
    """
    # Validate medication exists
    if compute_input.medication_id not in rules_state.meds:
        return ComputeResult(
            medication_id=compute_input.medication_id,
            dose_mg=compute_input.dose_mg,
            num_preparations=compute_input.num_preparations,
            errors=[f"Unknown medication ID: {compute_input.medication_id}"]
        )
    
    med = rules_state.meds[compute_input.medication_id]
    
    # Auto-select or validate container
    if compute_input.container_id:
        if compute_input.container_id not in rules_state.containers:
            return ComputeResult(
                medication_id=compute_input.medication_id,
                dose_mg=compute_input.dose_mg,
                num_preparations=compute_input.num_preparations,
                container_id=compute_input.container_id,
                medication=med,
                medication_name=med.name,
                errors=[f"Unknown container ID: {compute_input.container_id}"]
            )
        container = rules_state.containers[compute_input.container_id]
    else:
        # Auto-select container (placeholder for now)
        container = auto_select_container(med, compute_input.dose_mg, rules_state)
        if not container:
            return ComputeResult(
                medication_id=compute_input.medication_id,
                dose_mg=compute_input.dose_mg,
                num_preparations=compute_input.num_preparations,
                medication=med,
                medication_name=med.name,
                errors=["No container specified and auto-selection not yet implemented"]
            )
    
    # Determine final volume
    final_volume_ml = compute_input.final_volume_ml or container.capacity_ml
    
    # Auto-select or validate solvent
    if compute_input.solvent:
        if compute_input.solvent not in rules_state.solvents:
            return ComputeResult(
                medication_id=compute_input.medication_id,
                dose_mg=compute_input.dose_mg,
                num_preparations=compute_input.num_preparations,
                container_id=compute_input.container_id,
                final_volume_ml=final_volume_ml,
                medication=med,
                container=container,
                medication_name=med.name,
                container_description=f"{container.capacity_ml} mL {container.kind.replace('_', ' ').title()}",
                errors=[f"Unknown solvent ID: {compute_input.solvent}"]
            )
        final_solvent = rules_state.solvents[compute_input.solvent]
    else:
        final_solvent = auto_select_solvent(med, container, rules_state, "dilution")
        if not final_solvent:
            return ComputeResult(
                medication_id=compute_input.medication_id,
                dose_mg=compute_input.dose_mg,
                num_preparations=compute_input.num_preparations,
                container_id=compute_input.container_id,
                final_volume_ml=final_volume_ml,
                medication=med,
                container=container,
                medication_name=med.name,
                container_description=f"{container.capacity_ml} mL {container.kind.replace('_', ' ').title()}",
                errors=["No valid solvent found for this medication and container combination"]
            )
    
    # Calculate volumes based on medication
