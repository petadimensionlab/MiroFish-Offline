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

    _server_supports = {}

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        use_large: bool = False,
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        # Large stages (ontology / config / report) may be served by a separate
        # server (e.g. the Qwen3.8-27B TensorFold instance on its own port).
        if use_large and not base_url:
            large_url = getattr(Config, 'LLM_LARGE_BASE_URL', '')
            if large_url and large_url != Config.LLM_BASE_URL:
                self.base_url = large_url
                self.api_key = getattr(Config, 'LLM_LARGE_API_KEY', '') or self.api_key
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
        num_ctx: Optional[int] = None,
        extra_headers: Optional[Dict[str, str]] = None
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
            if self._server_supports.get(self.base_url, True):
                kwargs["response_format"] = response_format

        # For Ollama: pass num_ctx via extra_body to prevent prompt truncation
        effective_ctx = num_ctx or self._num_ctx
        if self._is_ollama() and effective_ctx:
            kwargs["extra_body"] = {
                "options": {"num_ctx": effective_ctx}
            }

        try:
            response = self.client.chat.completions.create(
                **kwargs, extra_headers=extra_headers or {}
            )
        except Exception as exc:
            # TensorFold without the xgrammar extra rejects
            # response_format={"type":"json_object"} with HTTP 400 ("structured
            # output needs xgrammar on the server"). Fall back to prompt-only
            # JSON induction once per server, then remember the verdict and
            # drop response_format up front on every later call.
            if "xgrammar" in str(exc):
                self._server_supports[self.base_url] = False
            # TensorFold also rejects prompts whose rendered length plus the
            # requested reply budget exceeds the server ctx (no implicit
            # truncation like Ollama). Retry: first drop the reply budget to
            # 1024; if the prompt itself already exceeds the context window,
            # truncate the newest user message so prompt+1024 fits under ctx.
            ctx400 = "context length" in str(exc) and "400" in str(exc)
            if ctx400:
                from .ctx_pack import parse_ctx_400, pack_messages
                import logging
                log = logging.getLogger(__name__)
                parsed = parse_ctx_400(exc)
                room, server_ctx = parsed if parsed else (0, 8192)
                budget_max = kwargs.get("max_tokens") or 1024
                measured = room
                # Pack the conversation; on each 400 feed the server's own
                # measurement back in so the pack converges (<= 6 tries).
                for _attempt in range(6):
                    msgs2, budget = pack_messages(messages, budget_max, measured, server_ctx)
                    log.info("packing conversation: %d -> %d turns, budget=%d (ctx=%d, measured prompt=%d tok)",
                             len(messages), len(msgs2), budget, server_ctx, measured)
                    kwargs["messages"] = msgs2
                    kwargs["max_tokens"] = budget
                    try:
                        response = self.client.chat.completions.create(
                            **kwargs, extra_headers=extra_headers or {}
                        )
                        break
                    except Exception as exc2:
                        parsed2 = parse_ctx_400(exc2)
                        if not parsed2:
                            raise
                        measured, server_ctx = parsed2
                        if budget <= 256 and measured >= 0.9 * server_ctx:
                            raise
            elif (
                kwargs.get("response_format") is not None
                and "xgrammar" in str(exc)
            ):
                kwargs.pop("response_format")
                response = self.client.chat.completions.create(
                    **kwargs, extra_headers=extra_headers or {}
                )
            else:
                raise
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
        num_ctx: Optional[int] = None,
        extra_headers: Optional[Dict[str, str]] = None
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
            num_ctx=num_ctx,
            extra_headers=extra_headers
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
