import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY

from .const import DOMAIN, CONF_OPENAI_API_KEY, CONF_TTS_VOICE, DEFAULT_TTS_VOICE


class JaneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            # Validate API key by making a test call
            from openai import OpenAI

            try:
                client = OpenAI(api_key=user_input[CONF_OPENAI_API_KEY])
                await self.hass.async_add_executor_job(
                    lambda: client.models.list()
                )
            except Exception:
                errors["base"] = "invalid_api_key"
            else:
                return self.async_create_entry(
                    title="Jane Voice Assistant",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_OPENAI_API_KEY): str,
                vol.Optional(CONF_TTS_VOICE, default=DEFAULT_TTS_VOICE): str,
            }),
            errors=errors,
        )
