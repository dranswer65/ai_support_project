# language/arabic_tone_engine.py

def select_arabic_tone(user_region=None, business_context="support"):
    """
    Returns: 'msa' or 'gulf_soft'
    """

    if business_context in ["banking", "legal", "government"]:
        return "msa"

    if user_region in ["KSA", "UAE", "KW", "QA", "BH"]:
        return "gulf_soft"

    return "msa"
