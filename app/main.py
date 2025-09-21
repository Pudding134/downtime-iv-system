from pydantic import BaseModel
from typing import Literal
from fastapi import FastAPI, Form, Request
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

app = FastAPI(title="Downtime-IV-System", version="0.1.0")

RULES_STATE: RulesState | None = None
RULES_BADGE = "Rules - not loaded"

@app.on_event("startup")
def _load_rules():
    global RULES_STATE, RULES_BADGE
    RULES_STATE = init_rules(Path(__file__).parent.parent / "rules")
    # For T2.1 we only show counts/OK; T3 will set a real badge from manifest.
    RULES_BADGE = RULES_STATE.badge_text

@app.get("/rules/status", response_model=RulesStatus)
def rules_status():
    """
    Read-only health/status for the installed Data Pack.
    Safe to call as Guest; Admin UI will also use this.
    """
    counts = {
        "meds": RULES_STATE.counts[0],
        "containers": RULES_STATE.counts[1],
        "solvents": RULES_STATE.counts[2]
    }
    errors_list = RULES_STATE.errors or []
    payload = {
        "version": RULES_STATE.rules_version, 
        "integrity": RULES_STATE.integrity, 
        "badge": RULES_STATE.badge_text,
        "counts": counts,
        "num_errors": len(errors_list),
        "errors": errors_list[:5] # cap to the first 5 errors in the list
    }
    return payload


@app.get("/", response_class=HTMLResponse)
def root():
    errors = RULES_STATE.errors if (RULES_STATE and RULES_STATE.errors) else []
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
    errors = RULES_STATE.errors if (RULES_STATE and RULES_STATE.errors) else []
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
def compute_endpoint(data: ComputeInput):
    """
    Main compute endpoint.
    Takes ComputeInput, returns ComputeOutput.
    """

