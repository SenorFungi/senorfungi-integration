"""Constants for the SenorFungi integration."""

DOMAIN = "senorfungi"

# EMS bus logger (options of the config entry)
CONF_EMS_ENABLED = "ems_enabled"
CONF_EMS_HOST = "ems_host"
CONF_EMS_PORT = "ems_port"
CONF_EMS_FILTER = "ems_filter_ids"
CONF_EMS_IGNORE = "ems_ignore_ids"
CONF_EMS_PAIR_SRC = "ems_pair_src"
CONF_EMS_PAIR_DST = "ems_pair_dst"
CONF_EMS_LOG_DIR = "ems_log_dir"
CONF_EMS_RETENTION = "ems_retention_days"
CONF_EMS_COMPRESS = "ems_compress_after_days"

DEFAULT_EMS_PORT = 23  # EMS-ESP telnet console
EMS_API_INFO = "http://{host}/api/system/info"  # EMS-ESP REST API, readable without login
DEFAULT_EMS_RETENTION = 90
DEFAULT_EMS_COMPRESS = 1
SHARE_DIR = "/share"  # exists on Home Assistant OS and Supervised installs
EMS_LOG_SUBDIR = "ems_bus"
EMS_BROADCAST_ID = "00"
