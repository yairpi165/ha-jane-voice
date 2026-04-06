#!/usr/bin/with-contenv bashio

# Read config from HA add-on options
export OPENAI_API_KEY=$(bashio::config 'openai_api_key')
export TTS_VOICE=$(bashio::config 'tts_voice')
# SUPERVISOR_TOKEN is injected automatically by HA

bashio::log.info "Starting Jane Voice API on port 5050..."

cd /app
python3 web_api.py
