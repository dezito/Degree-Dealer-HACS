# Degree-Dealer-HACS agent guide

Instructions for coding agents working in this repository. Do not guess behavior
that writes to the heating controller or thermostats.

## Project scope

Degree Dealer is a Home Assistant custom integration installed through HACS. It
optimises district heating: low return temperature, no room overshoot, and
setbacks only when they pay off. The design lives in `docs/design.md`.

- Integration code: `custom_components/degree_dealer/`
- Tests: `tests/`
- Documentation: `docs/` and `README.md`

## Better ideas are welcome

The project owner is not a heating engineer. If you know a better approach
(control strategy, physics, hydraulics, tariffs, sensors, safety) than what is
asked for or what `docs/design.md` describes, say so and explain why before or
alongside implementing the request. Point out assumptions that look wrong.

## Rules

- Start in shadow mode: calculate and expose recommendations before anything
  writes to hardware. Every write path must be behind an explicit switch.
- Writes to the heating controller must be rate limited and only sent when the
  value changes meaningfully; controller parameters may be stored in flash.
- Keep hardware specific code behind adapters (controller, thermostats, meter
  data) so other brands can be added later.
- Sister projects BattMind-HACS and Cable-Juice-Planner-HACS are references for
  structure (coordinator, config flow, dashboard, translations); follow their
  conventions where they fit.
- Translations (`strings.json` + `translations/{en,da,de,pl,uk}.json`) and
  dashboard texts (`texts/{en,da,de,pl,uk}.json`): update every language
  together and keep the keys identical.
- When the dashboard changes, check that each section's "Read more" text
  (`help_*` in `texts/`) still covers the entities and functions shown in it.
- Rooms are Home Assistant areas. `discovery.py` pre-fills each room with the
  area's thermostats, temperature, valve, window/door, motion and humidity
  entities; the user can adjust them in the flow.

## Code map

```text
custom_components/degree_dealer/
├── __init__.py      Entry setup/unload, stored learning removed with the entry
├── config_flow.py   Plant, rooms (areas), one page per room, meter, economy, dashboard
├── dashboard.py     Generated sidebar dashboard (built-in cards only), editable in the UI
├── texts/           Dashboard texts per language ([[key]] in the templates)
├── discovery.py     Finds the usable entities of an area
├── coordinator.py   Reads states every 5 min, feeds learning, builds the data
├── learning.py      Stored learning: daily records, time constant, heat-up rate
├── model.py         Pure maths: heat loss fit, supply advice, setback economics
└── sensor.py        The published sensors
```

## Tests

Use the shared Windows test venv from `../ha-dev` (same pins as BattMind):

```powershell
.\ha-dev\run_tests_windows.ps1 degree
```
