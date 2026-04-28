"""Request classifier — routes to chat/command/complex model."""

# Hebrew keywords for request classification
_COMMAND_KEYWORDS = {
    "הדלק",
    "כבה",
    "פתח",
    "סגור",
    "הפעל",
    "כבי",
    "הדליק",
    "תדליק",
    "תכבה",
    "תפתח",
    "תסגור",
    "תפעיל",
    "תכבי",
    "תדליקי",
    "הנמיך",
    "הגביר",
    "הגבר",
    "תעלה",
    "תוריד",
    "שנה",
    "שני",
    "הרתיח",
    "תרתיח",
    "תרתיחי",
    "לילה טוב",
    "בוקר טוב",
    "ערב טוב",
}

_CHAT_PATTERNS = {
    "מה שלומך",
    "שלום",
    "היי",
    "ספרי",
    "ספר לי",
    "בדיחה",
    "תודה",
    "יופי",
    "סבבה",
    "מה קורה",
    "מה נשמע",
    "אני בסדר",
    "מה העניינים",
    "איך את",
}

_COMPLEX_KEYWORDS = {
    "אוטומציה",
    "סצנה",
    "סקריפט",
    "automation",
    "תיצרי",
    "תמחקי",
    "תשנה",
    "למה",
    "תסביר",
    "מתי",
    "כמה זמן",
    "היסטוריה",
    "רשימה",
    "קניות",
    "יומן",
    "תזכורת",
    "הודעה",
}


def classify_request(user_text: str) -> str:
    """Classify request as 'chat', 'command', or 'complex'."""
    text = user_text.lower().strip().rstrip("?!.,")

    if len(text) < 40 and not any(kw in text for kw in _COMMAND_KEYWORDS):
        if any(kw in text for kw in _CHAT_PATTERNS):
            return "chat"

    if any(kw in text for kw in _COMPLEX_KEYWORDS):
        return "complex"

    if any(kw in text for kw in _COMMAND_KEYWORDS):
        return "command"

    return "complex"
