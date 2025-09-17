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
from typing import Optional

class ComputeInput(BaseModel):
    """
    User's selections for a compounding request.
    Patient fields are for PDF only (not persisted).
    """
    medication_id: str
    container_id: Optional[str] = None # System can auto-select if not given
    solvent: Optional[str] = None # Depends on container/medication
    dose_mg: float = Field(gt=0, description="Dose in milligrams") # Total dose required (not per vial)
    patient_name: Optional[str] = Field(default=None, max_length=60) # For PDF only
    patient_hrn: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9]{9}$") # For PDF only
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
    # Input echo
    # resolve items from rules - medication and container details
    # safety validation (no default)
    # fields with default
    # powder meds related fields
    # multiple prep related fields
    # safety flags boolean
    # container adjustment details (if auto-resize)


    model_config = ConfigDict()