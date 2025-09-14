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

from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from pathlib import Path
import math

from .rules_loader import RulesState, Medication, Container, Solvent


# ---------------------------
# Part 1: Input/Output Models
# ---------------------------

@dataclass
class ComputeInput:
    """
    User's selections for a compounding request.
    Patient fields are for PDF only (not persisted).
    """
    medication_id: str
    container_id: str
    dose_mg: float
    num_preparations: int = 1  # Number of identical preparations to make
    final_volume_ml: Optional[float] = None  # if None, use container capacity
    patient_name: str = ""
    patient_hrn: str = ""


@dataclass
class ComputeResult:
    """
    Everything needed to generate PDFs and display results.
    """
    # Input echo
    input_data: ComputeInput
    
    # Resolved objects from rules
    medication: Medication
    container: Container
    
    # Core computed values (no defaults)
    final_concentration_mg_per_ml: float
    final_volume_ml: float
    drug_volume_ml: float           # mL of drug solution to add
    withdraw_volume_ml: float       # mL to withdraw for headroom (if any)
    
    # Safety and validation (no defaults)
    warnings: List[str]
    errors: List[str]
    steps: List[str]
    
    # Fields with defaults
    final_solvent: Optional[Solvent] = None  # The solvent we're using for dilution
    
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
# Part 4: Main Compute Function
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
    # Validate input references exist
    if compute_input.medication_id not in rules_state.meds:
        return ComputeResult(
            input_data=compute_input,
            medication=None,
            container=None,
            final_concentration_mg_per_ml=0,
            final_volume_ml=0,
            drug_volume_ml=0,
            withdraw_volume_ml=0,
            warnings=[],
            errors=[f"Unknown medication ID: {compute_input.medication_id}"],
            steps=[]
        )
    
    if compute_input.container_id not in rules_state.containers:
        return ComputeResult(
            input_data=compute_input,
            medication=rules_state.meds[compute_input.medication_id],
            container=None,
            final_concentration_mg_per_ml=0,
            final_volume_ml=0,
            drug_volume_ml=0,
            withdraw_volume_ml=0,
            warnings=[],
            errors=[f"Unknown container ID: {compute_input.container_id}"],
            steps=[]
        )
    
    # Get objects
    med = rules_state.meds[compute_input.medication_id]
    container = rules_state.containers[compute_input.container_id]
    
    # Determine final volume
    final_volume_ml = compute_input.final_volume_ml or container.capacity_ml
    
    # TODO(M3.T2): Auto-upsize container if needed
    
    # Calculate volumes based on medication type
    warnings = []
    errors = []
    
    if med.presentation == "solution":
        drug_volume_ml, withdraw_volume_ml, final_conc, n_vials_solution, vol_warnings = calculate_solution_volumes(
            med, container, compute_input.dose_mg, final_volume_ml, compute_input.num_preparations
        )
        warnings.extend(vol_warnings)
        
        # For solutions, we use the container's solvent for dilution
        if container.kind in {"bag_prefilled", "bottle_prefilled"}:
            if not container.solvent or container.solvent not in rules_state.solvents:
                errors.append(f"Container {container.id} has invalid solvent: {container.solvent}")
                final_solvent = None
            else:
                final_solvent = rules_state.solvents[container.solvent]
        else:
            # For empty containers, we need to determine the best solvent
            # For now, use the first allowed solvent
            if med.allowed_solvents and med.allowed_solvents[0] in rules_state.solvents:
                final_solvent = rules_state.solvents[med.allowed_solvents[0]]
            else:
                errors.append("No valid solvent found for empty container")
                final_solvent = None
        
        # For solutions, set n_vials from calculation
        n_vials = n_vials_solution
        # Powder-specific fields remain zero for solutions
        reconst_per_vial_ml = 0.0
        stock_conc_mg_per_ml = 0.0
        stock_total_ml = 0.0
        stock_leftover_ml = 0.0
    
    else:  # powder
        (n_vials, reconst_per_vial_ml, stock_conc_mg_per_ml, stock_total_ml,
         drug_volume_ml, withdraw_volume_ml, final_conc, vol_warnings) = calculate_powder_volumes(
            med, container, compute_input.dose_mg, final_volume_ml, compute_input.num_preparations
        )
        warnings.extend(vol_warnings)
        stock_leftover_ml = stock_total_ml - drug_volume_ml
        
        # For powders, final solvent is determined by container or medication rules
        if container.kind in {"bag_prefilled", "bottle_prefilled"}:
            if not container.solvent or container.solvent not in rules_state.solvents:
                errors.append(f"Container {container.id} has invalid solvent: {container.solvent}")
                final_solvent = None
            else:
                final_solvent = rules_state.solvents[container.solvent]
        else:
            # Use first allowed solvent for dilution
            if med.allowed_solvents and med.allowed_solvents[0] in rules_state.solvents:
                final_solvent = rules_state.solvents[med.allowed_solvents[0]]
            else:
                errors.append("No valid solvent found for empty container")
                final_solvent = None
    
    # Safety validations
    conc_in_range, conc_warnings = validate_concentration(final_conc, med)
    warnings.extend(conc_warnings)
    
    solvent_compatible = True
    if final_solvent:
        solvent_compatible, solvent_warnings = validate_solvent_compatibility(
            med, final_solvent, "dilution"
        )
        warnings.extend(solvent_warnings)
    
    # TODO(M3.T3): Generate steps from steps_library + sequences
    steps = [
        f"Gather {med.name} {med.presentation}",
        f"Gather {container.capacity_ml} mL {container.kind.replace('_', ' ')}",
        "Complete compounding as per SOP",
        f"Final concentration: {final_conc:.2f} mg/mL"
    ]
    
    return ComputeResult(
        input_data=compute_input,
        medication=med,
        container=container,
        final_solvent=final_solvent,
        final_concentration_mg_per_ml=final_conc,
        final_volume_ml=final_volume_ml,
        drug_volume_ml=drug_volume_ml,
        withdraw_volume_ml=withdraw_volume_ml,
        n_vials=n_vials,
        reconst_per_vial_ml=reconst_per_vial_ml,
        stock_conc_mg_per_ml=stock_conc_mg_per_ml,
        stock_total_ml=stock_total_ml,
        stock_leftover_ml=stock_leftover_ml,
        total_drug_volume_ml=drug_volume_ml * compute_input.num_preparations,
        total_vials_needed=n_vials,
        total_dose_mg=compute_input.dose_mg * compute_input.num_preparations,
        warnings=warnings,
        errors=errors,
        concentration_in_range=conc_in_range,
        solvent_compatible=solvent_compatible,
        steps=steps
    )


# ---------------------------
# CLI Testing Helper
# ---------------------------

if __name__ == "__main__":
    # Quick test with sample data
    from pathlib import Path
    from .rules_loader import init_rules
    
    rules_state = init_rules(Path(__file__).parent.parent / "rules")
    
    # Test with a solution medication
    test_input = ComputeInput(
        medication_id="PACLITAXEL",
        container_id="bag_ns_250",
        dose_mg=200.0,
        patient_name="Test Patient",
        patient_hrn="12345"
    )
    
    result = compute_protocol(test_input, rules_state)
    
    print(f"Medication: {result.medication.name}")
    print(f"Container: {result.container.id}")
    print(f"Drug volume: {result.drug_volume_ml} mL")
    print(f"Withdraw: {result.withdraw_volume_ml} mL")
    print(f"Final conc: {result.final_concentration_mg_per_ml:.2f} mg/mL")
    print(f"Warnings: {len(result.warnings)}")
    print(f"Errors: {len(result.errors)}")
    if result.warnings:
        for w in result.warnings:
            print(f"  - {w}")
    if result.errors:
        for e in result.errors:
            print(f"  - {e}")
