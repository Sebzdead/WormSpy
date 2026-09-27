"""Tracking, estimator selection, Hold Focus, event markers, click-to-centre (ADR 0002/0008/0010)."""

from wormspy.control import AppState, TrackingStatus, reduce
from wormspy.control import commands as C
from wormspy.control import effects as X
from wormspy.control import events as E


def _live():
    return reduce(AppState(), C.StartLive()).state


def _recording():
    state = reduce(_live(), E.SessionOpened(path="/data/s")).state
    return reduce(state, C.StartRecording()).state


# --- tracking -----------------------------------------------------------------------------

def test_tracking_is_off_initially_and_needs_live():
    assert AppState().tracking is TrackingStatus.OFF
    assert reduce(AppState(), C.SetTracking(enabled=True)).rejected


def test_enabling_tracking_starts_the_tracker_with_the_selected_estimator():
    t = reduce(_live(), C.SetTracking(enabled=True))
    assert t.state.tracking is TrackingStatus.TRACKING
    assert t.effects == (X.SetTracker(enabled=True, estimator="dark_object",
                                      source_channel="behaviour"),)


def test_toggle_tracking_resolves_against_current_state():
    on = reduce(_live(), C.ToggleTracking()).state
    assert on.tracking is TrackingStatus.TRACKING
    off = reduce(on, C.ToggleTracking())
    assert off.state.tracking is TrackingStatus.OFF
    assert off.effects == (X.SetTracker(enabled=False, estimator="dark_object",
                                        source_channel="behaviour"),)


def test_tracker_reports_update_the_status_only_while_tracking_is_on():
    on = reduce(_live(), C.SetTracking(enabled=True)).state
    lost = reduce(on, E.TrackingStatusChanged(status=TrackingStatus.LOST)).state
    assert lost.tracking is TrackingStatus.LOST
    stale = reduce(_live(), E.TrackingStatusChanged(status=TrackingStatus.LOST)).state
    assert stale.tracking is TrackingStatus.OFF   # a late report can't turn tracking back on


def test_stopping_live_turns_tracking_off():
    on = reduce(_live(), C.SetTracking(enabled=True)).state
    t = reduce(on, C.StopLive())
    assert t.state.tracking is TrackingStatus.OFF
    assert t.effects == (X.SetTracker(enabled=False, estimator="dark_object",
                                      source_channel="behaviour"), X.StopAcquisition())


def test_shutdown_also_disables_a_running_tracker_first():
    on = reduce(_live(), C.SetTracking(enabled=True)).state
    t = reduce(on, C.Shutdown())
    assert t.effects[0] == X.SetTracker(enabled=False, estimator="dark_object",
                                        source_channel="behaviour")
    assert t.effects[-1] == X.StopAcquisition()


def test_estimator_can_only_be_changed_while_tracking_is_off():
    t = reduce(_live(), C.SelectEstimator(name="bright_marker", source_channel="fluorescence"))
    assert (t.state.estimator, t.state.source_channel) == ("bright_marker", "fluorescence")
    on = reduce(t.state, C.SetTracking(enabled=True)).state
    assert reduce(on, C.SelectEstimator(name="dark_object", source_channel="behaviour")).rejected


# --- Hold Focus (ADR 0002) ----------------------------------------------------------------

def test_set_focus_captures_z_via_the_executor():
    t = reduce(AppState(), C.SetFocus())
    assert t.effects == (X.CaptureFocus(),)
    assert reduce(t.state, E.FocusCaptured(z_um=1234.5)).state.focus_z_um == 1234.5


def test_return_to_focus_moves_z_to_the_stored_value():
    assert reduce(AppState(), C.ReturnToFocus()).rejected   # nothing stored yet
    state = reduce(AppState(), E.FocusCaptured(z_um=1234.5)).state
    assert reduce(state, C.ReturnToFocus()).effects == (X.MoveZTo(z_um=1234.5),)


# --- event markers and click-to-centre -------------------------------------------------------

def test_event_markers_are_written_only_while_recording():
    assert reduce(_live(), C.MarkEvent(label="glycerol")).rejected
    t = reduce(_recording(), C.MarkEvent(label="glycerol"))
    assert t.effects == (X.WriteEvent(label="glycerol"),)


def test_centre_on_a_clicked_point_needs_live_and_a_calibration():
    assert reduce(AppState(), C.CentreOn(channel="behaviour", x_px=10, y_px=20)).rejected
    calibrated = reduce(_live(), E.CalibrationCompleted(residual_px={"behaviour": 0.1})).state
    t = reduce(calibrated, C.CentreOn(channel="behaviour", x_px=10, y_px=20))
    assert t.effects == (X.CentreOn(channel="behaviour", x_px=10, y_px=20),)
