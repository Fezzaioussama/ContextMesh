"""Readable counts for public stage summaries."""


def counted(count: int, singular: str, plural: str = "") -> str:
    word = singular if count == 1 else plural or f"{singular}s"
    return f"{count} {word}"
