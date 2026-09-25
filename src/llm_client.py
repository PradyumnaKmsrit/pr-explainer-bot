"""LLM client wrapping Google Gemini and Groq API calls with robust JSON validation."""

import os
import json
import re
from typing import Type, TypeVar, Optional, Any, Dict, List
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMAPIError(Exception):
    """Raised when an LLM API call fails."""
    pass


class LLMValidationError(Exception):
    """Raised when the LLM output fails schema validation."""
    pass


def clean_json_text(raw_text: str) -> str:
    """Strips markdown code blocks, backticks, and extraneous text around JSON."""
    cleaned = raw_text.strip()

    # Match code blocks ```json ... ``` or ``` ... ```
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, cleaned, re.MULTILINE)
    if match:
        cleaned = match.group(1).strip()

    # Extract JSON object if there's leading or trailing commentary
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        cleaned = cleaned[first_brace : last_brace + 1]

    return cleaned


class LLMClient:
    """Unified client supporting Gemini and Groq with structured Pydantic response parsing."""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.2
    ):
        self.provider = (provider or os.getenv("LLM_PROVIDER", "gemini")).lower()
        self.model = model
        self.api_key = api_key
        self.temperature = temperature

        if self.provider == "gemini":
            self.model = self.model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
            self.api_key = self.api_key or os.getenv("GEMINI_API_KEY")
        elif self.provider == "groq":
            self.model = self.model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
            self.api_key = self.api_key or os.getenv("GROQ_API_KEY")
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}. Use 'gemini' or 'groq'.")

    def call_raw(self, system_prompt: str, user_prompt: str) -> str:
        """Invokes the selected LLM provider and returns the raw response string."""
        if self.provider == "gemini":
            return self._call_gemini(system_prompt, user_prompt)
        elif self.provider == "groq":
            return self._call_groq(system_prompt, user_prompt)
        else:
            raise ValueError(f"Unknown provider {self.provider}")

    def call_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Type[T]
    ) -> T:
        """
        Invokes LLM and validates output against the provided Pydantic model.
        Retries once if parsing or validation fails.
        """
        raw_output = self.call_raw(system_prompt, user_prompt)
        cleaned = clean_json_text(raw_output)

        try:
            parsed_dict = json.loads(cleaned)
            return schema.model_validate(parsed_dict)
        except (json.JSONDecodeError, ValidationError) as e:
            # Retry with repair hint
            repair_prompt = (
                f"{user_prompt}\n\n"
                f"CRITICAL FIX: Your previous response was invalid JSON or failed schema validation with error:\n"
                f"{str(e)}\n"
                f"Please output ONLY raw valid JSON conforming strictly to the requested schema."
            )
            retry_raw = self.call_raw(system_prompt, repair_prompt)
            retry_cleaned = clean_json_text(retry_raw)
            try:
                retry_dict = json.loads(retry_cleaned)
                return schema.model_validate(retry_dict)
            except Exception as final_err:
                raise LLMValidationError(
                    f"Failed to validate LLM output against {schema.__name__}.\n"
                    f"Error: {final_err}\n"
                    f"Raw output:\n{retry_raw}"
                ) from final_err

    def _get_active_gemini_models(self) -> List[str]:
        """Fetches list of active text models supporting generateContent directly from Google API."""
        import requests
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}"
            resp = requests.get(url, timeout=10)
            data = resp.json()
            models = []
            for m in data.get("models", []):
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    name = m.get("name", "").replace("models/", "")
                    # Filter out non-text/specialized models
                    if any(bad in name.lower() for bad in ["tts", "image", "transcribe", "clip", "robotics", "custom", "compute"]):
                        continue
                    models.append(name)
            return models
        except Exception:
            return []

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        """
        Call Gemini API with automatic retry and live model fallback.
        """
        import time

        if not self.api_key:
            raise LLMAPIError(
                "GEMINI_API_KEY environment variable is not set. "
                "Please get an API key from https://aistudio.google.com/ and set it."
            )

        # Build candidate list starting with preferred model
        primary = self.model.replace("models/", "")
        candidate_models = [primary]

        # Dynamically discover all valid text models available for this API key
        live_models = self._get_active_gemini_models()
        for m in live_models:
            if m not in candidate_models and ("flash" in m or "lite" in m):
                candidate_models.append(m)

        last_error = None
        for model_name in candidate_models:
            for attempt in range(2):
                try:
                    return self._call_gemini_rest(model_name, system_prompt, user_prompt)
                except Exception as e:
                    last_error = e
                    err_str = str(e).lower()
                    if any(term in err_str for term in ["high demand", "unavailable", "503", "429"]):
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    # For other errors (like model quota or not supported), break to try next model
                    break

        raise LLMAPIError(f"Gemini request failed across candidate models: {last_error}")

    def _call_gemini_rest(self, model_name: str, system_prompt: str, user_prompt: str) -> str:
        """Direct REST call to Gemini generateContent endpoint."""
        import requests

        clean_name = model_name.replace("models/", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={self.api_key}"
        payload: Dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "response_mime_type": "application/json",
            },
        }

        try:
            response = requests.post(url, json=payload, timeout=60)
            data = response.json()
            if "error" in data:
                raise LLMAPIError(f"Gemini API error: {data['error'].get('message')}")
            candidates = data.get("candidates", [])
            if candidates and "content" in candidates[0]:
                parts = candidates[0]["content"].get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            raise LLMAPIError(f"Unexpected Gemini REST response structure: {data}")
        except LLMAPIError:
            raise
        except Exception as e:
            raise LLMAPIError(f"Gemini REST request failed: {e}") from e

    def _call_groq(self, system_prompt: str, user_prompt: str) -> str:
        """Call Groq API using groq-python SDK or REST API."""
        if not self.api_key:
            raise LLMAPIError(
                "GROQ_API_KEY environment variable is not set. "
                "Please get an API key from https://console.groq.com/ and set it."
            )

        try:
            from groq import Groq

            client = Groq(api_key=self.api_key)
            completion = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            content = completion.choices[0].message.content
            if content:
                return content
            raise LLMAPIError("Empty response received from Groq.")
        except ImportError:
            return self._call_groq_rest(system_prompt, user_prompt)
        except Exception as e:
            raise LLMAPIError(f"Groq API error: {e}") from e

    def _call_groq_rest(self, system_prompt: str, user_prompt: str) -> str:
        """Direct REST fallback to Groq chat completions."""
        import requests

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            data = resp.json()
            if "error" in data:
                raise LLMAPIError(f"Groq API error: {data['error'].get('message')}")
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            raise LLMAPIError(f"Groq REST request failed: {e}") from e
