from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Annotated
from .auth import authenticate_admin
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "views"),
        autoescape=select_autoescape(["html", "xml"])
)

def render(name: str, **ctx):
    tpl = TEMPLATES.get_template(name)
    return HTMLResponse(tpl.render(**ctx))

app = FastAPI(title="Downtime-IV-System", version="0.1.0")

@ app.get("/", response_class=HTMLResponse)
def root():
    return render("home_guest.html", rules_badge="Rules - not loaded (M2)")


@app.get("/admin", response_class=HTMLResponse)
def admin_home():
    return render("home_admin.html", rules_badge = "Rules - not loaded (M2)")

# get the admin login page first, with a form for the passphase (name will need to match the checking function)
# indicate error 
@app.get("/admin/login", response_class=HTMLResponse)
def admin_login(request: Request):
    error = request.query_params.get("error")
    # pass error to render function, the error variable will be used in the html as error msg
    return render("admin_login.html", error="Invalid Admin passphrase entered!" if error else "")

# handle the admin login form submission, checking the passphase and redirecting accordingly
# will need to match the form field name
@app.post("/admin/login")
def admin_login_post(passphrase: str = Form("")):
    if authenticate_admin(passphrase):
        # Status code 303 tells the browser to follow the redirect with a GET 
        # after the POST (avoids resubmitting forms).
        return RedirectResponse(url="/admin", status_code=303)  # success -> go admin page
    return RedirectResponse("/admin/login?error=1", status_code=303)  # failure -> go home page
