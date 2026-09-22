"""
LLM Client Wrapper
Unified OpenAI format API calls
Supports Ollama num_ctx parameter to prevent prompt truncation
"""

import json
import re
from typing import Optional, Dict, Any, List
from openai import OpenAI

from ..config import Config


def reasoning_kwargs() -> Dict[str, Any]:
    """Return ``{"reasoning_effort": <value>}`` when configured, else ``{}``.

    Used to disable "thinking" on reasoning models (e.g. qwen3.5) whose
    OpenAI-compatible response can otherwise return empty ``content`` because the
    whole token budget was consumed by the separate ``reasoning`` field.
    """
    effort = getattr(Config, 'LLM_REASONING_EFFORT', '')
    return {"reasoning_effort": effort} if effort else {}


class LLMClient:
    """LLM Client"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 300.0,
        use_large: bool = False,
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        if model:
            self.model = model
        elif use_large and getattr(Config, 'LLM_MODEL_NAME_LARGE', ''):
            self.model = Config.LLM_MODEL_NAME_LARGE
        else:
            self.model = Config.LLM_MODEL_NAME

        if not self.api_key:
            raise ValueError("LLM_API_KEY not configured")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
        )

        # Ollama context window size — prevents prompt truncation.
        # Read from env OLLAMA_NUM_CTX (Config.OLLAMA_NUM_CTX, default 4096).
        self._num_ctx = Config.OLLAMA_NUM_CTX

    def _is_ollama(self) -> bool:
        """Check if we're talking to an Ollama server."""
        return '11434' in (self.base_url or '')

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        num_ctx: Optional[int] = None
    ) -> str:
        """
        Send chat request

        Args:
            messages: Message list
            temperature: Temperature parameter
            max_tokens: Max token count
            response_format: Response format (e.g., JSON mode)
            num_ctx: Override the Ollama context window for this call

        Returns:
            Model response text
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Disable reasoning/thinking when configured (see reasoning_kwargs).
        kwargs.update(reasoning_kwargs())

        if response_format:
            kwargs["response_format"] = response_format

        # For Ollama: pass num_ctx via extra_body to prevent prompt truncation
        effective_ctx = num_ctx or self._num_ctx
        if self._is_ollama() and effective_ctx:
            kwargs["extra_body"] = {
                "options": {"num_ctx": effective_ctx}
            }

        response = self.client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        content = message.content
        if not content:
            # Some OpenAI-compatible servers (e.g. oMLX) answer a prompt that
            # describes a tool-call protocol with native `tool_calls` and
            # `content: null`. Convert those back into the textual <tool_call>
            # form that report_agent._parse_tool_calls() expects, so the ReACT
            # loop keeps working (and callers never see None).
            parts = []
            for tc in getattr(message, 'tool_calls', None) or []:
                fn = getattr(tc, 'function', None)
                if not fn:
                    continue
                try:
                    params = json.loads(fn.arguments or '{}')
                except (json.JSONDecodeError, TypeError):
                    params = {}
                parts.append(
                    '<tool_call>'
                    + json.dumps({'name': fn.name, 'parameters': params}, ensure_ascii=False)
                    + '</tool_call>'
                )
            content = ''.join(parts)
        # Some models (like MiniMax M2.5) include <think>thinking content in response, need to remove
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        num_ctx: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send chat request and return JSON

        Args:
            messages: Message list
            temperature: Temperature parameter
            max_tokens: Max token count
            num_ctx: Override the Ollama context window for this call

        Returns:
            Parsed JSON object
        """
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            num_ctx=num_ctx
        )
        # Clean markdown code block markers
        cleaned_response = response.strip()
        cleaned_response = re.sub(r'^```(?:json)?\s*\n?', '', cleaned_response, flags=re.IGNORECASE)
        cleaned_response = re.sub(r'\n?```\s*$', '', cleaned_response)
        cleaned_response = cleaned_response.strip()

        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON format from LLM: {cleaned_response}")
