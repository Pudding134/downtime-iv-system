from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Annotated
from .auth import authenticate_admin


app = FastAPI(title="Downtime-IV-System", version="0.1.0")

@ app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse("Guest placeholder")


@app.get("/guest", response_class=HTMLResponse)
def guest_home():
    return HTMLResponse("Guest ok!")


@app.get("/admin", response_class=HTMLResponse)
def admin_home():
    return HTMLResponse("Admin ok!")

# get the admin login page first, with a form for the passphase (name will need to match the checking function)
# indicate error 
@app.get("/admin/login", response_class=HTMLResponse)
def admin_login(request: Request):
    error = "Invalid passphrase entered" if request.query_params.get("error") else ""
    # f is important here to fit the error message into the HTML page
    return HTMLResponse(f"""
        <form action="/admin/login" method="post">
            <input type="password" name="passphrase" placeholder="Admin Password">
            <button type="submit">Login</button>
        </form>
        <p style="color: red;">{error}</p>
    """)

# handle the admin login form submission, checking the passphase and redirecting accordingly
# will need to match the form field name
@app.post("/admin/login")
def admin_login_post(passphrase: str = Form("")):
    if authenticate_admin(passphrase):
        # Status code 303 tells the browser to follow the redirect with a GET 
        # after the POST (avoids resubmitting forms).
        return RedirectResponse(url="/admin", status_code=303)  # success -> go admin page
    return RedirectResponse("/admin/login?error=invalid_passphrase", status_code=303)  # failure -> go home page
