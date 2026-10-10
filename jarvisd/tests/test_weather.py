from jarvisd.brain import LinuxBrain
from jarvisd.core import weather
from jarvisd import tablink
import json

DATA = {"current": {"temperature_2m": 51.4, "weather_code": 3},
        "daily": {"temperature_2m_max": [58.2, 61.0], "temperature_2m_min": [40.1, 44.0],
                  "precipitation_probability_max": [10, 70], "weather_code": [3, 63]}}

def brain(cfg, said, fwd=None):
    b = LinuxBrain(cfg, run=lambda argv: (0, ""), say=said.append, forward=fwd)
    return b

def test_weather_spoken_from_config(monkeypatch, tmp_path):
    monkeypatch.setattr(weather, "forecast", lambda lat, lon, units="fahrenheit": DATA)
    said = []
    b = LinuxBrain({"briefing": {"city": "Testville", "latitude": 1.0, "longitude": 2.0}}, state_dir=str(tmp_path),
                   run=lambda argv: (0, ""), say=said.append)
    b.handle("what's the weather")
    assert "51" in said[-1] and "overcast" in said[-1]
    b.handle("is it going to rain tomorrow")
    assert said[-1].startswith("Tomorrow") and "70 percent" in said[-1]

def test_weather_without_city_asks_tab(tmp_path):
    said, sent = [], []
    b = LinuxBrain({}, state_dir=str(tmp_path), run=lambda argv: (0, ""), say=said.append, forward=sent.append)
    b.handle("what's the weather")
    assert sent and "asked Tab" in said[-1]

def test_link_copies_weather_city(tmp_path):
    win = tmp_path / "win.json"
    win.write_text(json.dumps({"briefing": {"city": "Testville", "latitude": 1.0, "longitude": 2.0, "units": "celsius",
                                            "tab_request": "x"}}))
    cfg = tablink.import_link(str(win), str(tmp_path / "linux.json"))
    assert cfg["briefing"] == {"city": "Testville", "latitude": 1.0, "longitude": 2.0, "units": "celsius"}
