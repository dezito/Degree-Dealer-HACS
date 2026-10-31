# Degree Dealer design

## Core idea

High return temperature and room overshoot share one cause: the supply water is
hotter than the house needs. When thermostatic valves open, hot water rushes
through the radiators and comes back warm, and the room overshoots because the
valve reacts too late.

Degree Dealer lets the heating controller supply the lowest supply temperature
that still keeps every room warm, so the thermostats stay mostly open. The
thermostats only act as per-room limiters.

## Inputs

| Source | Data | Rate |
|---|---|---|
| Heating controller (ECL Modbus) | Supply/return sensors, outdoor temperature, supply reference, valve position; writable: curve slope, min/max supply, return limit, comfort/saving offset, mode | ~30 s, local |
| Thermostats (Tado X via Matter) | Room temperature, setpoint, heating action | Local |
| Thermostats (Tado cloud) | Valve demand (heating power), windows, presence, mould risk | Cloud, quota limited, read only |
| Meter data (EForsyning) | Energy, supply, return, expected return, cooling | Daily, about one day late |
| Forecasts | Outdoor temperature, solar irradiance | Hourly |
| Presence | Home/away | Events |

## Modules

### 1. Valve-led supply temperature (highest value)

A slow loop (15-30 min) looks at valve demand in the most demanding room
(optionally the second highest, so one bathroom does not rule the house).

- Target: the most demanding valve is 70-90 % open.
- All valves below ~50 %: lower supply 0.5-1 K.
- A room at 100 % and below setpoint for a while: raise supply.
- Written to the controller as a dynamic max supply cap or parallel shift; the
  weather curve stays underneath as a safety net.
- Solar forecast lowers supply ahead of sunny afternoons.

### 2. Return temperature guard

- Set the controller's return limit dynamically, e.g. to the utility's expected
  return minus a margin. The controller handles the fast loop.
- The utility's daily return and cooling figures are the ground truth for which
  strategies worked.

### 3. No overshoot on recovery

- Optimum start: learn each room's heat-up rate (by supply and outdoor
  temperature) and start exactly early enough to reach the target on time.
- Cap supply during recovery and stagger rooms so not every valve opens fully
  at once.
- Disable thermostat and controller boost.

### 4. Setbacks only when they pay

Physically a setback always saves some energy: heat loss scales with the
indoor-outdoor difference, and recovery only puts back what the house released.
The question is whether that saving beats the side effects.

```
saving = UA * sum(T_comfort - T_indoor_predicted) * dt * heat_price
cost   = extra return temperature during recovery * motivation tariff
       + comfort / mould penalty (min. 16-17 °C)
set back only if saving - cost > threshold
```

- UA (W/K) is learnt from daily meter energy against degree days.
- Cooling rate (time constant) is learnt from night-time room cooling.
- Degree Dealer chooses both whether and how deep (1-4 K).
- The real recovery penalty is measured by comparing nights with and without
  setback against the next day's return and cooling figures.

## Design rules

- One owner of setpoints: Degree Dealer writes setpoints locally (Matter);
  the thermostat app schedule is disabled. Cloud is read only.
- The controller stays in comfort mode; Degree Dealer switches to saving.
- Rate limit controller writes (>= 0.5 K change, a few per hour).
- Shadow mode first: recommendations without writes.

## Control modes

Many district heating plants are connected directly: the radiators get the utility's supply
and the controller can only limit the return temperature by throttling the flow. Degree
Dealer therefore advises what the plant can actually control:

- `return` (default): the lowest return limit that still lets every room get enough heat.
  Raise it only when a room is starved while the return is at the limit (the limiter holds
  the flow back); a starved room with the return well below the limit is a radiator, valve
  or supply problem, not a limit problem. Otherwise lower it towards the return target.
- `supply`: the valve-led supply temperature described below.
- `none`: no control advice, only monitoring and setbacks.

## Learning from the history

On the first start (and with the *Learn from history* button) the recorder is used as far
back as it goes: long-term statistics (daily means, kept forever) for the heat loss fit before
the recorded states, and the recorded states (keep_days) replayed in 5 minute steps through the
same snapshot/learn code as live. The heat loss fit only uses days below 12 °C outside.

## Rooms

A room is a Home Assistant area. The flow pre-fills each area with its
thermostats, temperature sensors (the thermostat's own temperature sensor is
skipped), valve demand sensors (% sensors named valve/heating power/demand),
windows/doors, motion/presence and humidity sensors.

- Room temperature: mean of the temperature sensors, else of the thermostats.
- Valve demand: highest valve sensor, else the thermostats' `hvac_action`
  (heating = 100 %, idle = 0 %), smoothed.
- Open window/door: the room pauses learning and is left out of the supply advice.
- Motion and humidity: shown per room; meant for room setbacks and mould
  protection in later phases.

## Phases

1. Measurement and advice (implemented): rooms from areas, learn UA, cooling
   and heat-up rates, supply advice, return target, night setback economics.
   Nothing is written to hardware.
2. Valve-led supply temperature and return guard.
3. Optimum start and economic setback.
4. Per-room diagnostics: which radiators raise the return when they open
   (hydraulic balancing hints).

## Open questions

- ECL application and what S1-S6 are connected to.
- Domestic hot water: tank or instant heat exchanger, circulation?
- Utility motivation tariff: return vs expected return, or cooling? Price per MWh?
- Whether ECL Modbus parameter writes are stored in flash (write endurance).
