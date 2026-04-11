import voluptuous as vol
from homeassistant import config_entries

from .const import CONF_FIREBASE_KEY_PATH, CONF_GEMINI_API_KEY, DOMAIN


class JaneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            # Validate Gemini API key
            from google import genai

            try:
                client = genai.Client(api_key=user_input[CONF_GEMINI_API_KEY])
                await self.hass.async_add_executor_job(
                    lambda: client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents="test",
                    )
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
                vol.Required(CONF_GEMINI_API_KEY): str,
            }),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return JaneOptionsFlow(config_entry)


class JaneOptionsFlow(config_entries.OptionsFlow):
    """Options flow for adding/changing keys after setup."""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            new_data = {**self._config_entry.data, **user_input}
            new_data = {k: v for k, v in new_data.items() if v}
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            return self.async_create_entry(title="", data={})

        current_firebase = self._config_entry.data.get(CONF_FIREBASE_KEY_PATH, "")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_FIREBASE_KEY_PATH, default=current_firebase): str,
            }),
        )
