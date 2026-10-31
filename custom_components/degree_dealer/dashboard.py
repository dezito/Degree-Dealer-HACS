"""Generated sidebar dashboard: an overview plus one section per configured room.

The dashboard is built from the entry's options (rooms, plant sensors) and the texts in
texts/<language>.json (Home Assistant's language, English as fallback). It is served as a
Lovelace dashboard that can be edited in the UI: once the user saves it, the saved copy is
shown instead of the generated one until it is deleted in the raw configuration editor.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from homeassistant.components import frontend
from homeassistant.components.lovelace import dashboard as lovelace_dashboard
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.json import cached_json_fragment, json_fragment
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .const import (
    CONF_OUTDOOR,
    CONF_RETURN,
    CONF_ROOMS,
    CONF_SUPPLY,
    CONF_SUPPLY_REFERENCE,
    DOMAIN,
    ROOM_AREA,
    ROOM_CLIMATES,
    ROOM_MOTION,
    ROOM_OPENINGS,
    ROOM_TEMPERATURES,
)

_LOGGER = logging.getLogger(__name__)

TEXTS_DIR = Path(__file__).parent / "texts"
LANGUAGES = ("en", "da", "de", "pl", "uk")
ICON = "mdi:radiator"
SENSOR_KEYS = (
    "indoor_temperature",
    "heat_demand",
    "supply_advice",
    "supply_return_difference",
    "return_excess",
    "heat_loss_coefficient",
    "time_constant",
    "heat_up_rate",
    "night_setback",
    "learning_days",
)
_TEXT = re.compile(r"\[\[(\w+)\]\]")


def dashboard_language(language: str | None) -> str:
    code = (language or "en").split("-")[0].lower()
    return code if code in LANGUAGES else "en"


def load_texts(language: str) -> dict[str, str]:
    """Dashboard texts of the language, English for anything missing (blocking file I/O)."""
    texts: dict[str, str] = json.loads((TEXTS_DIR / "en.json").read_text(encoding="utf-8"))["dashboard"]
    if language != "en":
        texts = {**texts, **json.loads((TEXTS_DIR / f"{language}.json").read_text(encoding="utf-8"))["dashboard"]}
    return texts


def dashboard_url(title: str) -> str:
    """Frontend url of the dashboard (Lovelace requires a '-' in it)."""
    slug = slugify(title).replace("_", "-") or "degree-dealer"
    return slug if slug.startswith("degree-dealer") else f"degree-dealer-{slug}"


def custom_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """The user's edited copy of the dashboard."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.dashboard_custom")


# -- building -----------------------------------------------------------------------------


def _fill(template: str, texts: dict[str, str], **values: str) -> str:
    """[[key]] -> dashboard text, <<name>> -> value (entity ids, area ids, currency)."""
    for name, value in values.items():
        template = template.replace(f"<<{name}>>", value)
    return _TEXT.sub(lambda match: texts.get(match.group(1), match.group(1)), template)


def _heading(text: str, icon: str) -> dict[str, Any]:
    return {"type": "heading", "heading": text, "icon": icon}


def _read_more(texts: dict[str, str], key: str) -> dict[str, Any]:
    return {
        "type": "markdown",
        "content": f"<details><summary>{texts['read_more']}</summary>\n\n{texts[key]}\n\n</details>",
        "grid_options": {"columns": "full"},
    }


def _tiles(entity_ids: list[str | None]) -> list[dict[str, Any]]:
    return [{"type": "tile", "entity": entity_id} for entity_id in entity_ids if entity_id]


def _markdown(content: str) -> dict[str, Any]:
    return {"type": "markdown", "content": content, "grid_options": {"columns": "full"}}


ADVICE = """\
{% set e = '<<advice>>' %}
{% set action = state_attr(e, 'action') %}
{% if action is none %}[[no_advice]]{% else %}
**[[advice]]:** {% if action == 'raise' %}[[action_raise]]{% elif action == 'lower' %}[[action_lower]]{% else %}[[action_hold]]{% endif %} ({{ state_attr(e, 'delta') }} K → {{ states(e) }} °C)

{% set reason = state_attr(e, 'reason') %}{% if reason == 'rooms_starved' %}[[reason_rooms_starved]]{% elif reason == 'valves_mostly_closed' %}[[reason_valves_mostly_closed]]{% elif reason == 'valves_partly_closed' %}[[reason_valves_partly_closed]]{% elif reason == 'at_limit' %}[[reason_at_limit]]{% else %}[[reason_in_band]]{% endif %}

[[key_room]]: {{ state_attr(e, 'key_room') }} ({{ state_attr(e, 'key_demand') }} %){% endif %}"""

SETBACK = """\
{% set e = '<<setback>>' %}
{% if states(e) in ['unknown', 'unavailable'] %}
**[[setback_learning]]**
{% set m = state_attr(e, 'missing') or [] %}{% if 'heat_loss_coefficient' in m %}
- [[missing_ua]]{% endif %}{% if 'time_constant' in m %}
- [[missing_tau]]{% endif %}{% if 'comfort_temperature' in m %}
- [[missing_comfort]]{% endif %}{% if 'outdoor_temperature' in m %}
- [[missing_outdoor]]{% endif %}
{% else %}
{% if state_attr(e, 'worth_it') %}**[[setback_worth_it]]: {{ states(e) }} K**{% else %}**[[setback_not_worth_it]]**{% endif %}

| | |
|---|---|
| [[setback_window]] | {{ as_timestamp(state_attr(e, 'window_start')) | timestamp_custom('%H:%M') }} - {{ as_timestamp(state_attr(e, 'window_end')) | timestamp_custom('%H:%M') }} |
| [[comfort]] | {{ state_attr(e, 'comfort_temperature') }} °C |
| [[outdoor]] | {{ state_attr(e, 'outdoor_temperature') }} °C |
{% if state_attr(e, 'worth_it') %}| [[lowest_temperature]] | {{ state_attr(e, 'best_lowest_temperature') }} °C |
| [[recovery_start]] | {{ as_timestamp(state_attr(e, 'recovery_start')) | timestamp_custom('%H:%M') if state_attr(e, 'recovery_start') else '-' }} |
| [[net]] | {{ state_attr(e, 'best_net') }} <<currency>> |
{% endif %}
{% set options = state_attr(e, 'options') or [] %}{% if options %}
| [[depth]] | [[saving]] | [[penalty]] | [[net]] |
|---|---|---|---|
{% for o in options %}| {{ o.depth }} K | {{ o.saving_kwh }} kWh / {{ o.saving_cost }} <<currency>> | {{ o.penalty_cost }} <<currency>> | {{ o.net }} <<currency>> |
{% endfor %}{% endif %}
{% endif %}"""

MODEL = """\
{% set e = '<<heat_loss>>' %}
{% set source = state_attr(e, 'source') %}
| | |
|---|---|
| [[source]] | {% if source == 'manual' %}[[source_manual]]{% elif source == 'learnt' %}[[source_learnt]]{% else %}[[not_learnt]]{% endif %} |
| [[days_used]] | {{ state_attr(e, 'days_used') }} |
| [[base_load]] | {{ state_attr(e, 'base_load_kwh_per_day') if state_attr(e, 'base_load_kwh_per_day') is not none else '-' }} kWh |
| [[r2]] | {{ state_attr(e, 'r2') if state_attr(e, 'r2') is not none else '-' }} |"""

ROOM = """\
{% set r = (state_attr('<<indoor>>', 'rooms') or []) | selectattr('area_id', 'eq', '<<area>>') | list | first %}
{% if r %}
| | |
|---|---|
| [[room_temperature]] | {{ r.temperature if r.temperature is not none else '-' }} °C |
| [[room_setpoint]] | {{ r.setpoint if r.setpoint is not none else '-' }} °C |
| [[room_demand]] | {{ r.demand if r.demand is not none else '-' }} % |
| [[room_window]] | {% if r.window_open %}[[open]]{% else %}[[closed]]{% endif %} |
| [[room_occupied]] | {% if r.occupied is none %}-{% elif r.occupied %}[[yes]]{% else %}[[no]]{% endif %} |
| [[room_humidity]] | {{ r.humidity if r.humidity is not none else '-' }} % |
| [[room_cold]] | {{ r.cold_minutes }} min |
| [[room_time_constant]] | {{ r.time_constant if r.time_constant is not none else '-' }} h |
| [[room_heat_up_rate]] | {{ r.heat_up_rate if r.heat_up_rate is not none else '-' }} K/h |
| [[room_supply]] | {% if r.exclude_from_supply %}[[ignored]]{% else %}[[used]]{% endif %} |
{% else %}[[no_data]]{% endif %}"""


def build_dashboard(
    texts: dict[str, str],
    sensors: dict[str, str | None],
    plant: dict[str, str | None],
    rooms: list[dict[str, Any]],
    currency: str,
) -> dict[str, Any]:
    """Lovelace config of the dashboard (pure, no Home Assistant needed).

    sensors: Degree Dealer sensor key -> entity id, plant: supply/return/outdoor/reference
    entity ids, rooms: the configured rooms with their area name under "name".
    """
    values = {
        "advice": sensors.get("supply_advice") or "",
        "setback": sensors.get("night_setback") or "",
        "heat_loss": sensors.get("heat_loss_coefficient") or "",
        "indoor": sensors.get("indoor_temperature") or "",
        "currency": currency,
    }
    status = [
        _heading(texts["section_status"], "mdi:radiator"),
        _read_more(texts, "help_status"),
        *_tiles([sensors.get("indoor_temperature"), sensors.get("supply_advice"), sensors.get("heat_demand"), sensors.get("return_excess"), sensors.get("supply_return_difference")]),
        *_tiles([plant.get("supply"), plant.get("return"), plant.get("outdoor")]),
        _markdown(_fill(ADVICE, texts, **values)),
    ]
    graph_entities = [
        entity_id
        for entity_id in (plant.get("supply"), plant.get("return"), sensors.get("supply_advice"), plant.get("reference"), plant.get("outdoor"))
        if entity_id
    ]
    supply_return = [
        _heading(texts["section_supply_return"], "mdi:chart-line"),
        _read_more(texts, "help_supply_return"),
        {"type": "history-graph", "hours_to_show": 48, "entities": graph_entities, "grid_options": {"columns": "full"}},
    ]
    setback = [
        _heading(texts["section_setback"], "mdi:weather-night"),
        _read_more(texts, "help_setback"),
        *_tiles([sensors.get("night_setback")]),
        _markdown(_fill(SETBACK, texts, **values)),
    ]
    model = [
        _heading(texts["section_model"], "mdi:home-analytics"),
        _read_more(texts, "help_model"),
        *_tiles([sensors.get(key) for key in ("heat_loss_coefficient", "time_constant", "heat_up_rate", "learning_days")]),
        _markdown(_fill(MODEL, texts, **values)),
    ]
    overview = {
        "type": "sections",
        "title": texts["view_overview"],
        "path": "overview",
        "icon": "mdi:view-dashboard",
        "max_columns": 4,
        "sections": [{"type": "grid", "cards": cards} for cards in (status, supply_return, setback, model)],
    }

    room_sections = [
        {"type": "grid", "cards": [_heading(texts["section_rooms"], "mdi:floor-plan"), _read_more(texts, "help_rooms")]}
    ]
    for room in rooms:
        cards = [
            _heading(room["name"], "mdi:door"),
            _markdown(_fill(ROOM, texts, **values, area=room[ROOM_AREA])),
            *_tiles([*room.get(ROOM_CLIMATES, []), *room.get(ROOM_TEMPERATURES, []), *room.get(ROOM_OPENINGS, []), *room.get(ROOM_MOTION, [])]),
        ]
        room_sections.append({"type": "grid", "cards": cards})
    rooms_view = {
        "type": "sections",
        "title": texts["view_rooms"],
        "path": "rooms",
        "icon": "mdi:floor-plan",
        "max_columns": 4,
        "sections": room_sections,
    }
    return {"views": [overview, rooms_view]}


# -- serving --------------------------------------------------------------------------------


class DegreeDealerDashboard(lovelace_dashboard.LovelaceConfig):
    """The generated dashboard, editable in the UI (the edited copy is stored separately)."""

    def __init__(
        self,
        hass: HomeAssistant,
        url_path: str,
        config: dict[str, Any],
        generated: dict[str, Any],
        store: Store[dict[str, Any]],
        custom: dict[str, Any] | None,
    ) -> None:
        super().__init__(hass, url_path, config)
        self._generated = generated
        self._store = store
        self._custom = custom
        self._json: json_fragment | None = None

    @property
    def mode(self) -> str:
        return MODE_STORAGE

    @property
    def customized(self) -> bool:
        return self._custom is not None

    @property
    def _current(self) -> dict[str, Any]:
        return self._custom if self._custom is not None else self._generated

    async def async_get_info(self) -> dict[str, Any]:
        return {"mode": self.mode, "views": len(self._current.get("views", []))}

    async def async_load(self, force: bool) -> dict[str, Any]:
        return self._current

    async def async_json(self, force: bool) -> json_fragment:
        if self._json is None:
            self._json = cached_json_fragment(self._current)
        return self._json

    async def async_save(self, config: dict[str, Any]) -> None:
        self._custom = config
        self._json = None
        await self._store.async_save({"config": config})
        self._config_updated()

    async def async_delete(self) -> None:
        """Back to the generated dashboard."""
        self._custom = None
        self._json = None
        await self._store.async_remove()
        self._config_updated()


async def async_setup_dashboard(hass: HomeAssistant, entry: ConfigEntry, options: dict[str, Any]) -> None:
    """Build the dashboard and add it to the sidebar until the entry unloads."""
    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None:
        _LOGGER.warning("Lovelace is not loaded, the Degree Dealer dashboard is not available")
        return
    url_path = dashboard_url(entry.title)
    existing = lovelace.dashboards.get(url_path)
    if existing is not None and not isinstance(existing, DegreeDealerDashboard):
        _LOGGER.warning("A dashboard already uses /%s, the Degree Dealer dashboard is not added", url_path)
        return

    registry = er.async_get(hass)
    sensors = {key: registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}") for key in SENSOR_KEYS}
    plant = {
        "supply": options.get(CONF_SUPPLY),
        "return": options.get(CONF_RETURN),
        "outdoor": options.get(CONF_OUTDOOR),
        "reference": options.get(CONF_SUPPLY_REFERENCE),
    }
    areas = ar.async_get(hass)
    rooms = []
    for room in options.get(CONF_ROOMS, []):
        area = areas.async_get_area(room[ROOM_AREA])
        rooms.append({**room, "name": area.name if area else room[ROOM_AREA]})
    texts = await hass.async_add_executor_job(load_texts, dashboard_language(hass.config.language))
    generated = build_dashboard(texts, sensors, plant, rooms, hass.config.currency)

    store = custom_store(hass, entry.entry_id)
    stored = await store.async_load()
    dashboard = DegreeDealerDashboard(
        hass,
        url_path,
        {"mode": MODE_STORAGE, "title": entry.title, "icon": ICON, "show_in_sidebar": True, "require_admin": False},
        generated,
        store,
        stored["config"] if stored else None,
    )
    lovelace.dashboards[url_path] = dashboard
    frontend.async_register_built_in_panel(
        hass,
        "lovelace",
        frontend_url_path=url_path,
        sidebar_title=entry.title,
        sidebar_icon=ICON,
        require_admin=False,
        config={"mode": MODE_STORAGE},
        update=existing is not None,
    )
    _LOGGER.info("Dashboard available at /%s%s", url_path, " (edited in the UI)" if dashboard.customized else "")

    @callback
    def _remove() -> None:
        if lovelace.dashboards.get(url_path) is dashboard:
            lovelace.dashboards.pop(url_path)
            frontend.async_remove_panel(hass, url_path)

    entry.async_on_unload(_remove)


async def async_remove_dashboard(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the user's edited copy together with the entry."""
    await custom_store(hass, entry_id).async_remove()
