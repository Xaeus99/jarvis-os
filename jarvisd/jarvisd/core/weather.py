"""Weather from Open-Meteo (free, no key). Only the city's coordinates are sent."""
from __future__ import annotations
import json, logging, urllib.parse, urllib.request

log = logging.getLogger("jarvis.weather")

WMO = {0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "thunderstorms with hail"}

def _get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-desktop/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def geocode(name, count=5):
    """[(label, lat, lon, timezone)] for a city name."""
    q = urllib.parse.urlencode({"name": name, "count": count, "language": "en", "format": "json"})
    res = _get("https://geocoding-api.open-meteo.com/v1/search?" + q).get("results") or []
    out = []
    for r in res:
        parts = [r.get("name"), r.get("admin1"), r.get("country_code")]
        out.append((", ".join(p for p in parts if p), r["latitude"], r["longitude"], r.get("timezone", "auto")))
    return out

def forecast(lat, lon, units="fahrenheit"):
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "temperature_unit": "fahrenheit" if units.startswith("f") else "celsius",
        "wind_speed_unit": "mph" if units.startswith("f") else "kmh"})
    return _get("https://api.open-meteo.com/v1/forecast?" + q)

def describe(data, tomorrow=False, city=""):
    """One or two spoken sentences."""
    cur, day = data.get("current", {}), data.get("daily", {})
    unit = "degrees"
    where = f" in {city.split(',')[0]}" if city else ""
    if tomorrow and day.get("temperature_2m_max"):
        i = 1 if len(day["temperature_2m_max"]) > 1 else 0
        cond = WMO.get(day["weather_code"][i], "mixed weather")
        rain = day.get("precipitation_probability_max", [None, None])[i]
        s = f"Tomorrow{where}: {cond}, a high of {round(day['temperature_2m_max'][i])} and a low of {round(day['temperature_2m_min'][i])}."
        if rain is not None and rain >= 30:
            s += f" {rain} percent chance of rain."
        return s
    t = round(cur.get("temperature_2m", 0))
    cond = WMO.get(cur.get("weather_code"), "mixed weather")
    s = f"It's {t} {unit} and {cond}{where}"
    if day.get("temperature_2m_max"):
        s += f", with a high of {round(day['temperature_2m_max'][0])} today"
    s += "."
    rain = (day.get("precipitation_probability_max") or [None])[0]
    if rain is not None and rain >= 40:
        s += f" {rain} percent chance of rain, so perhaps a jacket."
    return s

def spoken(wcfg, tomorrow=False):
    """Weather sentence from config, or None if no city is set."""
    lat, lon = wcfg.get("latitude"), wcfg.get("longitude")
    if lat is None or lon is None:
        return None
    return describe(forecast(lat, lon, wcfg.get("units", "fahrenheit")), tomorrow, wcfg.get("city", ""))


def panel_data(wcfg):
    """v4 workspace weather card: a dict, or {'error': ...} when no city is set."""
    lat, lon = wcfg.get("latitude"), wcfg.get("longitude")
    if lat is None or lon is None:
        return {"error": "No city set yet, sir. Run setup again, or say \"Jarvis, weather\" once it's set."}
    units = wcfg.get("units", "fahrenheit")
    d = forecast(lat, lon, units)
    cur, day = d.get("current", {}), d.get("daily", {})
    code = cur.get("weather_code")
    return {"temp": cur.get("temperature_2m", 0), "feels": cur.get("apparent_temperature"), "code": code,
            "cond": WMO.get(code, "mixed weather"), "wind": cur.get("wind_speed_10m", 0),
            "wind_unit": "mph" if units.startswith("f") else "km/h",
            "hi": (day.get("temperature_2m_max") or [0])[0], "lo": (day.get("temperature_2m_min") or [0])[0],
            "rain": (day.get("precipitation_probability_max") or [0])[0] or 0, "city": wcfg.get("city", "")}
