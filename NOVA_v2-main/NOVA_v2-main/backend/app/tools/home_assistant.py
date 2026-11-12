"""Smart-home and physical devices through a local Home Assistant hub (REST API).

NOVA controls only the entities the owner lists in NOVA_HOME_ASSISTANT_ENTITIES. Microcontrollers and other hardware
reach NOVA the same way (for example ESPHome devices added to Home Assistant); NOVA has no other device integration.

Risk classes:
- observe: sensors and binary sensors (read only).
- reversible: lights, fans, media players, scenes — and switches the owner explicitly marks "=reversible".
- safety-critical: locks, covers (garage doors, gates, blinds), climate, water heaters, valves, sirens, humidifiers and,
  by default, switches (a switch can power a heater or a machine). These are queued for on-screen approval; the
  owner cannot relax them, except switches.
Every change is verified from the state Home Assistant reports afterwards. Physical devices keep their own manual
controls; NOVA never disables them, and the emergency stop pauses all device control.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import Field, create_model

from app.tools.registry import PermissionLevel, Tool, ToolContext, ToolInput

NOT_CONFIGURED = ("Home Assistant is not configured. Set NOVA_HOME_ASSISTANT_URL, NOVA_HOME_ASSISTANT_TOKEN and "
                  "NOVA_HOME_ASSISTANT_ENTITIES in .env and restart NOVA (see docs/ACTIONS.md).")

OBSERVE_DOMAINS = {"sensor", "binary_sensor"}
REVERSIBLE_DOMAINS = {"light", "fan", "media_player", "scene"}
RELAXABLE_DOMAINS = {"switch", "input_boolean"}  # critical unless the owner marks a specific one reversible
CRITICAL_DOMAINS = {"lock", "cover", "climate", "water_heater", "valve", "siren", "humidifier"}

ACTIONS: dict[str, dict[str, tuple[str, str | None]]] = {  # domain -> action -> (service, expected state)
    "light": {"turn_on": ("turn_on", "on"), "turn_off": ("turn_off", "off"), "toggle": ("toggle", None)},
    "fan": {"turn_on": ("turn_on", "on"), "turn_off": ("turn_off", "off"), "toggle": ("toggle", None)},
    "switch": {"turn_on": ("turn_on", "on"), "turn_off": ("turn_off", "off"), "toggle": ("toggle", None)},
    "input_boolean": {"turn_on": ("turn_on", "on"), "turn_off": ("turn_off", "off"), "toggle": ("toggle", None)},
    "media_player": {"turn_on": ("turn_on", "on"), "turn_off": ("turn_off", "off")},
    "siren": {"turn_on": ("turn_on", "on"), "turn_off": ("turn_off", "off")},
    "humidifier": {"turn_on": ("turn_on", "on"), "turn_off": ("turn_off", "off")},
    "water_heater": {"turn_on": ("turn_on", None), "turn_off": ("turn_off", "off")},
    "scene": {"turn_on": ("turn_on", None)},
    "lock": {"lock": ("lock", "locked"), "unlock": ("unlock", "unlocked")},
    "cover": {"open": ("open_cover", "open"), "close": ("close_cover", "closed"), "stop": ("stop_cover", None)},
    "valve": {"open": ("open_valve", "open"), "close": ("close_valve", "closed"), "stop": ("stop_valve", None)},
    "climate": {"set_temperature": ("set_temperature", None), "turn_off": ("turn_off", "off"),
                "turn_on": ("turn_on", None)},
}
MOVING = {"open": "opening", "closed": "closing"}  # covers and valves report these while they move
CLIMATE_RANGE = (5.0, 30.0)  # °C: a safe band for heating and cooling set points
_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


@dataclass(frozen=True)
class Entity:
    entity_id: str
    risk: PermissionLevel

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


def parse_entities(spec: str) -> dict[str, Entity]:
    """"light.kitchen, switch.desk_lamp=reversible, light.porch=critical". Unknown domains and bad entries are ignored."""
    out: dict[str, Entity] = {}
    for raw in re.split(r"[,;\n]", spec):
        entity_id, _, mark = raw.strip().lower().partition("=")
        entity_id, mark = entity_id.strip(), mark.strip()
        if not _ENTITY.match(entity_id) or mark not in ("", "reversible", "critical"):
            continue
        domain = entity_id.split(".", 1)[0]
        if domain in OBSERVE_DOMAINS:
            risk = PermissionLevel.READ
        elif domain in CRITICAL_DOMAINS:
            risk = PermissionLevel.APPROVAL
        elif domain in RELAXABLE_DOMAINS:
            risk = PermissionLevel.ROUTINE if mark == "reversible" else PermissionLevel.APPROVAL
        elif domain in REVERSIBLE_DOMAINS:
            risk = PermissionLevel.APPROVAL if mark == "critical" else PermissionLevel.ROUTINE
        else:
            continue
        out[entity_id] = Entity(entity_id, risk)
    return out


class HAProblem(Exception):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class HomeAssistant:
    def __init__(self, url: str, token: str, entities: dict[str, Entity], *, transport: httpx.BaseTransport | None = None,
                 poll_interval: float = 0.5, verify_seconds: float = 5.0) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.entities = entities
        self.poll_interval = poll_interval
        self.verify_seconds = verify_seconds
        self.client = httpx.Client(transport=transport, follow_redirects=False)

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token and self.entities)

    def close(self) -> None:
        self.client.close()

    def _call(self, ctx: ToolContext, method: str, path: str, **kwargs: Any) -> Any:
        ctx.check()
        try:
            response = self.client.request(method, self.url + path, headers={"Authorization": f"Bearer {self.token}"},
                                           timeout=max(1.0, min(10.0, ctx.remaining())), **kwargs)
        except httpx.HTTPError:
            raise HAProblem("failed", f"Home Assistant is not reachable at {self.url}. Check that it is running and on "
                            "this network.") from None
        if response.status_code == 401:
            raise HAProblem("failed", "Home Assistant rejected NOVA's access token. Create a new long-lived token and "
                            "update NOVA_HOME_ASSISTANT_TOKEN.")
        if response.status_code == 404:
            raise HAProblem("unavailable", "Home Assistant does not know that device. Check the entity ID.")
        if response.status_code >= 400:
            raise HAProblem("failed", f"Home Assistant refused the request (HTTP {response.status_code}).")
        return response.json() if response.content else {}

    def state(self, entity_id: str, ctx: ToolContext) -> dict[str, Any]:
        value = self._call(ctx, "GET", f"/api/states/{entity_id}")
        return value if isinstance(value, dict) else {}

    def status(self, entity_id: str, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        st = self.state(entity_id, ctx)
        name = (st.get("attributes") or {}).get("friendly_name") or entity_id
        return "succeeded", f"{name} ({entity_id}) is {st.get('state', 'unknown')}.", {
            "entity_id": entity_id, "state": st.get("state"), "verified": True}

    def control(self, entity: Entity, action: str, temperature: float | None,
                ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        services = ACTIONS.get(entity.domain, {})
        if action not in services:
            allowed = ", ".join(services) or "none"
            return "denied", f"A {entity.domain} cannot do '{action}'. Possible actions: {allowed}.", {"reason": "bad_action"}
        service, expected = services[action]
        body: dict[str, Any] = {"entity_id": entity.entity_id}
        if action == "set_temperature":
            low, high = CLIMATE_RANGE
            if temperature is None or not low <= temperature <= high:
                return ("denied", f"NOVA only sets temperatures between {low:g} and {high:g} °C.",
                        {"reason": "out_of_bounds"})
            body["temperature"] = temperature
        before = self.state(entity.entity_id, ctx)
        name = (before.get("attributes") or {}).get("friendly_name") or entity.entity_id
        if before.get("state") in ("unavailable", "unknown", None):
            return "unavailable", f"{name} is offline in Home Assistant, so NOVA did not send the command.", {"verified": False}
        if action == "toggle":
            expected = {"on": "off", "off": "on"}.get(str(before.get("state")))
        self._call(ctx, "POST", f"/api/services/{entity.domain}/{service}", json=body)

        def reached(st: dict[str, Any]) -> bool:
            if action == "set_temperature":
                return (st.get("attributes") or {}).get("temperature") == temperature
            return st.get("state") in (expected, MOVING.get(expected or ""))

        if expected is None and action != "set_temperature":
            return ("succeeded", f"Sent {action.replace('_', ' ')} to {name}. This device does not report a state to "
                    "confirm it.", {"verified": False, "entity_id": entity.entity_id})
        until = min(ctx.deadline - 0.2, time.monotonic() + self.verify_seconds)
        while True:
            st = self.state(entity.entity_id, ctx)
            if reached(st):
                now = st.get("state")
                detail = f"set to {temperature:g} °C" if action == "set_temperature" else f"now {now}"
                return "succeeded", f"{name} is {detail}.", {"verified": True, "entity_id": entity.entity_id, "state": now}
            if time.monotonic() + self.poll_interval >= until:
                return ("unverified", f"Home Assistant accepted the command, but {name} still reports "
                        f"'{st.get('state')}'.", {"verified": False, "entity_id": entity.entity_id})
            ctx.sleep(self.poll_interval)


def home_tools(ha: HomeAssistant) -> list[Tool]:
    on = ha.configured
    everything = tuple(sorted(ha.entities)) or ("none",)
    controllable = tuple(sorted(e for e, v in ha.entities.items() if v.risk != PermissionLevel.READ)) or ("none",)
    actions = tuple(sorted({a for d in ACTIONS.values() for a in d}))

    StatusInput = create_model("HomeStatusInput", __base__=ToolInput,
                               entity_id=(Literal[everything], Field(description="Device to read.")))  # type: ignore[valid-type]
    ControlInput = create_model(
        "HomeControlInput", __base__=ToolInput,
        entity_id=(Literal[controllable], Field(description="Device to control.")),  # type: ignore[valid-type]
        action=(Literal[actions], Field(description="What to do; must suit the device type.")),  # type: ignore[valid-type]
        temperature=(float | None, Field(default=None, description="°C, only for set_temperature.")),
    )

    def guarded(fn: Any) -> Any:
        def run(args: Any, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
            try:
                return fn(args, ctx)
            except HAProblem as problem:
                return problem.status, problem.message, {"verified": False}
        return run

    def classify(args: Any) -> PermissionLevel:
        entity = ha.entities.get(args.entity_id)
        return entity.risk if entity else PermissionLevel.APPROVAL

    kinds = {PermissionLevel.READ: "observe", PermissionLevel.ROUTINE: "reversible", PermissionLevel.APPROVAL: "needs approval"}
    listing = ", ".join(f"{e} ({v.domain}, {kinds[v.risk]})" for e, v in sorted(ha.entities.items())) or "none"
    return [
        Tool("home_status", f"Read the current state of a smart-home device through Home Assistant. Devices: {listing}.",
             StatusInput, PermissionLevel.READ, on, guarded(lambda a, c: ha.status(a.entity_id, c)), timeout_s=12,
             unavailable_reason=NOT_CONFIGURED),
        Tool("home_control", "Control a smart-home device through Home Assistant (lights, fans, switches, locks, covers, "
             "climate). Safety-critical devices are queued for the owner's on-screen approval and the result is checked "
             f"from the device's reported state. Devices: {listing}.", ControlInput, PermissionLevel.ROUTINE, on,
             guarded(lambda a, c: ha.control(ha.entities[a.entity_id], a.action, a.temperature, c)), timeout_s=20,
             unavailable_reason=NOT_CONFIGURED, classify=classify),
    ]
