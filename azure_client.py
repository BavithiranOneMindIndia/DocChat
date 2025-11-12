# azure_client.py (fixed callable checks + env fixes)
import os
import logging
from configparser import ConfigParser
from typing import List
import backoff
from requests.exceptions import RequestException

logger = logging.getLogger("prodocchat.azure")
logger.setLevel(logging.INFO)

# load config (attempt utils loader if present)
try:
    from utils import load_or_copy_config
    cfg = load_or_copy_config()
except Exception:
    cfg = ConfigParser()
    cfg.read("config.ini")

AZURE_ENDPOINT = cfg.get("azure", "endpoint", fallback=None)
API_KEY = cfg.get("azure", "api_key", fallback=None)
EMBED_DEPLOY = cfg.get("azure", "deployment_embed", fallback=None)
CHAT_DEPLOY = cfg.get("azure", "deployment_chat", fallback=None)
API_VERSION = cfg.get("azure", "api_version", fallback="2024-12-01-preview")

# Ensure env vars the new openai lib expects are present BEFORE importing openai
if AZURE_ENDPOINT:
    os.environ.setdefault("AZURE_OPENAI_ENDPOINT", AZURE_ENDPOINT)
if API_KEY:
    os.environ.setdefault("OPENAI_API_KEY", API_KEY)

# import openai after env vars
import openai

# attempt to set module attrs (harmless in many versions)
try:
    openai.api_type = "azure"
    openai.api_key = API_KEY
    openai.api_base = AZURE_ENDPOINT
    openai.api_version = API_VERSION
except Exception:
    pass

# exceptions compatibility
try:
    OpenAIError = openai.error.OpenAIError
    APIError = openai.error.APIError
    Timeout = openai.error.Timeout
except Exception:
    class OpenAIError(Exception): pass
    class APIError(OpenAIError): pass
    class Timeout(OpenAIError): pass

def _try_calls(calls):
    last_exc = None
    for f in calls:
        try:
            return f()
        except Exception as e:
            last_exc = e
            logger.debug("Call variant failed: %s", repr(e))
    raise last_exc

@backoff.on_exception(backoff.expo, (RequestException, Timeout, APIError, OpenAIError), max_tries=5, jitter=None)
def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []

    calls = []

    # old-style class
    if hasattr(openai, "Embeddings"):
        emb_cls = getattr(openai, "Embeddings", None)
        if emb_cls and callable(getattr(emb_cls, "create", None)):
            calls.append(lambda: emb_cls.create(engine=EMBED_DEPLOY, input=texts))

    # module-level 'embeddings' proxy
    embeddings_proxy = getattr(openai, "embeddings", None)
    if embeddings_proxy and callable(getattr(embeddings_proxy, "create", None)):
        # some variants expect model=, some expect engine/deployment_id
        calls.append(lambda: embeddings_proxy.create(model=EMBED_DEPLOY, input=texts))

    # new OpenAI client class
    OpenAIClient = getattr(openai, "OpenAI", None)
    if OpenAIClient:
        def _client_emb():
            # pass azure_endpoint explicitly
            client = OpenAIClient(api_key=API_KEY, azure_endpoint=AZURE_ENDPOINT, api_version=API_VERSION)
            # try model then deployment_id
            try:
                return client.embeddings.create(model=EMBED_DEPLOY, input=texts)
            except TypeError:
                return client.embeddings.create(deployment_id=EMBED_DEPLOY, input=texts)
        calls.append(_client_emb)

    # execute
    resp = _try_calls(calls)

    # normalize response
    if isinstance(resp, dict) and "data" in resp:
        return [item["embedding"] for item in resp["data"]]
    data = getattr(resp, "data", None)
    if data:
        out = []
        for item in data:
            if isinstance(item, dict) and "embedding" in item:
                out.append(item["embedding"])
            else:
                emb = getattr(item, "embedding", None)
                if emb:
                    out.append(list(emb))
        if out:
            return out
    if isinstance(resp, list) and resp and isinstance(resp[0], list):
        return resp

    raise RuntimeError(f"Could not parse embeddings response from OpenAI client: {type(resp)}")


@backoff.on_exception(backoff.expo, (RequestException, Timeout, APIError, OpenAIError), max_tries=5, jitter=None)
def chat_with_context(system_prompt: str, user_prompt: str, context: str, temperature=0.0, max_tokens=1024) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion:\n{user_prompt}"},
    ]

    calls = []

    # old ChatCompletion
    ChatCompletion = getattr(openai, "ChatCompletion", None)
    if ChatCompletion and callable(getattr(ChatCompletion, "create", None)):
        calls.append(lambda: ChatCompletion.create(engine=CHAT_DEPLOY, messages=messages, temperature=temperature, max_tokens=max_tokens))

    # module-level new chat
    chat_mod = getattr(openai, "chat", None)
    if chat_mod:
        comp = getattr(chat_mod, "completions", None)
        if comp and callable(getattr(comp, "create", None)):
            calls.append(lambda: comp.create(model=CHAT_DEPLOY, messages=messages, temperature=temperature, max_tokens=max_tokens))

    # OpenAI client
    OpenAIClient = getattr(openai, "OpenAI", None)
    if OpenAIClient:
        def _client_chat():
            client = OpenAIClient(api_key=API_KEY, azure_endpoint=AZURE_ENDPOINT, api_version=API_VERSION)
            try:
                return client.chat.completions.create(model=CHAT_DEPLOY, messages=messages, temperature=temperature, max_tokens=max_tokens)
            except TypeError:
                return client.chat.completions.create(deployment_id=CHAT_DEPLOY, messages=messages, temperature=temperature, max_tokens=max_tokens)
        calls.append(_client_chat)

    resp = _try_calls(calls)

    # normalize response
    try:
        if isinstance(resp, dict):
            choices = resp.get("choices")
            if choices and isinstance(choices, list):
                first = choices[0]
                if isinstance(first, dict):
                    msg = first.get("message") or first.get("text") or first.get("delta")
                    if isinstance(msg, dict):
                        return msg.get("content", "")
                    elif isinstance(msg, str):
                        return msg
        try:
            return resp.choices[0].message.content
        except Exception:
            pass
    except Exception as e:
        logger.debug("Failed to normalize chat response: %s", e)

    # fallback checks
    for attempt in (
        lambda r: r["choices"][0]["message"]["content"] if isinstance(r, dict) and "choices" in r else None,
        lambda r: getattr(getattr(r, "choices", [None])[0], "message", None) and getattr(r.choices[0].message, "content", None),
        lambda r: getattr(r, "text", None),
        lambda r: getattr(r, "output_text", None),
    ):
        try:
            v = attempt(resp)
            if v:
                return v
        except Exception:
            pass

    raise RuntimeError(f"Could not parse chat response from OpenAI client: {type(resp)}")
