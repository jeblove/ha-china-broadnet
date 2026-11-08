"""Constants for China Broadnet."""

from datetime import timedelta

DOMAIN = "china_broadnet"
TRAFFIC_KEYS = ("data_total", "data_used", "data_remaining")
MAX_AGE = timedelta(hours=48)
SMS_WINDOW = timedelta(minutes=10)
