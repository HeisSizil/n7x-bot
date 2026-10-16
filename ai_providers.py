"""
ai_providers.py - Talks to free AI providers, with automatic fallback.

Order: Gemini -> Groq -> OpenRouter (free models only).
If one fails (error, timeout, rate limit, empty answer), we try the next.
If all fail, AllProvidersFailed is raised and the command says "Try again in a minute."

This file is shared: /ask uses it now, and /summarize (Stage 6) will too.

Usage:
    ai = AIClient()
    await ai.start()
    answer, provider = await ai.ask(system_prompt, user_prompt)
    await ai.close()
"""

import logging

import aiohttp

import config

log = logging.getLogger("n7x.ai")

# --- Settings you can tweak ---
REQUEST_TIMEOUT = 25   # seconds to wait for one provider before trying the next
TEMPERATURE = 0.3      # 0 = very predictable, 1 = more creative. Low = fewer made-up facts.
MAX_TOKENS = 800       # rough cap on answer length

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class ProviderError(Exception):
    """One provider failed. We'll try the next one."""


class AllProvidersFailed(Exception):
    """Every provider failed (or none has a key)."""


class AIClient:
    def __init__(self):
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            trust_env=True,  # respect HTTPS_PROXY if your network uses one
        )

    async def close(self):
        if self.session:
            await self.session.close()

    def providers(self):
        """The providers to try, in order. Ones without a key are left out."""
        chain = []
        if config.GEMINI_API_KEY:
            chain.append(("Gemini", config.GEMINI_MODEL, self._ask_gemini))
        if config.GROQ_API_KEY:
            chain.append(("Groq", config.GROQ_MODEL, self._ask_groq))
        if config.OPENROUTER_API_KEY:
            if config.OPENROUTER_MODEL.endswith(":free"):
                chain.append(("OpenRouter", config.OPENROUTER_MODEL, self._ask_openrouter))
            else:
                # Safety net: never use a paid OpenRouter model by accident.
                log.warning("OPENROUTER_MODEL %r doesn't end in ':free', skipping OpenRouter", config.OPENROUTER_MODEL)
        return chain

    async def ask(self, system_prompt, user_prompt, json_mode=False, max_tokens=None):
        """
        Ask each provider in turn until one answers.
        json_mode=True asks providers that support it to reply with JSON only
        (the caller must still check the JSON - never trust it blindly).
        max_tokens: optional longer answer limit for this one call.
        Returns (answer text, "Provider (model)").
        """
        chain = self.providers()
        if not chain:
            log.error(
                "No usable AI provider. Add GEMINI_API_KEY, GROQ_API_KEY or OPENROUTER_API_KEY to .env "
                "(and OPENROUTER_MODEL must end in ':free')."
            )
            raise AllProvidersFailed()

        for name, model, ask_function in chain:
            try:
                answer = await ask_function(model, system_prompt, user_prompt, json_mode, max_tokens or MAX_TOKENS)
                answer = (answer or "").strip()
                if not answer:
                    raise ProviderError("empty answer")
                log.info("AI: answered by %s (%s)", name, model)
                return answer, f"{name} ({model})"
            except ProviderError as error:
                log.warning("AI: %s failed: %s - trying next provider", name, error)
            except (aiohttp.ClientError, TimeoutError) as error:
                log.warning("AI: %s connection problem: %s - trying next provider", name, str(error) or "timed out")
            except Exception:
                # Unexpected (e.g. the provider changed its reply format). Full details, then move on.
                log.exception("AI: unexpected error from %s - trying next provider", name)

        raise AllProvidersFailed()

    # ------------------------------------------------------------------
    # One function per provider. Each returns the answer text,
    # or raises ProviderError so ask() moves on to the next one.
    # ------------------------------------------------------------------

    async def _post_json(self, url, payload, headers):
        """Send a request and return the JSON reply, or raise ProviderError."""
        async with self.session.post(url, json=payload, headers=headers) as response:
            if response.status == 429:
                raise ProviderError("rate limit / free quota reached (429)")
            if response.status != 200:
                body = (await response.text())[:200]
                raise ProviderError(f"HTTP {response.status}: {body}")
            data = await response.json(content_type=None)
        # Some providers (OpenRouter) report errors inside a 200 reply.
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(f"error in reply: {str(data['error'])[:200]}")
        return data

    async def _ask_gemini(self, model, system_prompt, user_prompt, json_mode=False, max_tokens=MAX_TOKENS):
        generation_config = {"temperature": TEMPERATURE}
        if json_mode:
            generation_config["responseMimeType"] = "application/json"
        data = await self._post_json(
            GEMINI_URL.format(model=model),
            payload={
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": generation_config,
            },
            headers={"x-goog-api-key": config.GEMINI_API_KEY},
        )
        candidates = data.get("candidates") or []
        if not candidates:
            # Usually means Gemini blocked the question for safety reasons.
            raise ProviderError(f"no answer (blocked? {data.get('promptFeedback')})")
        parts = candidates[0].get("content", {}).get("parts", [])
        # Skip any "thinking" parts; keep only the real answer text.
        return "".join(part.get("text", "") for part in parts if not part.get("thought"))

    async def _ask_openai_style(self, url, api_key, model, system_prompt, user_prompt,
                                extra_headers=None, json_mode=False, max_tokens=MAX_TOKENS):
        """Groq and OpenRouter both use the same request format as OpenAI."""
        headers = {"Authorization": f"Bearer {api_key}"}
        headers.update(extra_headers or {})
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = await self._post_json(url, payload=payload, headers=headers)
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("no choices in reply")
        return choices[0].get("message", {}).get("content", "")

    async def _ask_groq(self, model, system_prompt, user_prompt, json_mode=False, max_tokens=MAX_TOKENS):
        return await self._ask_openai_style(GROQ_URL, config.GROQ_API_KEY, model, system_prompt, user_prompt,
                                            json_mode=json_mode, max_tokens=max_tokens)

    async def _ask_openrouter(self, model, system_prompt, user_prompt, json_mode=False, max_tokens=MAX_TOKENS):
        # JSON mode is not sent to OpenRouter: many free models reject it.
        # The prompt still asks for JSON, and the caller checks it.
        return await self._ask_openai_style(
            OPENROUTER_URL, config.OPENROUTER_API_KEY, model, system_prompt, user_prompt,
            # Optional headers OpenRouter uses to show your app name on their site.
            extra_headers={"X-Title": "N7X Discord Bot"}, max_tokens=max_tokens,
        )
