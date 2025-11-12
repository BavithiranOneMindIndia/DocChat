# utils.py — unified utilities for ProDocChat (exports both get_user_data_dir and get_app_user_data_dir)
import os
import logging
import shutil
from configparser import ConfigParser, NoSectionError, NoOptionError
from appdirs import user_data_dir

logger = logging.getLogger("prodocchat")
logging.basicConfig(level=logging.INFO)

APP_NAME = "ProDocChat"


def get_user_data_dir(app_name: str = APP_NAME) -> str:
    """
    Return the OS-specific user data directory for ProDocChat and ensure it exists.
    Example:
      Windows: %LOCALAPPDATA%/ProDocChat or %APPDATA%/ProDocChat
      macOS: ~/Library/Application Support/ProDocChat
      Linux: ~/.local/share/ProDocChat
    """
    path = user_data_dir(app_name)
    os.makedirs(path, exist_ok=True)
    return path


# keep older/alternate name used in some files for compatibility
def get_app_user_data_dir(app_name: str = APP_NAME) -> str:
    """
    Alias for get_user_data_dir for backward compatibility with other modules.
    """
    return get_user_data_dir(app_name)


def ensure_collections_dir(cfg: ConfigParser) -> str:
    """
    Ensure the collections directory exists.
    Prefer cfg['app']['collections_dir'] if set, otherwise default to <user_data_dir>/collections.
    Returns the path to the collections dir.
    """
    try:
        base = cfg.get("app", "collections_dir", fallback="").strip()
    except (NoSectionError, NoOptionError, AttributeError):
        base = ""
    if not base:
        base = os.path.join(get_user_data_dir(APP_NAME), "collections")
    os.makedirs(base, exist_ok=True)
    return base


def load_or_copy_config() -> ConfigParser:
    """
    Load config.ini. Priority:
      1) user_data_dir/config.ini (preferred)
      2) copy ./config.ini -> user_data_dir/config.ini (if exists)
      3) fallback to empty ConfigParser
    Returns ConfigParser instance.
    """
    cfg = ConfigParser()
    default_path = os.path.join(os.getcwd(), "config.ini")
    user_cfg_dir = get_user_data_dir(APP_NAME)
    user_cfg_path = os.path.join(user_cfg_dir, "config.ini")

    # if config already present in user data dir, load it
    if os.path.exists(user_cfg_path):
        cfg.read(user_cfg_path)
        logger.info(f"Loaded config from {user_cfg_path}")
        return cfg

    # if project-level config exists at cwd, copy it to user data dir
    if os.path.exists(default_path):
        try:
            os.makedirs(user_cfg_dir, exist_ok=True)
            shutil.copy(default_path, user_cfg_path)
            cfg.read(user_cfg_path)
            logger.info(f"Copied default config to user data dir: {user_cfg_path}")
            return cfg
        except Exception as e:
            logger.error(f"Failed copying config.ini to user data dir: {e}")

    # fallback — empty config
    logger.warning("No config.ini found; using empty configuration.")
    return cfg
