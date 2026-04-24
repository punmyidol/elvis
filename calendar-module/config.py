import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

ICLOUD_EMAIL = os.getenv("ICLOUD_EMAIL", "")
ICLOUD_APP_PASSWORD = os.getenv("ICLOUD_APP_PASSWORD", "")
ICLOUD_CALDAV_URL = "https://caldav.icloud.com"
DB_PATH = os.path.join(os.path.dirname(__file__), "calendar.db")
DEFAULT_LOOKAHEAD_DAYS = 7
