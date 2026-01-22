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

from dataclasses import dataclass
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal, Optional, List
from .rules_loader import Medication, RulesState, Container

# Custom exception class for domain-specific errors
@dataclass
class DomainError(Exception):
    code: str
    message: str
    field: Optional[str] = None
    hint: Optional[str] = None
    context: Optional[dict] = None


# ---------------------------
# Part 1: Input/Output Models
# ---------------------------
class ComputeInput(BaseModel):
    """
    User's selections for a compounding request.
    Patient fields are for PDF only (not persisted).
    """
    medication_id: str
    container_id: str
    solvent_id: Optional[str] = None # Depends on container/medication, required for empty containers/syringes
    dose_mg: float = Field(gt=0, description="Dose in milligrams") # Total dose required (not per vial)
    patient_name: Optional[str] = Field(default=None, max_length=60, repr=False) # For PDF only
    patient_hrn: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9]{9}$", repr=False) # For PDF only
    container_adjustment_vol_ml: float = Field(default=0.0, description="Container volume adjustment.") # Usually calculated
    num_preparations: int = Field(default=1, ge=1, le=50, description="Number of identical prep to make.")  # Default to single prep

    model_config = ConfigDict(
                            extra="forbid", # Forbid extra fields
                            str_strip_whitespace=True, # Strip leading / trailing whitespace from strings
                            )  # End of model config

class ComputeOutput(BaseModel):
    """
    Result of computation to return to frontend.
    Includes all details for PDF generation.
    """
    # Input echo - key values
    dose_mg: float
    num_preparations: int
    container_adjustment_vol_ml: float


    # Input echo - id & names
    medication_id: str
    medication_name: str
    container_id: str
    container_name: str
    solvent_id: str
    solvent_name: str
    solvent_source: Literal["container_prefill", "user_selection"]


    # Core computed values
    drug_volume_ml: Optional[float] = Field(None, gt=0, description="mL of drug solution to add")
    container_start_vol = float = Field(ge=0.0, description= "The container prefill solvent volume, if any.")
    container_adjustment_vol_ml: float = Field(default=0.0, description="mL to withdraw or add volume to adjust concentration (withdrawal done before drug addition)")
    final_product_conc_mg_per_ml: Optional[float] = Field(default=None, description="Final product concentration")
    final_product_vol_ml: Optional[float] = Field(default=None, description="Final product volume")

    # safety validation (no default)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)

    # powder meds related fields
    required_num_vials_per_preparation: int = Field(default=1, gt=0, description="Number of vials needed for powder medications.")
    reconst_per_vial_vol_ml: Optional[float] = Field(None, gt=0, description="mL needed to reconstitute each vial")
    reconst_vial_conc_mg_per_ml: Optional[float] = Field(None, gt=0, description="Concentration after reconstitution")
    reconst_vial_total_vol_ml: Optional[float] = Field(None, gt=0, description="Total reconstituted volume from all vials")
    reconst_vial_total_leftover_vol_ml: Optional[float] = Field(default=None, description="Unused reconstituted volume")

    # multiple prep related fields
    total_required_drug_volume_ml: Optional[float] = Field(None, gt=0, description="Total drug volume for all preparations")
    total_vials_needed: int = Field(default=1, gt=0, description="Total vials across all preparations")
    total_dose_mg_required: Optional[float] = Field(None, gt=0, description="dose_mg × num_preparations")

    # safety flags boolean - both put to None for now till we decide how to use
    concentration_in_range: Optional[bool] = None
    solvent_compatible: Optional[bool] = None

    # container adjustment details (if auto-resize)

    # temporary variable for testing
    stock_conc_mg_per_ml: Optional[float] = None


    model_config = ConfigDict(
                            extra="forbid", # Forbid extra fields
                            str_strip_whitespace=True, # Strip leading / trailing whitespace from strings
                            json_schema_extra = {
                                "example": {
                                    "medication_id": "PACLITAXEL",
                                    "medication_name": "Paclitaxel 300mg/50mL",
                                    "dose_mg": 150.0,
                                    "num_preparations": 3,
                                    "container_id": "bottle_ns_250",
                                    "container_name": "250 mL Normal Saline Bottle (Ecoflac)",
                                    "solvent_id": "NS",
                                    "solvent_name": "Normal Saline",
                                    "solvent_source": "container_prefill",
                                    "final_product_conc_mg_per_ml": 0.5454545,
                                    "drug_volume_ml": 25.0,
                                    "final_product_vol_ml": 275.0,
                                    "concentration_in_range": True
                                }
                            },
                            description="Output model for computed compounding protocol."
                            )  # End of model config



# ---------------------------
# Part 2: Core Math Functions
# ---------------------------

def compute_stock_concentration(medication: Medication) -> float:
    """
    Compute the stock concentration (mg/mL) for any medication.
    Handles both solution and powder types.
    Raises DomainError if essential fields are missing or invalid.
    """
    # Solution medication
    if medication.presentation == "solution":
        if medication.stock.strength is None or medication.stock.volume_ml is None:
            raise DomainError(
                "missing_medication_stock_info",
                "Medication stock information is incomplete.",
                field="medication_id",
                hint="Ensure stock strength and volume_ml are defined for solution medications.",
                ctx={"medication_id": medication.id},
            )
        return medication.stock.strength_mg() / medication.stock.volume_ml
    
    # Powder medication
    elif medication.presentation == "powder":
        if medication.stock.strength is None or medication.reconstitution.volume_ml is None:
            raise DomainError(
                "missing_medication_reconstitution_info",
                "Medication reconstitution information is incomplete.",
                field="medication_id",
                hint="Ensure stock strength and reconstitution volume_ml are defined for powder medications.",
                ctx={"medication_id": medication.id},
            )
        # if a specific concentration after reconstitution is provided, use it
        if medication.reconstitution.conc_after_recon_mg_per_ml is not None:
            return medication.reconstitution.conc_after_recon_mg_per_ml
        # otherwise compute from stock strength and reconstitution volume
        else:
            return medication.stock.strength_mg() / medication.reconstitution.volume_ml
    # Unsupported medication presentation
    else:
        raise DomainError(
            "unsupported_medication_presentation",
            f"Medication presentation '{medication.presentation}' is not supported.",
            ctx={"medication_id": medication.id},
        )


def determine_solvent_for_medication(medication: Medication, container: Container, solvent_id: Optional[str] = None) -> tuple[str, str]:
    """
    Determine the solvent based on container kind.
    For prefilled containers, return the container's solvent.
    For empty containers or syringes, raise error as user must select solvent.

    Returns:
        tuple[str, str]: A tuple containing (solvent_id, source) where source is
                        either "container_prefill" or "user_selection"
    """
    # Prefilled container defines solvent; user must NOT provide solvent, raise error if they do
    if container.kind in {"bag_prefilled", "bottle_prefilled"}:
        if solvent_id is not None:
            raise DomainError(
                "solvent_not_allowed_for_prefilled",
                "Solvent must not be provided for prefilled containers.",
                field="solvent_id",
                hint="Remove solvent_id or choose an empty bag/syringe.",
                ctx={"container_id": container.id},
            )
        return container.solvent, "container_prefill"
    
    # empty containers and syringes require user-selected solvent
    elif container.kind in {"bag_empty", "container_empty", "syringe"}:
        # User MUST provide solvent, and it must be allowed for the medication
        if solvent_id is None:
            raise DomainError(
                "solvent_required_for_empty_or_syringe",
                "Solvent is required for empty containers and syringes.",
                field="solvent_id",
            )
        if solvent_id not in medication.allowed_solvents:
            allowed = ", ".join(medication.allowed_solvents)
            raise DomainError(
                "incompatible_solvent_selected",
                "Selected solvent is not allowed for this medication.",
                field="solvent_id",
                hint=f"Pick from the allowed list of solvents: {allowed}.",
                ctx={"medication_id": medication.id, "solvent_id": solvent_id},
            )
        return solvent_id, "user_selection"

    else:
        raise DomainError(
            "unsupported_container_kind",
            f"Container kind '{container.kind}' is not supported.",
            ctx={"container_id": container.id},
        )


def compute_final_product_vol_with_adjustment(input_data: ComputeInput, container: Container, drug_volume_ml: float) -> float:
    """
    Compute the final product concentration and adjustment volume based on input data.
    Returns a tuple of (final_product_conc_mg_per_ml, adjustment_volume_ml).
    """
    # calculate the total product volume after drug addition - prefilled containers use their prefill volume
    if container.kind in {'bag_prefilled', 'bottle_prefilled'}:
        total_volume_ml = container.prefill_volume_ml + drug_volume_ml + input_data.container_adjustment_vol_ml
    # empty containers and syringes only have drug volume
    elif container.kind in {'syringe', 'bag_empty', 'container_empty'}:
        total_volume_ml = drug_volume_ml + input_data.container_adjustment_vol_ml
    else:
        raise DomainError(
            "unsupported_container_kind",
            f"Container kind '{container.kind}' is not supported.",
            ctx={"container_id": container.id},
        )
    
    return total_volume_ml


def compute_final_product_concentration(input_data: ComputeInput, total_product_volume: float) -> float:
    return input_data.dose_mg / total_product_volume


# ---------------------------
# Part 4: Main Compute Function (No Auto-Selection)
# ---------------------------
def plan_compound(input_data: ComputeInput, rules: RulesState) -> ComputeOutput:
    """
    SPEC: Main computation entry point.
    
    Takes user input, runs all calculations and validations,
    returns complete result for PDF generation.
    All selections must be explicitly provided by user - no auto-selection.
    """
    # 1) Validate medication exist
    med = rules.meds.get(input_data.medication_id)
    if not med:
        raise DomainError("unknown_medication", "Medication ID not found.", field="medication_id")

    # 2) Validate container exist
    ctr = rules.containers.get(input_data.container_id)
    if not ctr:
        raise DomainError("unknown_container", "Container ID not found.", field="container_id")

    # 3) Determine solvent - prefilled containers use their solvent, others require user selection
    final_solvent_id, solvent_source = determine_solvent_for_medication(
        medication=med,
        container=ctr,
        solvent_id=input_data.solvent_id
    )


    # 4) Display names
    medication_name = med.name
    container_name = getattr(ctr, "name", None) or ctr.id
    solv_obj = rules.solvents.get(final_solvent_id)
    solvent_name = (solv_obj.name if solv_obj else final_solvent_id)

    # 5) Compute stock concentration (mg/mL)
    stock_conc_mg_per_ml = compute_stock_concentration(med)

    # 6) Compute drug volume (mL) from dose and stock conc
    drug_volume_ml = input_data.dose_mg / stock_conc_mg_per_ml

    # 7) Compute product volume with adjustment
    total_volume_ml = compute_final_product_vol_with_adjustment(
        input_data=input_data,
        container=ctr,
        drug_volume_ml=drug_volume_ml
    )

    # 8) Compute final product concentration
    final_product_conc_mg_per_ml = compute_final_product_concentration(input_data, total_volume_ml)







    # ) Return placeholder ComputeOutput (numbers computed in M3.T2)
    return ComputeOutput(
        # echo key inputs
        dose_mg=input_data.dose_mg,
        num_preparations=input_data.num_preparations,

        # ids & names
        medication_id=med.id,
        medication_name=medication_name,
        container_id=ctr.id,
        container_name=container_name,
        solvent_id=final_solvent_id,
        solvent_name=solvent_name,
        solvent_source=solvent_source,

        # no math yet
        drug_volume_ml=drug_volume_ml,
        container_start_vol = ctr.prefill_volume_ml,
        container_adjustment_vol_ml=input_data.container_adjustment_vol_ml,
        final_product_conc_mg_per_ml=final_product_conc_mg_per_ml,
        final_product_vol_ml=total_volume_ml,

        # powder placeholders
        required_num_vials_per_preparation=1,
        reconst_per_vial_vol_ml=None,
        reconst_vial_conc_mg_per_ml=None,
        reconst_vial_total_vol_ml=None,
        reconst_vial_total_leftover_vol_ml=None,

        # total placeholders
        total_required_drug_volume_ml=None,
        total_vials_needed=1,
        total_dose_mg_required=None,

        # flags & lists
        concentration_in_range=None,
        solvent_compatible=True,
        warnings=[],
        errors=[],
        steps=[],
    )



