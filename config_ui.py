# config_ui.py
import os
import time
from configparser import ConfigParser
from pathlib import Path
from typing import List, Tuple, Optional

import streamlit as st

REQUIRED_FIELDS = [
    ("azure", "endpoint"),
    ("azure", "api_key"),
    ("azure", "deployment_embed"),
    ("azure", "deployment_chat"),
    ("azure", "api_version"),
]


def _config_file_path(user_dir: str) -> str:
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, "config.ini")


def _is_missing(cfg: ConfigParser) -> bool:
    for section, opt in REQUIRED_FIELDS:
        if not cfg.has_section(section) or not cfg.get(section, opt, fallback="").strip():
            return True
    return False


def _missing_list(cfg: ConfigParser) -> List[Tuple[str, str]]:
    missing = []
    for section, opt in REQUIRED_FIELDS:
        if not cfg.has_section(section) or not cfg.get(section, opt, fallback="").strip():
            missing.append((section, opt))
    return missing


def _write_config_to_user_dir(cfg: ConfigParser, user_dir: str) -> None:
    path = _config_file_path(user_dir)
    with open(path, "w", encoding="utf-8") as f:
        cfg.write(f)


def _pref(cfg: ConfigParser, sec: str, opt: str, default: str = "") -> str:
    try:
        return cfg.get(sec, opt, fallback=default)
    except Exception:
        return default


def _save_from_inputs(cfg: ConfigParser, user_dir: str, endpoint: str, api_key: str, api_version: str, deployment_embed: str, deployment_chat: str, app_name: str) -> bool:
    """
    Save values into cfg and persist to user_dir. Returns True on success.
    """
    if not cfg.has_section("azure"):
        cfg.add_section("azure")
    cfg.set("azure", "endpoint", endpoint.strip())
    cfg.set("azure", "api_key", api_key.strip())
    cfg.set("azure", "api_version", api_version.strip())
    cfg.set("azure", "deployment_embed", deployment_embed.strip())
    cfg.set("azure", "deployment_chat", deployment_chat.strip())

    if not cfg.has_section("app"):
        cfg.add_section("app")
    cfg.set("app", "app_name", app_name.strip() or "ProDocChat")

    try:
        _write_config_to_user_dir(cfg, user_dir)
        return True
    except Exception:
        return False


def show_settings_if_missing(cfg: ConfigParser, user_dir: str) -> None:
    """
    If any required Azure field is missing or empty, render the settings UI and stop the app.
    After saving, write config to <user_dir>/config.ini and set query params to force a rerun.
    """
    if not _is_missing(cfg):
        return

    st.title("ProDocChat — Configuration required")
    st.warning("Azure OpenAI configuration is missing or incomplete. Fill these settings to continue.")
    st.markdown("Values are saved locally to your app data directory and will not be committed to source control.")

    with st.form("config_form_required"):
        st.markdown(
            """
            **How to get these values**
            - `endpoint`: e.g. `https://<your-resource>.openai.azure.com/`
            - `api_key`: key from Azure portal
            - `api_version`: e.g. `2024-12-01-preview`
            - `deployment_embed`: e.g. `text-embedding-3-large`
            - `deployment_chat`: e.g. `gpt-4o-mini`
            """
        )

        endpoint = st.text_input("Azure endpoint (full URL)", value=_pref(cfg, "azure", "endpoint"))
        api_key = st.text_input("Azure API key", value=_pref(cfg, "azure", "api_key"))
        api_version = st.text_input("API version", value=_pref(cfg, "azure", "api_version", "2024-12-01-preview"))
        deployment_embed = st.text_input("Embedding deployment (deployment_embed)", value=_pref(cfg, "azure", "deployment_embed", "text-embedding-3-large"))
        deployment_chat = st.text_input("Chat deployment (deployment_chat)", value=_pref(cfg, "azure", "deployment_chat", "gpt-4o-mini"))
        app_name = st.text_input("App name (optional)", value=_pref(cfg, "app", "app_name", "ProDocChat"))

        submitted = st.form_submit_button("Save configuration")
        if submitted:
            errors = []
            if not endpoint.strip():
                errors.append("endpoint")
            if not api_key.strip():
                errors.append("api_key")
            if not api_version.strip():
                errors.append("api_version")
            if not deployment_embed.strip():
                errors.append("deployment_embed")
            if not deployment_chat.strip():
                errors.append("deployment_chat")

            if errors:
                st.error("Please provide values for: " + ", ".join(errors))
            else:
                ok = _save_from_inputs(cfg, user_dir, endpoint, api_key, api_version, deployment_embed, deployment_chat, app_name)
                if ok:
                    st.success("Configuration saved.")
                    # trigger rerun via query params (safe)
                    ts = str(int(time.time()))
                    try:
                        st.query_params = {"_cfg_saved": ts}
                    except Exception:
                        st.session_state["_cfg_saved_flag"] = not st.session_state.get("_cfg_saved_flag", False)
                    st.stop()
                else:
                    st.error("Failed to save configuration file.")


def show_settings_editor(cfg: ConfigParser, user_dir: str) -> None:
    """
    Render the editable settings form (for user-initiated settings editing).
    This function does NOT forcibly block the app; caller can choose to render it alone by calling st.stop()
    after invocation. After save, the function triggers a rerun and returns.
    """
    st.title("ProDocChat — Settings")
    st.markdown("Edit Azure/OpenAI configuration. Save to persist to your local app data directory.")

    with st.form("config_form_editor"):
        endpoint = st.text_input("Azure endpoint (full URL)", value=_pref(cfg, "azure", "endpoint"))
        api_key = st.text_input("Azure API key", value=_pref(cfg, "azure", "api_key"))
        api_version = st.text_input("API version", value=_pref(cfg, "azure", "api_version", "2024-12-01-preview"))
        deployment_embed = st.text_input("Embedding deployment (deployment_embed)", value=_pref(cfg, "azure", "deployment_embed", "text-embedding-3-large"))
        deployment_chat = st.text_input("Chat deployment (deployment_chat)", value=_pref(cfg, "azure", "deployment_chat", "gpt-4o-mini"))
        app_name = st.text_input("App name (optional)", value=_pref(cfg, "app", "app_name", "ProDocChat"))

        submitted = st.form_submit_button("Save settings")
        if submitted:
            errors = []
            if not endpoint.strip():
                errors.append("endpoint")
            if not api_key.strip():
                errors.append("api_key")
            if not api_version.strip():
                errors.append("api_version")
            if not deployment_embed.strip():
                errors.append("deployment_embed")
            if not deployment_chat.strip():
                errors.append("deployment_chat")

            if errors:
                st.error("Please provide values for: " + ", ".join(errors))
            else:
                ok = _save_from_inputs(cfg, user_dir, endpoint, api_key, api_version, deployment_embed, deployment_chat, app_name)
                if ok:
                    st.success("Settings saved.")
                    ts = str(int(time.time()))
                    try:
                        st.query_params = {"_cfg_saved": ts}
                    except Exception:
                        st.session_state["_cfg_saved_flag"] = not st.session_state.get("_cfg_saved_flag", False)
                    # return to caller; caller can st.stop() earlier to render only settings if desired
                    return
                else:
                    st.error("Failed to save configuration file.")
