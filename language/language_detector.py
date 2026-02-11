# -----------------------------------
# Day 44A — Language Detection
# -----------------------------------

def detect_language(text: str) -> str:
    """
    Returns detected language code.
    Currently supports: 'en', 'ar'
    """

    # Arabic Unicode block
    for char in text:
        if "\u0600" <= char <= "\u06FF":
            return "ar"

    return "en"
