"""The generated sidebar dashboard."""
from __future__ import annotations

import json
from pathlib import Path
import re

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from custom_components.degree_dealer import dashboard as dd
from custom_components.degree_dealer.dashboard import (
    ADVICE,
    LANGUAGES,
    MODEL,
    ROOM,
    SETBACK,
    TEXTS_DIR,
    DegreeDealerDashboard,
    build_dashboard,
    dashboard_language,
    dashboard_url,
    load_texts,
)

from .conftest import set_climate, set_plant

SOURCE = Path(dd.__file__).read_text(encoding="utf-8")


def _cards(config: dict) -> list[dict]:
    return [card for view in config["views"] for section in view["sections"] for card in section["cards"]]


def test_every_language_has_the_same_texts_and_all_used_keys() -> None:
    english = set(json.loads((TEXTS_DIR / "en.json").read_text(encoding="utf-8"))["dashboard"])
    templates = [SETBACK, MODEL, ROOM] + [ADVICE.replace("<<mode>>", mode) for mode in ("supply", "return")]
    used = {key for template in templates for key in re.findall(r"\[\[(\w+)\]\]", template)}
    used |= {f"reason_{reason}" for reason in dd.REASONS}
    used |= set(re.findall(r'texts\["(\w+)"\]', SOURCE))
    used |= set(re.findall(r'_read_more\(texts, "(\w+)"\)', SOURCE))
    assert used <= english, used - english
    for language in LANGUAGES:
        keys = set(json.loads((TEXTS_DIR / f"{language}.json").read_text(encoding="utf-8"))["dashboard"])
        assert keys == english, language


def test_language_and_url() -> None:
    assert dashboard_language("da") == "da"
    assert dashboard_language("en-GB") == "en"
    assert dashboard_language("fr") == "en"
    assert dashboard_url("Degree Dealer") == "degree-dealer"
    assert dashboard_url("Summer house") == "degree-dealer-summer-house"


def test_build_dashboard_has_an_overview_and_one_section_per_room() -> None:
    texts = load_texts("da")
    sensors = {key: f"sensor.dd_{key}" for key in dd.SENSOR_KEYS}
    plant = {"supply": "sensor.supply", "return": "sensor.return", "outdoor": None, "reference": None}
    rooms = [
        {"area_id": "stue", "name": "Stue", "climate_entity_ids": ["climate.stue"], "opening_entity_ids": ["binary_sensor.stue_vindue"]},
        {"area_id": "bad", "name": "Bad", "climate_entity_ids": ["climate.bad"]},
    ]
    sensors["learn_from_history"] = "button.dd_learn_from_history"
    config = build_dashboard(texts, sensors, {**plant, "return_limit": "number.limit"}, rooms, "DKK", "supply")
    overview, rooms_view = config["views"]
    assert overview["title"] == "Overblik"
    assert len(overview["sections"]) == 4
    assert len(rooms_view["sections"]) == 3
    tiles = [card for card in _cards(config) if card["type"] == "tile"]
    entities = [card["entity"] for card in tiles]
    assert "sensor.dd_supply_advice" in entities and "binary_sensor.stue_vindue" in entities
    assert "sensor.dd_return_limit_advice" not in entities
    button = next(card for card in tiles if card["entity"] == "button.dd_learn_from_history")
    assert button["tap_action"]["perform_action"] == "button.press"
    graph = next(card for card in _cards(config) if card["type"] == "history-graph")
    # the outdoor sensor is not configured, so it is left out
    assert graph["entities"] == ["sensor.supply", "sensor.return", "sensor.dd_supply_advice"]
    text = json.dumps(config, ensure_ascii=False)
    assert "[[" not in text and "<<" not in text
    assert "'stue'" in text and "Læs mere" in text and "DKK" in text and "Sænk fremløbet" in text


def test_dashboard_for_return_limit_and_without_control() -> None:
    texts = load_texts("en")
    sensors = {key: f"sensor.dd_{key}" for key in dd.SENSOR_KEYS}
    plant = {"supply": "sensor.supply", "return": "sensor.return", "outdoor": None, "reference": None, "return_limit": "number.limit"}
    config = build_dashboard(texts, sensors, plant, [], "DKK", "return")
    graph = next(card for card in _cards(config) if card["type"] == "history-graph")
    assert graph["entities"] == ["sensor.supply", "sensor.return", "sensor.dd_return_limit_advice", "number.limit"]
    assert "Lower the return limit" in json.dumps(config)
    config = build_dashboard(texts, sensors, plant, [], "DKK", "none")
    text = json.dumps(config)
    assert "dd_supply_advice" not in text and "dd_return_limit_advice" not in text and "[[advice]]" not in text


async def test_dashboard_is_added_to_the_sidebar_and_editable(hass: HomeAssistant, entry) -> None:
    set_plant(hass)
    set_climate(hass, "climate.living_trv", 20.0, 21.0, "heating")
    hass.states.async_set("sensor.living_heating_power", "40", {"unit_of_measurement": "%"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    dashboard = hass.data[LOVELACE_DATA].dashboards["degree-dealer"]
    assert isinstance(dashboard, DegreeDealerDashboard)
    config = await dashboard.async_load(False)
    rooms_view = config["views"][1]
    assert [section["cards"][0]["heading"] for section in rooms_view["sections"][1:]] == ["Living room", "Bathroom"]

    # the markdown templates render in Home Assistant
    for template in (ADVICE, SETBACK, MODEL, ROOM):
        content = dd._fill(
            template,
            load_texts("en"),
            advice="sensor.degree_dealer_recommended_return_limit",
            mode="return",
            learning="sensor.degree_dealer_days_learnt",
            setback="sensor.degree_dealer_night_setback",
            heat_loss="sensor.degree_dealer_heat_loss_coefficient",
            indoor="sensor.degree_dealer_indoor_temperature",
            currency="DKK",
            area=next(iter(entry.options["rooms"]))["area_id"],
        )
        rendered = Template(content, hass).async_render(parse_result=False)
        assert "Traceback" not in rendered
    room_text = dd._fill(ROOM, load_texts("en"), indoor="sensor.degree_dealer_indoor_temperature", area=entry.options["rooms"][0]["area_id"])
    assert "40.0 %" in Template(room_text, hass).async_render(parse_result=False)

    await dashboard.async_save({"views": [{"title": "Mine"}]})
    assert (await dashboard.async_load(False))["views"][0]["title"] == "Mine"
    await dashboard.async_delete()
    assert (await dashboard.async_load(False))["views"][0]["path"] == "overview"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert "degree-dealer" not in hass.data[LOVELACE_DATA].dashboards


async def test_no_dashboard_when_turned_off(hass: HomeAssistant, entry) -> None:
    hass.config_entries.async_update_entry(entry, options={**entry.options, "dashboard": False})
    set_plant(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert "degree-dealer" not in hass.data[LOVELACE_DATA].dashboards
