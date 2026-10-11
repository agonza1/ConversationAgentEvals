"""Load the shared selection contract without importing the audio SDK."""
import sys
from pathlib import Path

try:
    from speech_settings import SpeechSelection, deployment_speech_selection, selection_issues, selection_requirements
except ModuleNotFoundError:
    # Source checkout; Docker copies the canonical contract beside this file.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
    from app.services.speech_settings import SpeechSelection, deployment_speech_selection, selection_issues, selection_requirements
