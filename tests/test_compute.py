"""
tests/test_compute.py
Starter pytest suite for the M3 compute engine (plan_compound and helpers).

Conventions:
- Rules are loaded once per session from the real rules/ pack (session fixture).
- Error tests assert DomainError.code, not message text (messages may be reworded).
- Volume assertions use pytest.approx to avoid float-equality flakiness.

Fixture data used (from rules/):
- CARBOPLATIN: solution, 450 mg / 45 mL (10 mg/mL), solvents NS+D5, bag_prefilled only
- OXALIPLATIN: solution, solvents D5 only, bag_prefilled only
- TRASTUZUMAB: powder, conc_after_recon 21 mg/mL, solvent NS, bag_prefilled only
- BORTEZOMIB: powder, 3.5 mg / 3.5 mL recon (1 mg/mL), solvent NS, syringe only
- bag_ns_250: prefilled NS bag, prefill 250 mL, capacity 350 mL
- syringe_10ml / syringe_50ml: usable_fraction 0.8
"""

from pathlib import Path

import pytest

from app.compute import ComputeInput, DomainError, plan_compound
from app.rules_loader import init_rules

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


@pytest.fixture(scope="session")
def rules():
    return init_rules(RULES_DIR)


def make_input(**overrides) -> ComputeInput:
    """Valid baseline request; tests override the field(s) under test."""
    base = dict(
        medication_id="CARBOPLATIN",
        container_id="bag_ns_250",
        dose_mg=100.0,
    )
    base.update(overrides)
    return ComputeInput(**base)


def expect_domain_error(rules, code: str, **overrides):
    with pytest.raises(DomainError) as excinfo:
        plan_compound(make_input(**overrides), rules)
    assert excinfo.value.code == code
    return excinfo.value


# ---------------------------
# Happy paths
# ---------------------------

def test_solution_in_prefilled_bag(rules):
    # 100 mg at 10 mg/mL stock -> 10 mL drug; 250 prefill - 10 withdrawn + 10 drug = 250 mL
    out = plan_compound(make_input(container_adjustment_vol_ml=-10.0), rules)
    assert out.stock_conc_mg_per_ml == pytest.approx(10.0)
    assert out.drug_volume_ml == pytest.approx(10.0)
    assert out.container_start_vol_ml == pytest.approx(250.0)
    assert out.final_product_vol_ml == pytest.approx(250.0)
    assert out.final_product_conc_mg_per_ml == pytest.approx(100.0 / 250.0)
    assert out.solvent_id == "NS"
    assert out.solvent_source == "container_prefill"


def test_powder_with_recon_override_in_prefilled_bag(rules):
    # Trastuzumab stock conc comes from conc_after_recon_mg_per_ml (21), not 440/20 (22)
    # 210 mg at 21 mg/mL -> 10 mL drug; 250 prefill + 10 drug = 260 mL
    out = plan_compound(
        make_input(medication_id="TRASTUZUMAB", dose_mg=210.0),
        rules,
    )
    assert out.stock_conc_mg_per_ml == pytest.approx(21.0)
    assert out.drug_volume_ml == pytest.approx(10.0)
    assert out.container_start_vol_ml == pytest.approx(250.0)
    assert out.final_product_vol_ml == pytest.approx(260.0)
    assert out.solvent_id == "NS"
    assert out.solvent_source == "container_prefill"


def test_powder_in_syringe(rules):
    # Bortezomib: 3.5 mg / 3.5 mL recon = 1 mg/mL; syringe starts at 0 mL
    out = plan_compound(
        make_input(
            medication_id="BORTEZOMIB",
            container_id="syringe_50ml",
            solvent_id="NS",
            dose_mg=3.5,
        ),
        rules,
    )
    assert out.stock_conc_mg_per_ml == pytest.approx(1.0)
    assert out.drug_volume_ml == pytest.approx(3.5)
    assert out.container_start_vol_ml == pytest.approx(0.0)
    assert out.final_product_vol_ml == pytest.approx(3.5)
    assert out.solvent_source == "user_selection"


# ---------------------------
# Selection validation
# ---------------------------

def test_unknown_medication(rules):
    expect_domain_error(rules, "unknown_medication", medication_id="NOT_A_MED")


def test_unknown_container(rules):
    expect_domain_error(rules, "unknown_container", container_id="not_a_container")


def test_container_kind_not_allowed(rules):
    # Trastuzumab is bag_prefilled-only; a syringe must be rejected
    expect_domain_error(
        rules,
        "container_kind_not_allowed",
        medication_id="TRASTUZUMAB",
        container_id="syringe_50ml",
    )


# ---------------------------
# Solvent policy
# ---------------------------

def test_prefilled_solvent_incompatible(rules):
    # Oxaliplatin allows only D5; an NS-prefilled bag must be rejected
    expect_domain_error(
        rules,
        "prefilled_solvent_incompatible",
        medication_id="OXALIPLATIN",
        container_id="bag_ns_250",
    )


def test_solvent_not_allowed_for_prefilled(rules):
    # Prefilled containers define their own solvent; passing one is an error
    expect_domain_error(rules, "solvent_not_allowed_for_prefilled", solvent_id="NS")


def test_solvent_required_for_syringe(rules):
    expect_domain_error(
        rules,
        "solvent_required_for_empty_or_syringe",
        medication_id="BORTEZOMIB",
        container_id="syringe_50ml",
    )


def test_incompatible_solvent_selected(rules):
    # Bortezomib allows NS only
    expect_domain_error(
        rules,
        "incompatible_solvent_selected",
        medication_id="BORTEZOMIB",
        container_id="syringe_50ml",
        solvent_id="D5",
    )


# ---------------------------
# Adjustment guards
# ---------------------------

def test_withdrawal_exceeds_prefill(rules):
    expect_domain_error(
        rules,
        "withdrawal_exceeds_prefill",
        container_adjustment_vol_ml=-300.0,
    )


def test_negative_adjustment_on_syringe(rules):
    # Syringes start empty: there is nothing to withdraw
    expect_domain_error(
        rules,
        "invalid_container_adjustment",
        medication_id="BORTEZOMIB",
        container_id="syringe_50ml",
        solvent_id="NS",
        dose_mg=3.5,
        container_adjustment_vol_ml=-5.0,
    )


# ---------------------------
# Capacity checks (hard stops)
# ---------------------------

def test_bag_capacity_exceeded(rules):
    # 250 prefill + 95 added + 10 drug = 355 mL > 350 capacity
    expect_domain_error(
        rules,
        "container_capacity_exceeded",
        container_adjustment_vol_ml=95.0,
    )


def test_syringe_usable_capacity_exceeded(rules):
    # 10 mL syringe has 8 mL usable (0.8); 10 mg at 1 mg/mL -> 10 mL drug volume
    expect_domain_error(
        rules,
        "syringe_capacity_exceeded",
        medication_id="BORTEZOMIB",
        container_id="syringe_10ml",
        solvent_id="NS",
        dose_mg=10.0,
    )
