"""Qwen chat client. Server addresses and credentials are runtime configuration."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse


class ModelError(RuntimeError):
    pass


class QwenClient:
    def __init__(self, endpoint, model, *, api_key=None, timeout=120, max_tokens=2000,
                 json_mode=False, opener=None):
        parsed = urlparse(endpoint)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('QWEN_ENDPOINT must be a full HTTP(S) chat/completions URL')
        if not model or timeout <= 0 or max_tokens <= 0:
            raise ValueError('model and positive timeout/max_tokens are required')
        self.endpoint, self.model = endpoint, model
        self.api_key, self.timeout, self.max_tokens = api_key, timeout, max_tokens
        self.json_mode, self.opener = json_mode, opener or urllib.request.urlopen

    def complete(self, messages):
        payload = {'model': self.model, 'messages': messages, 'temperature': 0,
                   'stream': False, 'max_tokens': self.max_tokens}
        if self.json_mode:
            payload['response_format'] = {'type': 'json_object'}
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = 'Bearer ' + self.api_key
        request = urllib.request.Request(self.endpoint, json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ModelError(f'Qwen HTTP error: {exc.code}') from exc
        except (OSError, ValueError) as exc:
            raise ModelError('Qwen connection or response decoding failed') from exc
        try:
            choice = result['choices'][0]
            if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict):
                raise ModelError('Qwen returned an invalid chat choice/message')
            if choice.get('finish_reason') == 'length':
                raise ModelError('Qwen output reached the configured output limit')
            content = choice['message']['content']
            if not isinstance(content, str) or not content.strip():
                raise ModelError('Qwen returned no text content')
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError('Qwen response does not contain a chat message') from exc
        return content
