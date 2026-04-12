import logging

import voluptuous as vol
from homeassistant import config_entries

from .const import (
    CONF_FIREBASE_KEY_PATH,
    CONF_GEMINI_API_KEY,
    CONF_PG_DATABASE,
    CONF_PG_HOST,
    CONF_PG_PASSWORD,
    CONF_PG_PORT,
    CONF_PG_USER,
    CONF_REDIS_PASSWORD,
    CONF_REDIS_PORT,
    DEFAULT_REDIS_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class JaneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
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
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GEMINI_API_KEY): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return JaneOptionsFlow(config_entry)


class JaneOptionsFlow(config_entries.OptionsFlow):
    """Options flow for configuring Firebase backup and PostgreSQL."""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        errors = {}

        if user_input is not None:
            # Validate PG connection if host is provided
            pg_host = user_input.get(CONF_PG_HOST, "")
            if pg_host:
                try:
                    import asyncpg

                    conn = await asyncpg.connect(
                        host=pg_host,
                        port=int(user_input.get(CONF_PG_PORT, 5432)),
                        database=user_input.get(CONF_PG_DATABASE, "jane"),
                        user=user_input.get(CONF_PG_USER, "postgres"),
                        password=user_input.get(CONF_PG_PASSWORD, ""),
                        timeout=5,
                        ssl="disable",
                    )
                    await conn.close()
                except Exception as e:
                    errors["pg_host"] = "pg_connection_failed"
                    _LOGGER.warning("PG connection test failed: %s", e)

                # Validate Redis connection (same host as PG)
                if not errors:
                    try:
                        import redis.asyncio as aioredis

                        redis_client = aioredis.Redis(
                            host=pg_host,
                            port=int(user_input.get(CONF_REDIS_PORT, DEFAULT_REDIS_PORT)),
                            password=user_input.get(CONF_REDIS_PASSWORD) or None,
                            socket_connect_timeout=5,
                        )
                        await redis_client.ping()
                        await redis_client.aclose()
                    except Exception as e:
                        errors["redis_port"] = "redis_connection_failed"
                        _LOGGER.warning("Redis connection test failed: %s", e)

            if not errors:
                new_data = {**self._config_entry.data, **user_input}
                new_data = {k: v for k, v in new_data.items() if v}
                self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
                return self.async_create_entry(title="", data={})

        data = self._config_entry.data

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_FIREBASE_KEY_PATH,
                        default=data.get(CONF_FIREBASE_KEY_PATH, ""),
                    ): str,
                    vol.Optional(
                        CONF_PG_HOST,
                        default=data.get(CONF_PG_HOST, ""),
                    ): str,
                    vol.Optional(
                        CONF_PG_PORT,
                        default=int(data.get(CONF_PG_PORT, 5432)),
                    ): vol.Coerce(int),
                    vol.Optional(
                        CONF_PG_DATABASE,
                        default=data.get(CONF_PG_DATABASE, "jane"),
                    ): str,
                    vol.Optional(
                        CONF_PG_USER,
                        default=data.get(CONF_PG_USER, "postgres"),
                    ): str,
                    vol.Optional(
                        CONF_PG_PASSWORD,
                        default=data.get(CONF_PG_PASSWORD, ""),
                    ): str,
                    vol.Optional(
                        CONF_REDIS_PORT,
                        default=int(data.get(CONF_REDIS_PORT, DEFAULT_REDIS_PORT)),
                    ): vol.Coerce(int),
                    vol.Optional(
                        CONF_REDIS_PASSWORD,
                        default=data.get(CONF_REDIS_PASSWORD, ""),
                    ): str,
                }
            ),
            errors=errors,
        )
