# Routes and maneuvers for the HMI

How the HMI gets a route with turn-by-turn maneuvers from this backend, and
what each field means. The contract is `proto/e39maps_api/v1/routes.proto`
(`e39maps.v1.RouteService`); this file explains it. In the HMI (e39-rpi):
`RemoteRouteRepository` / `RouteGrpcClient` in `ca.stefanm.ibus.gui.map.repo`
(regenerate the Kotlin stubs with `./makeMapsProtos.sh` after proto changes).

## The call

`ComputeRoute(origin, destination, via[], costing, units, language)`:

- `via`: stops in between, in order. Each one ends a leg; the legs come back
  joined into one route.
- `costing`: car (the only one; the routing tiles are car-only).
- `units`: kilometers or miles, for the instruction **texts** only ("In 2
  miles, ..."). All numeric lengths are always meters.
- `language`: BCP 47, e.g. `en-US` (default), `en-GB`, `fr-FR`, `de-DE`.

Answers in 0.2-0.3 s on the laptop for a city route, ~2-3 s in QEMU for
long ones (the first call after the service starts loads Valhalla: ~3 s
in QEMU).

| Status | When |
| --- | --- |
| `OK` | a `Route` |
| `INVALID_ARGUMENT` | no origin/destination, or coordinates out of range |
| `NOT_FOUND` | no road near a point, or no road connects them (e.g. outside Ontario/Quebec, or on an island without a ferry) |
| `UNAVAILABLE` | no routing tiles installed, no Valhalla, or tiles built by another Valhalla version |

## The route

| Field | Meaning |
| --- | --- |
| `shape` | the whole route as points (WGS 84), origin to destination: what the map draws (the HMI's `Route.path` / `List<LatLng>`) |
| `length_meters`, `duration_seconds` | totals |
| `maneuvers` | in driving order; the first is the start, the last the arrival |

## A maneuver

| Field | Meaning |
| --- | --- |
| `location` | where the maneuver happens (= `shape[begin_shape_index]`) |
| `trigger_radius_meters` | show/announce the maneuver once the car is within this distance of `location` (straight line) |
| `instruction` | text to show, e.g. "Turn left onto Parliament Road." |
| `verbal_pre_transition` | text to speak before the maneuver (same idea, phrased for speech) |
| `type` | what kind: `START`, `LEFT`, `SLIGHT_RIGHT`, `RAMP_LEFT`, `EXIT_RIGHT`, `ROUNDABOUT_ENTER`, `DESTINATION`, ... (Valhalla's maneuver types, number + 1; for the icon) |
| `street_names` | the street(s) the maneuver turns onto |
| `roundabout_exit_count` | roundabouts: which exit (1 = first); 0 otherwise |
| `length_meters`, `duration_seconds` | the stretch **after** this maneuver, up to the next one |
| `begin_shape_index`, `end_shape_index` | that stretch in `shape` (indexes into the joined shape, also across via legs) |

So for guidance each maneuver gives the HMI the three things it needs: **a
point (`location`), a radius (`trigger_radius_meters`) and an instruction
(`instruction` / `verbal_pre_transition`)**, plus `type` for an arrow icon.

### The trigger radius

About **15 seconds of driving** at the speed of the approach (the previous
maneuver's stretch: its length / duration), clamped to **30-1000 m**, and
**never longer than that approach** (so it can't fire before the previous
maneuver has happened; very short approaches still get 15 m). The start
maneuver gets 30 m.

| Approach | Radius |
| --- | --- |
| 120 km/h highway | 500 m |
| 100 km/h | ~417 m |
| 50 km/h city street | ~208 m |
| 24 km/h | 100 m |
| slow / short streets | 30 m |
| a 20 m stretch between two turns | 20 m |

Tunable in `src/api/routing.py` (`TRIGGER_SECONDS`, `TRIGGER_MIN_M`,
`TRIGGER_MAX_M`).

### Example (Parliament Hill -> Kanata, real tiles)

```
START_LEFT   r=  30 m  Drive south.
LEFT         r=  62 m  Turn left onto Parliament Road.
RIGHT        r= 162 m  Turn right onto Catherine Street/60.
RAMP_LEFT    r= 154 m  Turn left to take the 417 West ramp.
...
DESTINATION            You have arrived at your destination.
```
23.6 km, 18 min, 418 shape points, 12 maneuvers.

## Using it in the HMI (suggested)

1. Draw `shape` as the route.
2. Keep an index to the **next** maneuver (start at 1: maneuver 0 is the
   start). With each GPS position (gpsd, later): when the distance to the
   next maneuver's `location` drops below its `trigger_radius_meters`, show
   its `instruction` (and arrow for `type`) and speak
   `verbal_pre_transition`; once the car has passed it (distance growing
   again, or within the following maneuver's stretch), move to the next.
3. `DESTINATION` is the arrival.
4. Off route (far from `shape` for a while): ask for a new route from the
   current position.

The HMI's `GuidanceInstruction` (location only today) maps onto a maneuver
with `location`; the radius and texts are the additions.

## Versions: frozen

Routing tiles only load with the Valhalla version that built them, so one
version is used everywhere: **Valhalla 3.9.1**.

| Where | How it's pinned |
| --- | --- |
| this repo | `mapdata.schema.VALHALLA_VERSION` (the freeze); `pyvalhalla==3.9.1` in `src/requirements/base.txt` and `toolkit/requirements.txt` (a test fails if they differ) |
| e39-rpi-yocto | `E39_VALHALLA_VERSION` in `layers/meta-e39rpi-maps/conf/layer.conf` (`PREFERRED_VERSION_valhalla`, versioned `RDEPENDS` of e39maps; the e39maps recipe fails if it differs from `VALHALLA_VERSION`) |
| map database | `valhalla_version` in its facts (the tiles' builder) |

Checks: the toolkit warns when its pyvalhalla, the backend's frozen version
and the yocto recipe disagree (window log, a dialog before building or
updating the image, `WARNING:` lines on the command line); a live swap
asks the device which Valhalla it has and refuses tiles from another
version; the device's installer refuses such a pair too; the backend
answers `UNAVAILABLE` ("tiles were built with Valhalla X, this is Y")
instead of failing obscurely. To change versions: all pins at once, then
rebuild the routing tiles and the image.

The toolkit's container (`toolkit/map-toolkit-container.sh`) installs
exactly the pinned pyvalhalla, so the laptop's own Python packages can't
cause a mismatch.
