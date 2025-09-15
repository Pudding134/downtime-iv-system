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

from pydantic import BaseModel
from typing import Optional

class ComputeInput(BaseModel):
    """
    User's selections for a compounding request.
    Patient fields are for PDF only (not persisted).
    """
    medication_id: str
    container_id: Optional[str] = None # System can auto-select if not given
    solvent: Optional[str] = None # Depends on container/medication
    dose_mg: float # Total dose required (not per vial)
    patient_name: Optional[str] = None # For PDF only
    patient_hrn: Optional[str] = None # For PDF only
    target_conc_mg_per_ml: Optional[float] = None # Usually calculated
    num_preparations: int = 1  # Default to single prep

class ComputeOutput(BaseModel):
    """
    Result of computation to return to frontend.
    Includes all details for PDF generation.
    """
    total_drug_volume_ml: float    # Scales linearly
    total_vials_needed: int        # Optimized via ceil() function
    total_dose_mg: float           # Scales linearly
    steps: list[dict]              # Step-by-step instructions
    warnings: list[str]            # Any safety warnings
    notes: Optional[str] = None    # Additional notes