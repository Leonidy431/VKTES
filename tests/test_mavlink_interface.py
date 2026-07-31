"""Тесты MAVLinkInterface — обёртка связи с полётным контроллером."""

import pytest

from blueos.mavlink_interface import (
    MAVLinkInterface,
    DroneTelemetry,
    GroundStationTelemetry,
)
from blueos import config


@pytest.fixture
def mav():
    return MAVLinkInterface()


# ---------------------------------------------------------------------------
# Init / properties
# ---------------------------------------------------------------------------

def test_default_connection_string(mav):
    assert mav._connection_string == config.MAVLINK_CONNECTION


def test_custom_connection_string():
    m = MAVLinkInterface("udp:127.0.0.1:9999")
    assert m._connection_string == "udp:127.0.0.1:9999"


def test_not_connected_initially(mav):
    assert mav.is_connected is False


def test_drone_telemetry_default(mav):
    assert isinstance(mav.drone, DroneTelemetry)
    assert mav.drone.armed is False


def test_ground_station_telemetry_default(mav):
    assert isinstance(mav.ground_station, GroundStationTelemetry)
    assert mav.ground_station.tether_connected is False


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------

def test_connect_succeeds(mav):
    result = mav.connect()
    assert result is True
    assert mav.is_connected is True


def test_disconnect(mav):
    mav.connect()
    mav.disconnect()
    assert mav.is_connected is False


# ---------------------------------------------------------------------------
# arm / disarm
# ---------------------------------------------------------------------------

def test_arm_without_connection_fails(mav):
    result = mav.arm()
    assert result is False
    assert mav.drone.armed is False


def test_arm_with_connection_succeeds(mav):
    mav.connect()
    result = mav.arm()
    assert result is True
    assert mav.drone.armed is True


def test_disarm(mav):
    mav.connect()
    mav.arm()
    result = mav.disarm()
    assert result is True
    assert mav.drone.armed is False


# ---------------------------------------------------------------------------
# set_mode
# ---------------------------------------------------------------------------

def test_set_mode(mav):
    result = mav.set_mode("GUIDED")
    assert result is True
    assert mav.drone.mode == "GUIDED"


# ---------------------------------------------------------------------------
# takeoff
# ---------------------------------------------------------------------------

def test_takeoff_without_arm_fails(mav):
    result = mav.takeoff(10.0)
    assert result is False


def test_takeoff_with_arm_succeeds(mav):
    mav.connect()
    mav.arm()
    result = mav.takeoff(10.0)
    assert result is True


# ---------------------------------------------------------------------------
# land
# ---------------------------------------------------------------------------

def test_land_sets_land_mode(mav):
    result = mav.land()
    assert result is True
    assert mav.drone.mode == "LAND"


# ---------------------------------------------------------------------------
# goto / goto_local
# ---------------------------------------------------------------------------

def test_goto(mav):
    assert mav.goto(55.75, 37.61, 10.0) is True


def test_goto_local(mav):
    assert mav.goto_local(1.0, 2.0, -3.0) is True


def test_goto_local_default_speed(mav):
    assert mav.goto_local(0.0, 0.0, -1.0) is True


# ---------------------------------------------------------------------------
# set_ground_steering
# ---------------------------------------------------------------------------

def test_set_ground_steering(mav):
    assert mav.set_ground_steering(50.0, 10.0) is True


# ---------------------------------------------------------------------------
# send_gs_command
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "START_CHARGE", "STOP_CHARGE", "ENABLE_BOOST", "DISABLE_BOOST", "EMERGENCY_STOP",
])
def test_send_gs_command(mav, cmd):
    assert mav.send_gs_command(cmd) is True


# ---------------------------------------------------------------------------
# update_telemetry (stub)
# ---------------------------------------------------------------------------

def test_update_telemetry_does_not_raise(mav):
    mav.update_telemetry()  # заглушка, просто не должна падать
