import os
import time
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer, BadSignature

# load the env file
load_dotenv()

# session management - auto logout for admin
SESSION_SECRET = (os.getenv("SESSION_SECRET") or "dev-secret").strip()
TIMEOUT_MIN = int(os.getenv("LOCK_TIMEOUT_MIN") or "15")
_serial = URLSafeTimedSerializer(SESSION_SECRET, salt="downtime.v1")

# authenticate user input also check for empty string provided
def authenticate_admin(passphrase: str) -> bool:
    # access the passphrase + check for empty
    ADMIN_PASSPHRASE = (os.getenv("ADMIN_PASSPHRASE") or "").strip()
    p = (passphrase or "").strip()
    return bool(ADMIN_PASSPHRASE) and bool(p) and p == ADMIN_PASSPHRASE

# session management, create session + assign time
def make_session() -> str:
    return _serial.dumps({"role": "admin", "timeStart": int(time.time())})

# read session data
def read_session(token: str):
    try:
        return _serial.loads(token)
    except BadSignature:
        return None

# check if session is fresh = within timeout period
def is_fresh(payload: dict, minutes: int = TIMEOUT_MIN) -> bool:
    return (int(time.time()) - int(payload.get("timeStart", 0))) <= minutes * 60