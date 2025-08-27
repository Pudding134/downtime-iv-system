import os
from dotenv import load_dotenv

# load the env file
load_dotenv()

# authenticate user input also check for empty string provided
def authenticate_admin(passphrase: str) -> bool:
    # access the passphrase + check for empty
    ADMIN_PASSPHRASE = (os.getenv("ADMIN_PASSPHRASE") or "").strip()
    p = (passphrase or "").strip()
    return bool(ADMIN_PASSPHRASE) and bool(p) and p == ADMIN_PASSPHRASE