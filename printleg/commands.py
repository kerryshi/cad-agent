"""Correct MQTT command payloads where bambulabs_api's are malformed.

bambulabs_api 2.6.6's turn_light_on/off publish {"system": {"led_mode": ...}}
with no `command` field. The firmware silently ignores unrecognized commands,
and the library returns the BROKER ack as success — so the call "returns True
and does nothing" (chased across two days as a storage/dev-mode problem).
The full ledctrl envelope below toggled the P2S chamber light live on
2026-07-21 (raw-publish fork test: off -> on -> restored off).

start_print_3mf is NOT reimplemented here: its project_file envelope is
well-formed in the library; only the light stub is broken. If another
library write "succeeds" without effect, suspect its payload before the
printer (raw-payload-beats-typed-getters, write edition).
"""

from __future__ import annotations

import json
import time

PUSHALL_SETTLE_S = 6.0


def ledctrl_payload(mode: str, node: str = "chamber_light") -> dict:
    """The full documented ledctrl envelope — every field, or it is ignored."""
    if mode not in ("on", "off"):
        raise ValueError(f"mode must be 'on' or 'off', got {mode!r}")
    return {"system": {
        "sequence_id": "0", "command": "ledctrl", "led_node": node,
        "led_mode": mode, "led_on_time": 500, "led_off_time": 500,
        "loop_times": 0, "interval_time": 0,
    }}


def set_chamber_light(printer, mode: str) -> bool:
    """Publish a correct ledctrl and confirm against FRESH pushall state.

    Success means the printer's reported state changed — never the publish
    ack, which only proves the broker heard us (an actor cannot be its own
    verifier; neither can its message bus).
    """
    mq = printer.mqtt_client
    info = mq._client.publish(mq.command_topic, json.dumps(ledctrl_payload(mode)))
    info.wait_for_publish()
    if not info.is_published():
        return False
    mq.pushall()
    time.sleep(PUSHALL_SETTLE_S)
    for node in printer.mqtt_dump().get("print", {}).get("lights_report", []) or []:
        if node.get("node") == "chamber_light":
            return node.get("mode") == mode
    return False
