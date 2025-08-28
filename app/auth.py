import os
import time
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer, BadSignature

# Load environment variables from .env file
load_dotenv()

# Session management configuration - implements auto logout for admin
# Uses stateless, signed cookies to avoid needing a database, cache, or filesystem session store
# Perfect for offline downtime use
SESSION_SECRET = (os.getenv("SESSION_SECRET") or "dev-secret").strip()
TIMEOUT_MIN = int(os.getenv("LOCK_TIMEOUT_MIN") or "15")
# Create a serializer that signs and verifies JSON payloads for secure session tokens
_serial = URLSafeTimedSerializer(SESSION_SECRET, salt="downtime.v1")

# Authenticate admin login by comparing provided passphrase with environment variable
def authenticate_admin(passphrase: str) -> bool:
    # Get admin passphrase from environment and strip whitespace
    ADMIN_PASSPHRASE = (os.getenv("ADMIN_PASSPHRASE") or "").strip()
    p = (passphrase or "").strip()
    # Check that both the environment admin passphrase and the provided passphrase are non-empty,
    # and return True only if the provided passphrase matches the admin passphrase from the environment
    return bool(ADMIN_PASSPHRASE) and bool(p) and p == ADMIN_PASSPHRASE

# Create a signed session token containing admin role and current timestamp
def make_session() -> str:
    return _serial.dumps({"role": "admin", "timeStart": int(time.time())})

# Deserialize and verify session token, returning payload if valid
# Verifies signature and returns the payload if valid; returns None if tampered or malformed
# Catches BadSignature so routes can treat it as "not logged in"
def read_session(token: str):
    try:
        return _serial.loads(token)
    except BadSignature:
        return None

# Check if session is still within the timeout period (not expired)
def is_fresh(payload: dict, minutes: int = TIMEOUT_MIN) -> bool:
    # Calculate elapsed time since session start and check if it's within the timeout period
    # Returns True if session is still fresh (not expired), False if timeout exceeded
    return (int(time.time()) - int(payload.get("timeStart", 0))) <= minutes * 60