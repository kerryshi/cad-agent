"""printleg.commands tests — payload correctness, no printer needed.

The library bug this guards against: a well-formed-looking publish whose
payload the firmware silently ignores, with the broker ack read as success.
The helper must (a) build the full documented envelope and (b) report
success only from observed state, never from the publish ack.
"""

import pytest

from printleg.commands import ledctrl_payload, set_chamber_light


def test_ledctrl_payload_full_envelope():
    p = ledctrl_payload("on")
    s = p["system"]
    # every field the firmware requires; absence = silently ignored command
    assert s["command"] == "ledctrl"
    assert s["led_node"] == "chamber_light"
    assert s["led_mode"] == "on"
    for key in ("sequence_id", "led_on_time", "led_off_time",
                "loop_times", "interval_time"):
        assert key in s


def test_ledctrl_payload_rejects_bad_mode():
    with pytest.raises(ValueError):
        ledctrl_payload("blink")


class _FakeInfo:
    def __init__(self, published):
        self._published = published

    def wait_for_publish(self):
        pass

    def is_published(self):
        return self._published


class _FakePrinter:
    """Publish ack and reported state are set independently on purpose."""

    def __init__(self, published, reported_mode):
        self._reported = reported_mode
        self.published_payloads = []
        outer = self

        class _Paho:
            def publish(self, topic, payload):
                outer.published_payloads.append((topic, payload))
                return _FakeInfo(published)

        class _MQ:
            command_topic = "device/TEST/request"
            _client = _Paho()

            def pushall(self):
                pass

        self.mqtt_client = _MQ()

    def mqtt_dump(self):
        return {"print": {"lights_report": [
            {"node": "chamber_light", "mode": self._reported}]}}


def test_set_chamber_light_success_is_observed_state(monkeypatch):
    import printleg.commands as commands

    monkeypatch.setattr(commands, "PUSHALL_SETTLE_S", 0.0)
    # ack True + state matches -> True
    assert set_chamber_light(_FakePrinter(True, "on"), "on") is True
    # ack True but state did NOT change -> False (the library-bug case)
    assert set_chamber_light(_FakePrinter(True, "off"), "on") is False
    # publish itself failed -> False
    assert set_chamber_light(_FakePrinter(False, "on"), "on") is False
