"""State-machine rules for calibration and click-to-centre (ADR 0009/0010)."""

from wormspy.control import AppState, reduce
from wormspy.control import commands as C
from wormspy.control import effects as X
from wormspy.control import events as E


def _live():
    return reduce(AppState(), C.StartLive()).state


def _calibrating():
    return reduce(_live(), C.Calibrate()).state


def test_calibration_runs_only_while_live_and_not_tracking_or_recording():
    assert reduce(AppState(), C.Calibrate()).rejected
    t = reduce(_live(), C.Calibrate())
    assert t.effects == (X.RunCalibration(),) and t.state.calibrating
    tracking = reduce(_live(), C.SetTracking(enabled=True)).state
    assert reduce(tracking, C.Calibrate()).rejected
    recording = reduce(reduce(_live(), E.SessionOpened(path="/s")).state, C.StartRecording()).state
    assert reduce(recording, C.Calibrate()).rejected


def test_stage_moving_commands_wait_while_calibrating():
    state = _calibrating()
    for command in (C.Calibrate(), C.SetTracking(enabled=True), C.StartRecording(),
                    C.CentreOn(channel="bf", x_px=1, y_px=2), C.ReturnToFocus(),
                    C.StopLive()):
        assert reduce(state, command).rejected, command


def test_completed_calibration_enables_click_to_centre():
    assert reduce(_live(), C.CentreOn(channel="bf", x_px=1, y_px=2)).rejected   # not calibrated
    state = reduce(_calibrating(), E.CalibrationCompleted(residual_px={"bf": 0.1})).state
    assert state.calibrated and not state.calibrating
    t = reduce(state, C.CentreOn(channel="bf", x_px=1, y_px=2))
    assert t.effects == (X.CentreOn(channel="bf", x_px=1, y_px=2),)


def test_failed_calibration_raises_an_alarm_and_keeps_the_previous_calibration():
    state = reduce(_live(), E.CalibrationCompleted(residual_px={"bf": 0.1})).state   # loaded
    state = reduce(state, C.Calibrate()).state
    state = reduce(state, E.CalibrationFailed(reason="no texture under the objective")).state
    assert state.calibrated and not state.calibrating
    assert any(a.kind == "calibration_failed" and "texture" in a.message for a in state.alarms)


def test_a_successful_calibration_clears_an_earlier_failure_alarm():
    state = reduce(_calibrating(), E.CalibrationFailed(reason="x")).state
    state = reduce(reduce(state, C.Calibrate()).state,
                   E.CalibrationCompleted(residual_px={"bf": 0.1})).state
    assert not any(a.kind == "calibration_failed" for a in state.alarms)
