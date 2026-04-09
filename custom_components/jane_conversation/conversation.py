"""Jane Conversation Entity — integrates with HA Assist pipeline."""

import logging
import uuid
from openai import OpenAI

from homeassistant.components import conversation
from homeassistant.components.conversation import ConversationEntity, ConversationResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_OPENAI_API_KEY, CONF_TAVILY_API_KEY, WHISPER_HALLUCINATIONS
from .brain import think
from .memory import append_action, process_memory

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Jane conversation entity from config entry."""
    async_add_entities([JaneConversationEntity(config_entry)])


class JaneConversationEntity(ConversationEntity):
    """Jane conversation agent for HA Assist pipeline."""

    _attr_has_entity_name = True
    _attr_name = "Jane"
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, config_entry):
        """Initialize."""
        self._config_entry = config_entry
        self._client = OpenAI(api_key=config_entry.data[CONF_OPENAI_API_KEY])
        self._attr_unique_id = config_entry.entry_id
        self._sessions: dict[str, list[dict]] = {}

    @property
    def tavily_api_key(self) -> str | None:
        """Get Tavily key from config (supports options flow updates)."""
        return self._config_entry.data.get(CONF_TAVILY_API_KEY)

    @property
    def supported_languages(self) -> list[str]:
        return ["he", "en"]

    def _get_history(self, conversation_id: str | None) -> tuple[str, list[dict]]:
        """Get or create conversation history for a session."""
        if not conversation_id:
            conversation_id = str(uuid.uuid4())
        if conversation_id not in self._sessions:
            self._sessions[conversation_id] = []
        return conversation_id, self._sessions[conversation_id]

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> ConversationResult:
        """Process a voice/text command through Jane's brain."""
        user_text = user_input.text
        user_name = user_input.context.user_id or "default"

        # Resolve HA user name from user_id
        if user_input.context.user_id:
            user = await self.hass.auth.async_get_user(user_input.context.user_id)
            if user:
                user_name = user.name or user_name

        # Get conversation history
        conversation_id, history = self._get_history(user_input.conversation_id)

        _LOGGER.info("Jane received: %s (user: %s)", user_text, user_name)

        # Filter Whisper hallucinations (phantom phrases from silence/noise)
        if user_text.strip().lower() in WHISPER_HALLUCINATIONS:
            _LOGGER.info("Ignoring Whisper hallucination: %s", user_text)
            response = intent.IntentResponse(language=user_input.language or "he")
            response.async_set_speech("")
            return ConversationResult(
                conversation_id=conversation_id,
                response=response,
            )

        # Think with tool calling
        response_text = await think(
            self._client,
            user_text,
            user_name,
            self.hass,
            history,
            self.tavily_api_key,
        )

        # Update conversation history (only user text + final response, not tool calls)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": response_text})

        # Keep history manageable (last 10 turns = 20 messages)
        if len(history) > 20:
            self._sessions[conversation_id] = history[-20:]

        _LOGGER.info("Jane responds: %s", response_text)

        # Log action in background
        await self.hass.async_add_executor_job(
            append_action, user_name, response_text
        )

        # Memory extraction in background
        silent = any(p in user_text for p in ["אל תזכרי", "אל תזכור", "מצב שקט"])
        if not silent:
            self.hass.async_add_executor_job(
                process_memory, self._client, user_name, user_text, response_text, "tool"
            )

        # Return response for TTS
        response = intent.IntentResponse(language=user_input.language or "he")
        response.async_set_speech(response_text)

        return ConversationResult(
            conversation_id=conversation_id,
            response=response,
            continue_conversation=response_text.strip().endswith("?"),
        )
