"""Fetch hourly weather for USYD (Camperdown) from Open-Meteo and turn it into solweig.Weather objects.

Open-Meteo is free and needs no API key. Radiation values are the mean of the *preceding* hour,
which matches SOLWEIG's hour-ending convention (a row labelled 15:00 = 14:00-15:00, sun at 14:30).

Usage:
    from live_weather import fetch_hourly, to_solweig
    rows, utc_offset = fetch_hourly()                  # today (and yesterday), live forecast
    rows, utc_offset = fetch_hourly(date="2026-01-15") # a past day, e.g. a heatwave
    weather = to_solweig(rows, hours=range(9, 19))     # 9am-6pm, for the time slider
    location = solweig.Location(latitude=LAT, longitude=LON, utc_offset=utc_offset)
"""
from datetime import date as Date
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import solweig

LAT, LON = -33.888, 151.187  # University of Sydney, Camperdown
TZ = ZoneInfo("Australia/Sydney")
VARS = "temperature_2m,relative_humidity_2m,shortwave_radiation,direct_radiation,diffuse_radiation,wind_speed_10m"


def fetch_hourly(date: str | None = None):
    """Return (rows, utc_offset_hours). date=None gives live forecast data for yesterday and today."""
    params = dict(latitude=LAT, longitude=LON, hourly=VARS, wind_speed_unit="ms", timezone="Australia/Sydney")
    if date is None:
        url = "https://api.open-meteo.com/v1/forecast"
        params.update(past_days=1, forecast_days=1)
    elif (datetime.now(TZ).date() - Date.fromisoformat(date)).days <= 60:
        # the ERA5 archive runs about 5 days behind, so recent days (and today) come from the forecast API,
        # which keeps the last ~3 months
        url = "https://api.open-meteo.com/v1/forecast"
        params.update(start_date=date, end_date=date)
    else:  # older days: ERA5-based archive (back to 1940)
        url = "https://archive-api.open-meteo.com/v1/archive"
        params.update(start_date=date, end_date=date)
    h = requests.get(url, params=params, timeout=20).json()["hourly"]
    rows = [dict(zip(h, vals)) for vals in zip(*h.values())]
    day = datetime.fromisoformat(rows[-1]["time"]).replace(tzinfo=TZ)
    # Sydney switches AEST(+10) -> AEDT(+11) on the first Sunday of October, so take the offset per day.
    return rows, day.utcoffset().total_seconds() / 3600


def to_solweig(rows, hours=range(9, 19), date: str | None = None):
    """Convert Open-Meteo rows to solweig.Weather for the given local hours (default: the last day returned)."""
    days = sorted({r["time"][:10] for r in rows})
    day = date or days[-1]
    out = []
    for r in rows:
        t = datetime.fromisoformat(r["time"])
        if r["time"][:10] != day or t.hour not in hours or r["temperature_2m"] is None:
            continue
        out.append(solweig.Weather(
            datetime=t,
            ta=r["temperature_2m"],
            rh=min(max(r["relative_humidity_2m"], 0), 100),
            global_rad=max(r["shortwave_radiation"], 0.0),
            ws=max(r["wind_speed_10m"], 0.1),
            measured_direct_rad=max(r["direct_radiation"], 0.0),    # horizontal direct component
            measured_diffuse_rad=max(r["diffuse_radiation"], 0.0),
        ))
    return out
