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
from rules_loader import RulesState

# Custom exception class for domain-specific errors
@dataclass
class DomainError(Exception):
    code: str
    message: str
    field: Optional[str] = None
    hint: Optional[str] = None
    context: Optional[dict] = None

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
    target_conc_mg_per_ml: Optional[float] = Field(default=None, gt=0, description="Product target concentration dose.") # Usually calculated
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
    target_conc_mg_per_ml: Optional[float] = None

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
    container_adjustment_vol_ml: Optional[float] = Field(default=None, description="mL to withdraw or add volume to adjust concentration (withdrawal done before drug addition)")
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

def plan_compound(input_data: ComputeInput, rules:RulesState) -> ComputeOutput:
    # 1) Lookups
    med = rules.meds.get(input_data.medication_id)
    if not med:
        raise DomainError("unknown_medication", "Medication ID not found.", field="medication_id")

    ctr = rules.containers.get(input_data.container_id)
    if not ctr:
        raise DomainError("unknown_container", "Container ID not found.", field="container_id")

    # 2) Solvent policy by container kind
    ctr_kind = ctr.kind  # "bag_prefilled" | "bottle_prefilled" | "bag_empty" | "container_empty" | "syringe"

    # Prefilled container defines solvent; user must NOT provide solvent
    if ctr_kind in {"bag_prefilled", "bottle_prefilled"}:
        if input_data.solvent_id is not None:
            DomainError(
                "solvent_not_allowed_for_prefilled",
                "Solvent must not be provided for prefilled containers.",
                field="solvent_id",
                hint="Remove solvent_id or choose an empty bag/syringe.",
                ctx={"container_id": ctr.id},
            )
        final_solvent_id = ctr.solvent
        solvent_source = "container_prefill"

    # empty containers and syringes require user-selected solvent
    elif ctr_kind in {"bag_empty", "container_empty", "syringe"}:
        # User MUST provide solvent, and it must be allowed for the medication
        if input_data.solvent_id is None:
            raise DomainError(
                "solvent_required_for_empty_or_syringe",
                "Solvent is required for empty containers and syringes.",
                field="solvent_id",
            )
        if input_data.solvent_id not in med.allowed_solvents:
            allowed = ", ".join(med.allowed_solvents)
            raise DomainError(
                "incompatible_solvent_selected",
                "Selected solvent is not allowed for this medication.",
                field="solvent_id",
                hint=f"Pick from the allowed list of solvents: {allowed}.",
                ctx={"medication_id": med.id, "solvent_id": input_data.solvent_id},
            )
        final_solvent_id = input_data.solvent_id
        solvent_source = "user_selection"

    else:
        raise DomainError(
            "unsupported_container_kind",
            f"Container kind '{ctr_kind}' is not supported.",
            ctx={"container_id": ctr.id},
        )

    # 3) Display names
    medication_name = med.name
    container_name = getattr(ctr, "display_name", None) or ctr.id
    solv_obj = rules.solvents.get(final_solvent_id)
    solvent_name = (solv_obj.name if solv_obj else final_solvent_id)

    # 4) Return placeholder ComputeOutput (numbers computed in M3.T2)
    return ComputeOutput(
        # echo key inputs
        dose_mg=input_data.dose_mg,
        num_preparations=input_data.num_preparations,
        target_conc_mg_per_ml=input_data.target_conc_mg_per_ml,

        # ids & names
        medication_id=med.id,
        medication_name=medication_name,
        container_id=ctr.id,
        container_name=container_name,
        solvent_id=final_solvent_id,
        solvent_name=solvent_name,
        solvent_source=solvent_source,

        # no math yet
        drug_volume_ml=None,
        container_adjustment_vol_ml=None,
        final_product_conc_mg_per_ml=None,
        final_product_vol_ml=None,

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


# A) Compute stock concentration (mg/mL)
# 	•	Solution meds: concentration is stock.mg_per_ml (from stock.mg / stock.volume_ml or a direct field if you stored it).
# 	•	Powder meds: concentration is stock.mg / reconstitution.volume_ml after reconstitution.
# You already enforce that powder must have a diluent + volume in your rules loader.

# What to add (minimal)

# Inside your compute_endpoint, right after you’ve resolved med, compute:
# 	•	stock_conc = ... (float, mg/mL)
# 	•	For powders, also compute a vial math scaffold (just counts/placeholders now): required_num_vials_per_preparation and reconst_per_vial_vol_ml if your YAML defines a per-vial reconstitution.

# Why only this now

# You’ll be able to:
# 	•	return stock_conc (as reconst_vial_conc_mg_per_ml for powders or stock_conc_mg_per_ml if you add that field),
# 	•	and use it in the next step to derive the drug volume.





# B) Establish final volume policy (prefilled vs empty/syringe)
# C) Compute drug volume (mL) from dose and stock conc
# D) Compute container adjustment (pre-withdraw or top-up)
# E) Set final product conc and in-range warnings
