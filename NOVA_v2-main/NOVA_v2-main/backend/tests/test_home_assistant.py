"""Home Assistant integration against a MOCKED Home Assistant REST API (no real devices)."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.tools.home_assistant import HomeAssistant, home_tools, parse_entities
from app.tools.registry import PermissionLevel, ToolContext, ToolRegistry


class FakeHA:
    def __init__(self, states: dict[str, list[dict]] | None = None, status: int = 200):
        self.states = states or {}
        self.status = status
        self.services: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer ha-token"
        if self.status != 200:
            return httpx.Response(self.status)
        path = request.url.path
        if path.startswith("/api/states/"):
            entity = path.rsplit("/", 1)[1]
            seq = self.states.get(entity)
            if not seq:
                return httpx.Response(404, json={"message": "Entity not found."})
            state = seq.pop(0) if len(seq) > 1 else seq[0]
            return httpx.Response(200, json={"entity_id": entity, **state})
        if path.startswith("/api/services/"):
            self.services.append((path.removeprefix("/api/services/"), json.loads(request.content)))
            return httpx.Response(200, json=[])
        return httpx.Response(404)


ENTITIES = "light.kitchen, switch.desk_lamp=reversible, switch.heater, lock.front_door=reversible, climate.living, sensor.temp"


def make(fake: FakeHA, entities: str = ENTITIES) -> ToolRegistry:
    ha = HomeAssistant("http://ha.local:8123", "ha-token", parse_entities(entities),
                       transport=httpx.MockTransport(fake), poll_interval=0.01, verify_seconds=0.5)
    return ToolRegistry(home_tools(ha))


def ctx() -> ToolContext:
    return ToolContext(deadline=time.monotonic() + 5)


def light(state: str) -> dict:
    return {"state": state, "attributes": {"friendly_name": "Kitchen light"}}


def test_unconfigured_integration_is_reported_not_pretended():
    reg = ToolRegistry(home_tools(HomeAssistant("", "", {})))
    result = reg.execute("home_control", {"entity_id": "light.kitchen", "action": "turn_on"}, ctx())
    assert result.status == "unavailable" and "NOVA_HOME_ASSISTANT_URL" in result.message


def test_risk_classes_follow_the_device_type_and_owner_can_only_relax_harmless_switches():
    entities = parse_entities(ENTITIES)
    assert entities["light.kitchen"].risk == PermissionLevel.ROUTINE
    assert entities["switch.desk_lamp"].risk == PermissionLevel.ROUTINE  # owner marked it reversible
    assert entities["switch.heater"].risk == PermissionLevel.APPROVAL  # switches are safety-critical by default
    assert entities["lock.front_door"].risk == PermissionLevel.APPROVAL  # locks can never be relaxed
    assert entities["climate.living"].risk == PermissionLevel.APPROVAL
    assert entities["sensor.temp"].risk == PermissionLevel.READ
    assert parse_entities("light.porch=critical")["light.porch"].risk == PermissionLevel.APPROVAL


def test_status_reads_the_reported_state():
    reg = make(FakeHA({"light.kitchen": [light("on")]}))
    result = reg.execute("home_status", {"entity_id": "light.kitchen"}, ctx())
    assert result.status == "succeeded" and result.message == "Kitchen light (light.kitchen) is on."


def test_reversible_control_runs_and_is_verified_from_reported_state():
    fake = FakeHA({"light.kitchen": [light("off"), light("off"), light("on")]})
    result = make(fake).execute("home_control", {"entity_id": "light.kitchen", "action": "turn_on"}, ctx())
    assert result.status == "succeeded" and result.data["verified"] is True
    assert result.message == "Kitchen light is now on."
    assert fake.services == [("light/turn_on", {"entity_id": "light.kitchen"})]


def test_unconfirmed_change_is_not_reported_as_success():
    fake = FakeHA({"light.kitchen": [light("off")]})
    result = make(fake).execute("home_control", {"entity_id": "light.kitchen", "action": "turn_on"}, ctx())
    assert result.status == "unverified" and result.data["verified"] is False


def test_safety_critical_devices_never_run_without_on_screen_approval():
    fake = FakeHA({"lock.front_door": [{"state": "locked", "attributes": {"friendly_name": "Front door"}},
                                        {"state": "unlocked", "attributes": {"friendly_name": "Front door"}}]})
    reg = make(fake)
    args = {"entity_id": "lock.front_door", "action": "unlock"}
    assert reg.permission_for("home_control", args) == PermissionLevel.APPROVAL
    assert reg.execute("home_control", args, ctx()).status == "denied"
    assert fake.services == []
    approved = reg.execute("home_control", args, ctx(), approved=True)
    assert approved.status == "succeeded" and fake.services == [("lock/unlock", {"entity_id": "lock.front_door"})]


def test_arguments_are_checked_against_the_allowlist_device_type_and_safe_bounds():
    fake = FakeHA({"climate.living": [{"state": "heat", "attributes": {"temperature": 20}}]})
    reg = make(fake)
    assert reg.execute("home_control", {"entity_id": "lock.back_door", "action": "unlock"}, ctx()).status == "denied"
    assert reg.execute("home_control", {"entity_id": "sensor.temp", "action": "turn_on"}, ctx()).status == "denied"
    wrong = reg.execute("home_control", {"entity_id": "light.kitchen", "action": "unlock"}, ctx(), approved=True)
    assert wrong.status == "denied" and "light" in wrong.message
    hot = reg.execute("home_control", {"entity_id": "climate.living", "action": "set_temperature", "temperature": 45},
                      ctx(), approved=True)
    assert hot.status == "denied"
    assert fake.services == []


def test_offline_device_unreachable_hub_and_bad_token_are_explained():
    offline = make(FakeHA({"light.kitchen": [light("unavailable")]}))
    result = offline.execute("home_control", {"entity_id": "light.kitchen", "action": "turn_on"}, ctx())
    assert result.status == "unavailable" and "offline" in result.message

    bad_token = make(FakeHA(status=401)).execute("home_status", {"entity_id": "light.kitchen"}, ctx())
    assert bad_token.status == "failed" and "token" in bad_token.message

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    ha = HomeAssistant("http://ha.local:8123", "ha-token", parse_entities(ENTITIES), transport=httpx.MockTransport(down))
    unreachable = ToolRegistry(home_tools(ha)).execute("home_status", {"entity_id": "light.kitchen"}, ctx())
    assert unreachable.status == "failed" and "not reachable" in unreachable.message


@pytest.mark.parametrize("spec", ["light.kitchen;rm -rf", "Light.Kitchen", "light.", "light.kitchen=admin"])
def test_malformed_entity_entries_are_ignored(spec):
    assert parse_entities(spec) == {} or set(parse_entities(spec)) == {"light.kitchen"}
