# Degree Dealer for Home Assistant

District heating optimisation that only deals out the degrees that pay off.

Degree Dealer aims to get as much heat as possible out of every litre of district heating water:

- **Low return temperature**: keeps the supply temperature as low as the house allows, so the
  water goes back to the utility cold.
- **No overshoot**: brings rooms back up to temperature gradually and on time, instead of opening
  every radiator valve at once.
- **Setback only when it pays**: night setbacks are advised only when the predicted saving beats
  the cost of recovering (return temperature tariff, comfort and the lowest room temperature).

> Status: phase 1 (measure, learn and advise). Degree Dealer does not control anything yet; it
> publishes sensors with its advice. See [docs/design.md](docs/design.md).

## Setup

Add **Degree Dealer** under *Settings → Devices & services*. The flow asks for:

1. **Heating plant**: supply and return temperature, outdoor temperature (sensor or weather
   entity), and what the controller can control:
   - **Return temperature limit** (default): directly connected district heating, where the
     supply comes from the utility and the controller can only limit the return by throttling
     the flow. Optionally the controller's current return limit entity and the limits.
   - **Supply temperature**: mixing loop, heat exchanger, boiler or heat pump. Optionally the
     controller's supply reference and the limits.
   - **Nothing**: only monitoring and the night setback.
2. **Rooms**: choose Home Assistant **areas**. For each area Degree Dealer finds the thermostats,
   temperature sensors, valve demand sensors, windows/doors, motion/presence and humidity
   sensors in it. Check and adjust them, and optionally keep a room out of the advice
   (e.g. a bathroom with floor heating).
3. **Meter data** (optional): a heat meter reading (Wh/kWh/MWh/MJ/GJ), how many days the data
   arrives late, and the utility's expected return temperature or a fixed target.
4. **Economy and setback**: heat price, return temperature tariff, night window, lowest room
   temperature, largest setback and the minimum saving. The heat loss coefficient can be set
   by hand when there is no meter data.
5. **Dashboard**: whether to add the Degree Dealer dashboard to the sidebar.

Everything can be changed later under *Configure*.

## Learning from the history

The first time, Degree Dealer learns from Home Assistant's recorder as far back as it has data,
in the background:

- **Long-term statistics** (kept forever for sensors with a state class): daily means of the
  room temperature sensors and the outdoor sensor and the daily meter reading, for the days
  before the recorded states. They feed the heat loss coefficient.
- **Recorded states** (as many days as the recorder keeps, `purge_keep_days`): replayed in
  5 minute steps through the same code as the live updates, so the cooling time constant,
  heat-up rate, comfort temperature and recent days are learnt as if Degree Dealer had been
  running.

Only days below 12 °C outside are used for the heat loss coefficient (on warmer days sun and
people heat the house). The button **Learn from history** forgets what was learnt and starts
over, e.g. after changing rooms or sensors.

## Dashboard

Degree Dealer adds a dashboard to the sidebar (in Home Assistant's language: English, Danish,
German, Polish or Ukrainian). It only uses built-in cards:

- **Overview**: status (indoor temperature, the recommendation (supply or return limit) with the reason, heat demand,
  return above target, cooling), a 48 hour graph of supply and return, the night setback with
  every depth's saving, tariff cost and net result, and the house model.
- **Rooms**: one section per area with temperature, setpoint, valve demand, window/door,
  in use, humidity, time constant and heat-up rate, and the room's own entities.

Every section has a *Read more* explaining what is shown. The dashboard follows the rooms when
you change them. You can edit it in the UI; your copy is then shown instead, until you delete it
in the raw configuration editor.

## Sensors

| Sensor | What it shows |
|---|---|
| Indoor temperature | Mean of the rooms; attributes per room (temperature, setpoint, valve demand, window, occupied, humidity) |
| Heat demand | Valve demand of the most demanding room |
| Recommended supply temperature | Supply control: advised supply; attributes `action` (raise/lower/hold), `reason`, `current`, `key_room` |
| Recommended return limit | Return limit control: advised return limit; same attributes |
| Cooling (supply - return) | Supply minus return temperature |
| Return above target | Return temperature minus the expected/target return |
| Heat loss coefficient | UA in W/K, learnt from daily meter data against indoor-outdoor temperature |
| Cooling time constant | How slowly the house cools down (learnt at night while the valves are closed) |
| Heat-up rate | How fast the rooms heat up (K/h) |
| Night setback | Most profitable setback in K for the next night (0 = not worth it); attributes with saving, tariff cost, net and recovery start |
| Days learnt | Number of days with recorded data; attributes about learning from the history |
| Learn from history (button) | Start over from the recorder history |

While a window or door in a room is open, the room pauses learning and does not decide the
advice.

## License

GPL-3.0, see [LICENSE](LICENSE).
