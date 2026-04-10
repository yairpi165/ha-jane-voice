"""Config flow for Gemini TTS."""

import logging

import voluptuous as vol
from google import genai
from google.genai import types

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_API_KEY,
    CONF_CACHE,
    CONF_LANGUAGE,
    CONF_MODEL,
    CONF_STYLE_PROMPT,
    CONF_VOICE,
    DEFAULT_CACHE,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DEFAULT_STYLE_PROMPT,
    DEFAULT_VOICE,
    DOMAIN,
    MODELS,
    SUPPORTED_LANGUAGES,
    VOICES,
)

_LOGGER = logging.getLogger(__name__)


def _validate_api_key(api_key: str) -> None:
    """Test API key with a minimal TTS request."""
    client = genai.Client(api_key=api_key)
    client.models.generate_content(
        model=DEFAULT_MODEL,
        contents="Test",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=DEFAULT_VOICE,
                    )
                )
            ),
        ),
    )


class GeminiTTSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Gemini TTS config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle initial setup."""
        errors = {}

        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    _validate_api_key, user_input[CONF_API_KEY]
                )
            except Exception as e:
                _LOGGER.error("API key validation failed: %s", e)
                errors["base"] = "invalid_api_key"
            else:
                return self.async_create_entry(
                    title="Gemini TTS", data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                    vol.Optional(CONF_MODEL, default=DEFAULT_MODEL): vol.In(MODELS),
                    vol.Optional(CONF_VOICE, default=DEFAULT_VOICE): vol.In(VOICES),
                    vol.Optional(CONF_LANGUAGE, default=DEFAULT_LANGUAGE): vol.In(
                        SUPPORTED_LANGUAGES
                    ),
                    vol.Optional(
                        CONF_STYLE_PROMPT, default=DEFAULT_STYLE_PROMPT
                    ): str,
                    vol.Optional(CONF_CACHE, default=DEFAULT_CACHE): bool,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get options flow handler."""
        return GeminiTTSOptionsFlow(config_entry)


class GeminiTTSOptionsFlow(config_entries.OptionsFlow):
    """Handle Gemini TTS options."""

    def __init__(self, config_entry):
        """Initialize."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Handle options update."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.data

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MODEL, default=current.get(CONF_MODEL, DEFAULT_MODEL)
                    ): vol.In(MODELS),
                    vol.Optional(
                        CONF_VOICE, default=current.get(CONF_VOICE, DEFAULT_VOICE)
                    ): vol.In(VOICES),
                    vol.Optional(
                        CONF_LANGUAGE,
                        default=current.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
                    ): vol.In(SUPPORTED_LANGUAGES),
                    vol.Optional(
                        CONF_STYLE_PROMPT,
                        default=current.get(CONF_STYLE_PROMPT, DEFAULT_STYLE_PROMPT),
                    ): str,
                    vol.Optional(
                        CONF_CACHE,
                        default=current.get(CONF_CACHE, DEFAULT_CACHE),
                    ): bool,
                }
            ),
        )
