import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN, CONF_OPENAI_API_KEY, CONF_TAVILY_API_KEY


class JaneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            # Validate OpenAI API key
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
                vol.Optional(CONF_TAVILY_API_KEY): str,
            }),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return JaneOptionsFlow(config_entry)


class JaneOptionsFlow(config_entries.OptionsFlow):
    """Options flow for adding/changing Tavily key after setup."""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            # Merge into config entry data
            new_data = {**self._config_entry.data, **user_input}
            # Remove empty keys
            new_data = {k: v for k, v in new_data.items() if v}
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            return self.async_create_entry(title="", data={})

        current_tavily = self._config_entry.data.get(CONF_TAVILY_API_KEY, "")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_TAVILY_API_KEY, default=current_tavily): str,
            }),
        )
