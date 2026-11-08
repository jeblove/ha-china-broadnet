"""Constants for China Broadnet."""

from datetime import timedelta

DOMAIN = "china_broadnet"
TRAFFIC_KEYS = ("data_total", "data_used", "data_remaining")
MAX_AGE = timedelta(hours=48)
BROWSER_MAX_AGE = timedelta(minutes=90)
SMS_WINDOW = timedelta(minutes=10)
