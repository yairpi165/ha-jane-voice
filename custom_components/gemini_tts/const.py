"""Constants for Gemini TTS integration."""

DOMAIN = "gemini_tts"

CONF_API_KEY = "api_key"
CONF_MODEL = "model"
CONF_VOICE = "voice"
CONF_LANGUAGE = "language"
CONF_STYLE_PROMPT = "style_prompt"
CONF_CACHE = "cache"

DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_VOICE = "callirrhoe"
DEFAULT_LANGUAGE = "he"
DEFAULT_STYLE_PROMPT = ""
DEFAULT_CACHE = True

MODELS = [
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
]

VOICES = [
    "achernar",
    "achird",
    "algenib",
    "algieba",
    "alnilam",
    "aoede",
    "autonoe",
    "callirrhoe",
    "charon",
    "despina",
    "enceladus",
    "erinome",
    "fenrir",
    "gacrux",
    "iapetus",
    "kore",
    "laomedeia",
    "leda",
    "orus",
    "puck",
    "pulcherrima",
    "rasalgethi",
    "sadachbia",
    "sadaltager",
    "schedar",
    "sulafat",
    "umbriel",
    "vindemiatrix",
    "zephyr",
    "zubenelgenubi",
]

SUPPORTED_LANGUAGES = [
    "he", "en", "ar", "fr", "es", "de", "it", "pt", "ru", "ja",
    "ko", "zh", "nl", "pl", "tr", "sv", "da", "no", "fi",
]
