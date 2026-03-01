# core/nlu.py
from __future__ import annotations
import re


# =========================================================
# LANGUAGE DETECTION
# =========================================================

def detect_language(text: str) -> str:
    if not text:
        return "en"

    arabic = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
    ratio = arabic / max(len(text), 1)

    return "ar" if ratio > 0.20 else "en"


# =========================================================
# NORMALIZATION (VERY IMPORTANT FOR GCC MIXED TEXT)
# =========================================================

def normalize(text: str) -> str:
    if not text:
        return ""

    text = text.lower()

    replacements = {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ة": "ه",
        "ى": "ي",
    }

    for k, v in replacements.items():
        text = text.replace(k, v)

    return text


# =========================================================
# KEYWORDS
# =========================================================

BOOKING = [
    "احجز","حجز","حاب احجز","ابي احجز","ابغى احجز",
    "عايز احجز","عاوز احجز","اريد حجز","موعد","مواعيد",
    "book","booking","appointment","schedule","reserve",
]

RESCHEDULE = [
    "تعديل موعد","اغير موعد","change appointment",
    "reschedule","تاجيل","اقدم الموعد"
]

CANCEL = [
    "الغاء","الغاء الحجز","cancel","cancel appointment",
    "كنسل","شيل الموعد"
]

THANKS = [
    "شكرا","شكرًا","تسلم","يعطيك العافيه",
    "thanks","thank you","thx","appreciate"
]

RECEPTION = [
    "موظف","استقبال","ابي اكلم احد",
    "human","agent","representative",
    "talk to someone"
]

EMERGENCY = [
    "ما اقدر اتنفس",
    "اختناق",
    "نزيف",
    "فاقد الوعي",
    "unconscious",
    "can't breathe",
    "not breathing",
    "stroke",
    "heart attack",
]

# =========================================================
# SPECIALTIES
# =========================================================

SPECIALTIES = {
    "PEDIATRICS": [
        "اطفال","دكتور اطفال","طفلي","ولدي","baby","pediatric"
    ],

    "DENTAL": [
        "اسنان","دكتور اسنان","tooth","dentist",
        "molar","braces","filling"
    ],

    "CARDIOLOGY": [
        "قلب","الم صدر","cardiologist",
        "heart","palpitation"
    ],

    "NEUROLOGY": [
        "اعصاب","صداع","دوخه",
        "neurologist","migraine"
    ],

    "UROLOGY": [
        "تبول","احتباس","مسالك",
        "difficulty urinating",
        "kidney pain","urinary"
    ],

    "GENERAL": [
        "حراره","كحه","زكام",
        "fever","flu","fatigue"
    ],
}


# =========================================================
# INTENT DETECTOR
# =========================================================

def contains(text: str, keywords: list[str]) -> bool:
    return any(k in text for k in keywords)


def detect_intent(text: str):

    t = normalize(text)

    if contains(t, THANKS):
        return "THANKS", None

    if contains(t, RECEPTION):
        return "RECEPTION", None

    if contains(t, EMERGENCY):
        return "EMERGENCY", None

    if contains(t, CANCEL):
        return "CANCEL", None

    if contains(t, RESCHEDULE):
        return "RESCHEDULE", None

    # specialty detection
    for dept, words in SPECIALTIES.items():
        if contains(t, words):
            return "SPECIALTY", dept

    if contains(t, BOOKING):
        return "BOOK", None

    return "UNKNOWN", None