import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

os.environ.setdefault('TRANSFORMERS_CACHE', str(Path(__file__).parent / '.hf_cache'))

from transformers import pipeline as hf_pipeline  # noqa: E402

# mDeBERTa-v3 antrenat pe XNLI multilingv — include explicit română în date.
# Zero-shot: nu are nevoie de exemple de sentiment, înțelege semantic textul.
MODEL_NAME = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"

_CANDIDATE_LABELS    = ["sentiment pozitiv", "sentiment negativ", "sentiment neutru"]
_HYPOTHESIS_TEMPLATE = "Acest text exprimă {}."

_LABEL_MAP = {
    "sentiment pozitiv": "POSITIVE",
    "sentiment negativ": "NEGATIVE",
    "sentiment neutru":  "NEUTRAL",
}


def load_model():
    logger.info("Încărcare model zero-shot '%s'...", MODEL_NAME)
    pipe = hf_pipeline(
        task="zero-shot-classification",
        model=MODEL_NAME,
        device=-1,          # CPU explicit
    )
    logger.info("Model gata.")
    return pipe


def predict(text: str, pipe) -> str:
    """Returnează 'POSITIVE', 'NEGATIVE' sau 'NEUTRAL'."""
    result = pipe(
        text,
        candidate_labels=_CANDIDATE_LABELS,
        hypothesis_template=_HYPOTHESIS_TEMPLATE,
        multi_label=False,
    )
    top_label = result['labels'][0]
    return _LABEL_MAP.get(top_label, 'NEUTRAL')
