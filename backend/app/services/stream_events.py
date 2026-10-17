"""Application-owned progress labels; generic delivery lives in utils.streaming."""

from app.utils.streaming import StreamEvent, emit

PROGRESS = {
    "route": "Understanding your request…",
    "direct": "Writing your answer…",
    "plan": "Planning the source search…",
    "retrieve": "Searching your sources…",
    "rerank": "Reading relevant passages…",
    "assess": "Checking the evidence…",
    "generate": "Preparing a grounded answer…",
    "verify": "Verifying citations…",
    "repair": "Refining the supported answer…",
    "release": "Finalizing your answer…",
    "persist": "Saving your answer…",
}


def progress(stage: str) -> None:
    """Emit the fixed display message for `stage`, a key of `PROGRESS`.

    Readers only ever see these fixed labels, never prompts or passage text.
    """
    emit(StreamEvent("progress", {"stage": stage, "message": PROGRESS[stage]}))
