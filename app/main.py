from pydantic import BaseModel
from typing import Literal
from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Annotated
from .auth import authenticate_admin, make_session, read_session, is_fresh
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from .rules_loader import RulesState, init_rules

class RulesStatus(BaseModel):
    """
    To force returned RulesStatus response into this particular shape.
    - version: Rules version
    - integrity: Fast single source of truth whether rules are ready # ["ok", "mismatch", "missing"]
    - badge: Rules badge
    - counts: Count of each category # {"meds": int, "containers": int, "solvents": int}
    - num_errors: Number of errors caught
    - errors: list of caught errors against the rules
    """
    version: str | None
    integrity: Literal["ok", "mismatch", "missing"]
    badge: str
    counts: dict  # {"meds": int, "containers": int, "solvents": int}
    num_errors: int
    errors: list[str]

TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "views"),
    autoescape=select_autoescape(["html", "xml"])
)

def render(name: str, **ctx):
    tpl = TEMPLATES.get_template(name)
    return HTMLResponse(tpl.render(**ctx))

# Helper to raise HTTP 422 with structured error info
def err422(code: str, message: str, *, field: str | None = None, hint: str | None = None, ctx: dict | None = None) -> None:
    detail = {"error": {"code": code, "message": message}}
    if field: detail["error"]["field"] = field
    if hint:  detail["error"]["hint"]  = hint
    if ctx:   detail["error"]["context"] = ctx
    raise HTTPException(status_code=422, detail=detail)


app = FastAPI(title="Downtime-IV-System", version="0.1.0")

RULES_STATE_AT_STARTUP: RulesState | None = None
RULES_BADGE = "Rules - not loaded"

@app.on_event("startup")
def _load_rules():
    global RULES_STATE_AT_STARTUP, RULES_BADGE
    RULES_STATE_AT_STARTUP = init_rules(Path(__file__).parent.parent / "rules")
    # For T2.1 we only show counts/OK; T3 will set a real badge from manifest.
    RULES_BADGE = RULES_STATE_AT_STARTUP.badge_text

@app.get("/rules/status", response_model=RulesStatus)
def rules_status():
    """
    Read-only health/status for the installed Data Pack.
    Safe to call as Guest; Admin UI will also use this.
    """
    counts = {
        "meds": RULES_STATE_AT_STARTUP.counts[0],
        "containers": RULES_STATE_AT_STARTUP.counts[1],
        "solvents": RULES_STATE_AT_STARTUP.counts[2]
    }
    errors_list = RULES_STATE_AT_STARTUP.errors or []
    payload = {
        "version": RULES_STATE_AT_STARTUP.rules_version, 
        "integrity": RULES_STATE_AT_STARTUP.integrity, 
        "badge": RULES_STATE_AT_STARTUP.badge_text,
        "counts": counts,
        "num_errors": len(errors_list),
        "errors": errors_list[:5] # cap to the first 5 errors in the list
    }
    return payload


@app.get("/", response_class=HTMLResponse)
def root():
    errors = RULES_STATE_AT_STARTUP.errors if (RULES_STATE_AT_STARTUP and RULES_STATE_AT_STARTUP.errors) else []
    return render("home_guest.html", rules_badge=RULES_BADGE, rules_errors=errors)


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    # get the cookies token by the cookies name
    token = request.cookies.get("dv_sess")
    # verify if payload valid or check if session is fresh (within timeout period)
    payload = read_session(token) if token else None
    if not payload or not is_fresh(payload):
        return RedirectResponse(url="/admin/login?error=expired", status_code=303)
    
    # render page if all valid
    errors = RULES_STATE_AT_STARTUP.errors if (RULES_STATE_AT_STARTUP and RULES_STATE_AT_STARTUP.errors) else []
    response = render("home_admin.html", rules_badge = RULES_BADGE, rules_errors=errors)

    # Refresh idle timer (slide the window): issue a fresh token
    new_token = make_session()
    response.set_cookie(key="dv_sess",
                        value=new_token, 
                        httponly=True, 
                        samesite="lax")
    return response

# get the admin login page first, with a form for the passphase (name will need to match the checking function)
# indicate error 
@app.get("/admin/login", response_class=HTMLResponse)
def admin_login(request: Request):
    error = request.query_params.get("error")
    # pass error to render function, the error variable will be used in the html as error msg
    return render("admin_login.html", error=error if error else "")

# handle the admin login form submission, checking the passphase and redirecting accordingly
# will need to match the form field name
@app.post("/admin/login")
def admin_login_post(passphrase: str = Form("")):
    if authenticate_admin(passphrase):
        # create token / session
        token = make_session()
        # Status code 303 tells the browser to follow the redirect with a GET 
        response = RedirectResponse(url="/admin", status_code=303)
        # Set the session cookie into the response
        response.set_cookie(
            key="dv_sess",          # Cookie name for session identification
            value=token,            # The signed session token containing admin role and timestamp
            httponly=True,          # Prevents JavaScript access to cookie (XSS protection)
            samesite="lax"          # Allows cookie on same-site requests and top-level navigation (CSRF protection)
        )
        return response
    return RedirectResponse("/admin/login?error=wrong_password", status_code=303)  # failure -> go home page


# Handle admin logout by clearing session and redirecting to home page
@app.post("/admin/logout")
def admin_logout():
    # Redirect to home page after logout
    response = RedirectResponse(url="/", status_code=303)
    # Delete the session cookie to terminate admin session
    response.delete_cookie(key="dv_sess")
    return response

# post/compute endpoint to handle compute requests
from .compute import ComputeInput, ComputeOutput    
@app.post("/compute", response_model=ComputeOutput)
def compute_endpoint(input_data: ComputeInput):
    # 0) Ensure rules are loaded
    if RULES_STATE_AT_STARTUP is None:
        # Rare, but return a clear 503 if init failed
        raise HTTPException(status_code=503, detail={"error": {"code": "rules_unavailable", "message": "Rules not loaded"}})

    # 1) Lookups
    med = RULES_STATE_AT_STARTUP.meds.get(input_data.medication_id)
    if not med:
        err422("unknown_medication", "Medication ID not found.", field="medication_id")

    ctr = RULES_STATE_AT_STARTUP.containers.get(input_data.container_id)
    if not ctr:
        err422("unknown_container", "Container ID not found.", field="container_id")

    # 2) Solvent policy by container kind
    kind = ctr.kind  # "bag_prefilled" | "bottle_prefilled" | "bag_empty" | "container_empty" | "syringe"

    if kind in {"bag_prefilled", "bottle_prefilled"}:
        # User must NOT provide solvent; container defines it
        if input_data.solvent_id is not None:
            err422(
                "solvent_not_allowed_for_prefilled",
                "Solvent must not be provided for prefilled containers.",
                field="solvent_id",
                hint="Remove solvent_id or choose an empty bag/syringe.",
                ctx={"container_id": ctr.id},
            )
        final_solvent_id = ctr.solvent
        solvent_source = "container_prefill"

    elif kind in {"bag_empty", "container_empty", "syringe"}:
        # User MUST provide solvent, and it must be allowed for the medication
        if input_data.solvent_id is None:
            err422(
                "solvent_required_for_empty_or_syringe",
                "Solvent is required for empty containers and syringes.",
                field="solvent_id",
            )
        if input_data.solvent_id not in med.allowed_solvents:
            allowed = ", ".join(med.allowed_solvents)
            err422(
                "incompatible_solvent",
                "Selected solvent is not allowed for this medication.",
                field="solvent_id",
                hint=f"Pick one of: {allowed}.",
                ctx={"medication_id": med.id, "solvent_id": input_data.solvent_id},
            )
        final_solvent_id = input_data.solvent_id
        solvent_source = "user_selection"

    else:
        err422(
            "unsupported_container_kind",
            f"Container kind '{kind}' is not supported.",
            ctx={"container_id": ctr.id},
        )

    # 3) Display names
    medication_name = med.name
    container_name = getattr(ctr, "display_name", None) or ctr.id
    solv_obj = RULES_STATE_AT_STARTUP.solvents.get(final_solvent_id)
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