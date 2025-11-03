"""
app/compute_request.py
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
    All required fields must be explicitly provided by the user.
    """
    # Required user selections
    medication_id: str
    container_id: str  # User must specify container
    dose_mg: float = Field(gt=0, description="Dose in milligrams")
    patient_name: str = Field(description="Patient name for PDF generation")
    patient_hrn: str = Field(description="Patient HRN/MRN for PDF generation")
    
    # Optional user selections
    solvent_id: Optional[str] = Field(None, description="Required for syringes and empty containers")
    num_preparations: int = Field(1, ge=1, le=100, description="Number of identical preparations")
    final_volume_ml: Optional[float] = Field(None, gt=0, description="Target volume (uses container capacity if not specified)")
    target_conc_mg_per_ml: Optional[float] = Field(None, gt=0, description="Target concentration if specified")

    class Config:
        json_schema_extra = {
            "example": {
                "medication_id": "PACLITAXEL",
                "container_id": "bag_ns_250",
                "dose_mg": 150.0,
                "patient_name": "John Doe",
                "patient_hrn": "MRN12345",
                "num_preparations": 1
            },
            "description": "IV compounding calculation request - all fields required",
            "properties": {
                "patient_name": {"writeOnly": True},  # Hide from response docs
                "patient_hrn": {"writeOnly": True}
            }
        }

    @model_validator(mode='after')
    def validate_solvent_requirements(self):
        """
        Validate that solvent is provided when required for container type.
        """
        # Note: We can't validate container.kind here since we don't have rules_state
        # This validation will be done in compute_protocol()
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
    container_id: str  # Always provided by user
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
    total_dose_umg: float = 0.0               # dose_mg × num_preparations
    
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
    elif container.kind in {"bag_empty", "container_empty"}:
        # Empty containers: no withdrawal needed (we add solvent as needed)
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
        # Empty containers and syringes: no withdrawal needed
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
# Part 4: Main Compute Function (No Auto-Selection)
# ---------------------------

def compute_protocol(
    compute_input: ComputeInput,
    rules_state: RulesState
) -> ComputeResult:
    """
    SPEC: Main computation entry point.
    
    Takes user input, runs all calculations and validations,
    returns complete result for PDF generation.
    All selections must be explicitly provided by user - no auto-selection.
    """
    # Validate medication exists
    if compute_input.medication_id not in rules_state.meds:
        return ComputeResult(
            medication_id=compute_input.medication_id,
            dose_mg=compute_input.dose_mg,
            num_preparations=compute_input.num_preparations,
            container_id=compute_input.container_id,
            final_volume_ml=0.0,
            errors=[f"Unknown medication ID: {compute_input.medication_id}"]
        )
    
    med = rules_state.meds[compute_input.medication_id]
    
    # Validate container exists (required)
    if compute_input.container_id not in rules_state.containers:
        return ComputeResult(
            medication_id=compute_input.medication_id,
            dose_mg=compute_input.dose_mg,
            num_preparations=compute_input.num_preparations,
            container_id=compute_input.container_id,
            final_volume_ml=0.0,
            medication_name=med.name,
            errors=[f"Unknown container ID: {compute_input.container_id}"]
        )
    
    container = rules_state.containers[compute_input.container_id]
    
    # Determine final volume
    final_volume_ml = compute_input.final_volume_ml or container.capacity_ml
    
    # Determine solvent - prefilled containers use their solvent, others require user selection
    final_solvent = None
    solvent_id = None
    
    if container.kind in {"bag_prefilled", "bottle_prefilled"}:
        # Use container's prefilled solvent
        if not container.solvent or container.solvent not in rules_state.solvents:
            return ComputeResult(
                medication_id=compute_input.medication_id,
                dose_mg=compute_input.dose_mg,
                num_preparations=compute_input.num_preparations,
                container_id=compute_input.container_id,
                final_volume_ml=final_volume_ml,
                medication_name=med.name,
                container_name=container.name,
                errors=[f"Container {container.id} has invalid solvent: {container.solvent}"]
            )
        final_solvent = rules_state.solvents[container.solvent]
        solvent_id = container.solvent
    else:
        # Syringe, empty bag, or empty container - solvent must be provided by user
        if not compute_input.solvent_id:
            container_type = container.kind.replace('_', ' ')
            return ComputeResult(
                medication_id=compute_input.medication_id,
                dose_mg=compute_input.dose_mg,
                num_preparations=compute_input.num_preparations,
                container_id=compute_input.container_id,
                final_volume_ml=final_volume_ml,
                medication_name=med.name,
                container_name=container.name,
                errors=[f"Solvent must be specified for {container_type} containers"]
            )
        
        if compute_input.solvent_id not in rules_state.solvents:
            return ComputeResult(
                medication_id=compute_input.medication_id,
                dose_mg=compute_input.dose_mg,
                num_preparations=compute_input.num_preparations,
                container_id=compute_input.container_id,
                solvent_id=compute_input.solvent_id,
                final_volume_ml=final_volume_ml,
                medication_name=med.name,
                container_name=container.name,
                errors=[f"Unknown solvent ID: {compute_input.solvent_id}"]
            )
        
        final_solvent = rules_state.solvents[compute_input.solvent_id]
        solvent_id = compute_input.solvent_id
    
    # Calculate volumes based on medication type
    warnings = []
    errors = []
    
    if med.presentation == "solution":
        drug_volume_ml, withdraw_volume_ml, final_conc, n_vials, vol_warnings = calculate_solution_volumes(
            med, container, compute_input.dose_mg, final_volume_ml, compute_input.num_preparations
        )
        warnings.extend(vol_warnings)
        
        # For solutions, powder-specific fields remain zero
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
        stock_leftover_ml = stock_total_ml - (drug_volume_ml * compute_input.num_preparations)
    
    # Safety validations
    conc_in_range, conc_warnings = validate_concentration(final_conc, med)
    warnings.extend(conc_warnings)
    
    solvent_compatible, solvent_warnings = validate_solvent_compatibility(
        med, final_solvent, "dilution"
    )
    warnings.extend(solvent_warnings)
    
    # TODO(M3.T3): Generate steps from steps_library + sequences
    steps = [
        f"Gather {med.name} {med.presentation}",
        f"Gather {container.name}",
        f"Use {final_solvent.name} for dilution",
        "Complete compounding as per SOP",
        f"Final concentration: {final_conc:.2f} mg/mL"
    ]
    
    return ComputeResult(
        # Input echo (NO PHI)
        medication_id=compute_input.medication_id,
        dose_mg=compute_input.dose_mg,
        num_preparations=compute_input.num_preparations,
        container_id=compute_input.container_id,
        solvent_id=solvent_id,
        final_volume_ml=final_volume_ml,
        target_conc_mg_per_ml=compute_input.target_conc_mg_per_ml,
        
        # Human-readable names
        medication_name=med.name,
        container_name=container.name,
        solvent_name=final_solvent.name,
        
        # Internal objects
        medication=med,
        container=container,
        final_solvent=final_solvent,
        
        # Calculated values
        final_concentration_mg_per_ml=final_conc,
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
# CLI Testing Helper
# ---------------------------

if __name__ == "__main__":
    # Quick test with sample data
    from pathlib import Path
    from .rules_loader import init_rules
    
    rules_state = init_rules(Path(__file__).parent.parent / "rules")
    
    # Test with explicit selections (new approach)
    test_input = ComputeInput(
        medication_id="PACLITAXEL",
        container_id="bag_ns_250",
        dose_mg=200.0,
        patient_name="Test Patient",
        patient_hrn="12345",
        num_preparations=1
        # solvent_id not needed for prefilled bag
    )
    
    # Test with syringe (requires solvent)
    test_input_syringe = ComputeInput(
        medication_id="PACLITAXEL",
        container_id="syringe_50ml",
        dose_mg=150.0,
        patient_name="Test Patient",
        patient_hrn="12345",
        solvent_id="D5",  # Required for syringe
        num_preparations=3
    )
    
    result = compute_protocol(test_input, rules_state)
    
    print(f"Medication: {result.medication_name}")
    print(f"Container: {result.container_name}")
    print(f"Solvent: {result.solvent_name}")
    print(f"Drug volume: {result.drug_volume_ml} mL")
    print(f"Withdraw: {result.withdraw_volume_ml} mL")
    print(f"Final conc: {result.final_concentration_mg_per_ml:.2f} mg/mL")
    print(f"Total preparations: {result.num_preparations}")
    print(f"Total drug volume: {result.total_drug_volume_ml} mL")
    print(f"Total vials needed: {result.total_vials_needed}")
    print(f"Warnings: {len(result.warnings)}")
    print(f"Errors: {len(result.errors)}")
    if result.warnings:
        for w in result.warnings:
            print(f"  - {w}")
    if result.errors:
        for e in result.errors:
            print(f"  - {e}")
    
    # Example of PDF generation workflow
    if not result.errors:
        pdf_context = PDFContext(
            patient_name=test_input.patient_name,
            patient_hrn=test_input.patient_hrn,
            generated_at="2025-09-21T10:30:00Z"
        )
        
        worksheet_data = WorksheetData(
            compute_result=result,
            pdf_context=pdf_context
        )
        
        print(f"\nPDF Context: {pdf_context.patient_name} ({pdf_context.patient_hrn})")
