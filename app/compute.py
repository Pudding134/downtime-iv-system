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

from pydantic import BaseModel, Field, ConfigDict
from typing import Literal, Optional, List

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