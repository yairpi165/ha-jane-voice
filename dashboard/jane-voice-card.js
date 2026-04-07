class JaneVoiceCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._recording = false;
    this._processing = false;
    this._mediaRecorder = null;
    this._chunks = [];
    this._audioCtx = null;
  }

  setConfig(config) {
    this._config = config;
    this._ingressUrl = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
  }

  getCardSize() {
    return 3;
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          direction: rtl;
        }
        ha-card {
          padding: 20px 16px;
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 12px;
        }
        .title {
          font-size: 1.1rem;
          font-weight: 500;
          color: var(--primary-text-color);
        }
        #mic-btn {
          width: 72px;
          height: 72px;
          border-radius: 50%;
          border: none;
          background: var(--primary-color, #03a9f4);
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: all 0.2s ease;
          -webkit-tap-highlight-color: transparent;
          outline: none;
        }
        #mic-btn:active {
          transform: scale(0.93);
        }
        #mic-btn svg {
          width: 32px;
          height: 32px;
          fill: white;
        }
        #mic-btn.recording {
          background: #ff453a;
          animation: pulse 1.5s ease-in-out infinite;
        }
        #mic-btn.processing {
          background: #ff9f0a;
          pointer-events: none;
          animation: none;
        }
        @keyframes pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(255,69,58,0.4); }
          50% { box-shadow: 0 0 0 14px rgba(255,69,58,0); }
        }
        #status {
          font-size: 0.85rem;
          color: var(--secondary-text-color);
          min-height: 1.2em;
        }
        #messages {
          width: 100%;
          display: flex;
          flex-direction: column;
          gap: 6px;
          max-height: 150px;
          overflow-y: auto;
        }
        .msg {
          padding: 6px 10px;
          border-radius: 10px;
          font-size: 0.85rem;
          line-height: 1.4;
          max-width: 90%;
          word-wrap: break-word;
        }
        .msg.user {
          background: var(--secondary-background-color, #e0e0e0);
          color: var(--primary-text-color);
          align-self: flex-end;
        }
        .msg.jane {
          background: color-mix(in srgb, var(--primary-color, #03a9f4) 15%, transparent);
          color: var(--primary-color, #03a9f4);
          align-self: flex-start;
        }
        .msg.error {
          background: rgba(255,69,58,0.1);
          color: #ff453a;
          align-self: center;
          text-align: center;
        }
      </style>
      <ha-card>
        <div class="title">ג'יין</div>
        <button id="mic-btn">
          <svg viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5zm6 6c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
        </button>
        <div id="status"></div>
        <div id="messages"></div>
      </ha-card>
    `;

    this.shadowRoot.getElementById('mic-btn').addEventListener('click', () => this._toggle());
    this._setStatus('');
  }

  _setStatus(text) {
    const el = this.shadowRoot.getElementById('status');
    if (el) el.textContent = text;
  }

  _addMsg(text, type) {
    const container = this.shadowRoot.getElementById('messages');
    if (!container) return;
    const div = document.createElement('div');
    div.className = 'msg ' + type;
    div.textContent = text;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  _getSupportedMimeType() {
    for (const type of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']) {
      if (MediaRecorder.isTypeSupported(type)) return type;
    }
    return '';
  }

  _getExt(mime) {
    if (mime.includes('mp4')) return '.mp4';
    if (mime.includes('ogg')) return '.ogg';
    return '.webm';
  }

  async _toggle() {
    if (this._recording) {
      this._stop();
    } else {
      await this._start();
    }
  }

  async _start() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = this._getSupportedMimeType();
      this._mediaRecorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
      this._chunks = [];

      this._mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) this._chunks.push(e.data);
      };

      this._mediaRecorder.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        const ext = this._getExt(this._mediaRecorder.mimeType);
        const blob = new Blob(this._chunks, { type: this._mediaRecorder.mimeType });
        this._send(blob, ext);
      };

      if (!this._audioCtx) {
        this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      }

      this._mediaRecorder.start();
      this._recording = true;
      this.shadowRoot.getElementById('mic-btn').classList.add('recording');
      this._setStatus('מקשיבה...');
    } catch {
      this._addMsg('אין גישה למיקרופון', 'error');
    }
  }

  _stop() {
    if (this._mediaRecorder && this._mediaRecorder.state !== 'inactive') {
      this._mediaRecorder.stop();
    }
    this._recording = false;
    const btn = this.shadowRoot.getElementById('mic-btn');
    btn.classList.remove('recording');
    btn.classList.add('processing');
    this._setStatus('מעבדת...');
  }

  async _getApiUrl() {
    if (this._resolvedUrl) return this._resolvedUrl;
    // 1. Use ingress_url from config if provided
    if (this._config.ingress_url) {
      this._resolvedUrl = this._config.ingress_url.replace(/\/$/, '');
      return this._resolvedUrl;
    }
    // 2. Try to discover ingress dynamically
    try {
      const slug = this._config.addon_slug || 'local_jane';
      const info = await this._hass.callApi('GET', 'hassio/addons/' + slug + '/info');
      const url = (info.data && info.data.ingress_url) || info.ingress_url;
      if (url) {
        this._resolvedUrl = url.replace(/\/$/, '');
        return this._resolvedUrl;
      }
    } catch {}
    // 3. Fallback to direct IP
    if (this._config.api_url) {
      this._resolvedUrl = this._config.api_url;
      return this._resolvedUrl;
    }
    return '';
  }

  async _send(blob, ext) {
    const btn = this.shadowRoot.getElementById('mic-btn');
    const form = new FormData();
    form.append('audio', blob, 'recording' + ext);
    const user = (this._hass && this._hass.user && this._hass.user.name) || this._config.user || 'default';
    form.append('user', user);

    const apiUrl = await this._getApiUrl();
    try {
      const res = await fetch(apiUrl + '/api/voice', {
        method: 'POST',
        body: form,
      });
      const data = await res.json();

      if (data.user_text) this._addMsg(data.user_text, 'user');
      if (data.response_text) this._addMsg(data.response_text, 'jane');
      if (data.audio) await this._play(data.audio);
    } catch {
      this._addMsg('שגיאה בחיבור לג\'יין', 'error');
    }

    btn.classList.remove('processing');
    this._setStatus('');
  }

  async _play(b64) {
    try {
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const buffer = await this._audioCtx.decodeAudioData(bytes.buffer.slice(0));
      const source = this._audioCtx.createBufferSource();
      source.buffer = buffer;
      source.connect(this._audioCtx.destination);
      source.start(0);
      await new Promise(r => { source.onended = r; });
    } catch {
      const audio = new Audio('data:audio/mpeg;base64,' + b64);
      await audio.play();
    }
  }
}

customElements.define('jane-voice-card', JaneVoiceCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'jane-voice-card',
  name: 'Jane Voice',
  description: 'Voice control for Jane smart home assistant',
});
