import os

from dotenv import load_dotenv

load_dotenv()

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MEDPLUM_CLIENT_ID = os.environ.get("MEDPLUM_CLIENT_ID", "")
MEDPLUM_CLIENT_SECRET = os.environ.get("MEDPLUM_CLIENT_SECRET", "")
MEDPLUM_BASE_URL = os.environ.get("MEDPLUM_BASE_URL", "https://api.medplum.com/")
MOSS_PROJECT_ID = os.environ.get("MOSS_PROJECT_ID", "")
MOSS_PROJECT_KEY = os.environ.get("MOSS_PROJECT_KEY", "")

CLAUDE_MODEL = "claude-opus-5"
