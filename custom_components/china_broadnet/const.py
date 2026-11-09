"""Constants for China Broadnet."""

from datetime import timedelta

DOMAIN = "china_broadnet"
DEFAULT_REFRESH_MINUTES = 8
MIN_REFRESH_MINUTES = 5
MAX_REFRESH_MINUTES = 60
TRAFFIC_KEYS = ("data_total", "data_used", "data_remaining")
MAX_AGE = timedelta(hours=48)
SMS_WINDOW = timedelta(minutes=10)
