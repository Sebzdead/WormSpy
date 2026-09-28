"""The pure state-machine reducer: (state, message) -> Transition.

No I/O happens here. Anything with a side effect is returned as an Effect for the executor to
carry out, and facts from the outside world arrive as Events. A rejected Command leaves the
state unchanged and carries a human-readable reason for the UI to show.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from wormspy.control import commands as C
from wormspy.control import effects as X
from wormspy.control import events as E
from wormspy.control.state import Alarm, AppState, Mode, TrackingStatus
from wormspy.presets import DEFAULT_PRESET, CameraSettings


@dataclass(frozen=True)
class Transition:
    state: AppState
    effects: tuple = ()
    rejected: str | None = None


def _ok(state: AppState, *effects) -> Transition:
    return Transition(state, tuple(effects))


def _reject(state: AppState, reason: str) -> Transition:
    return Transition(state, (), reason)


def _start_live(s: AppState, _: C.StartLive) -> Transition:
    if s.mode is not Mode.IDLE:
        return _reject(s, "Already live.")
    return _ok(replace(s, mode=Mode.LIVE), X.StartAcquisition())


def _disable_tracker(s: AppState) -> tuple:
    """Effects that switch a running tracker off before acquisition stops."""
    if s.tracking is TrackingStatus.OFF:
        return ()
    return (_set_tracker(s, False),)


def _stop_live(s: AppState, _: C.StopLive) -> Transition:
    if s.calibrating:
        return _reject(s, "Wait for the calibration to finish.")
    if s.mode is Mode.RECORDING:
        return _reject(s, "Stop the recording before stopping the live view.")
    if s.mode is Mode.IDLE:
        return _reject(s, "Not live.")
    return _ok(replace(s, mode=Mode.IDLE, tracking=TrackingStatus.OFF),
               *_disable_tracker(s), X.StopAcquisition())


def _new_session(s: AppState, cmd: C.NewSession) -> Transition:
    if s.mode is Mode.RECORDING:
        return _reject(s, "Stop the recording before starting a new session.")
    return _ok(s, X.CreateSession(metadata=cmd.metadata))


def _start_recording(s: AppState, _: C.StartRecording) -> Transition:
    if s.calibrating:
        return _reject(s, "Wait for the calibration to finish.")
    if s.mode is Mode.RECORDING:
        return _reject(s, "Already recording.")
    if s.mode is not Mode.LIVE:
        return _reject(s, "Start the live view before recording.")
    if s.session_dir is None:
        return _reject(s, "Open a session before recording.")
    name = f"rec-{s.next_recording:03d}"
    return _ok(replace(s, mode=Mode.RECORDING, recording_name=name, recording_drops=0),
               X.OpenRecording(session_dir=s.session_dir, name=name))


def _stop_recording(s: AppState, _: C.StopRecording) -> Transition:
    if s.mode is not Mode.RECORDING:
        return _reject(s, "Not recording.")
    return _ok(_end_recording(s, Mode.LIVE), X.CloseRecording())


def _end_recording(s: AppState, mode: Mode) -> AppState:
    """Leave RECORDING. The recording's number is consumed even if it failed, so a partly
    written folder is never reused."""
    if s.mode is not Mode.RECORDING:
        return replace(s, mode=mode)
    return replace(s, mode=mode, recording_name=None, next_recording=s.next_recording + 1)


def _toggle_recording(s: AppState, _: C.ToggleRecording) -> Transition:
    if s.mode is Mode.RECORDING:
        return _stop_recording(s, C.StopRecording())
    return _start_recording(s, C.StartRecording())


def _shutdown(s: AppState, _: C.Shutdown) -> Transition:
    effects = list(_disable_tracker(s))
    if s.mode is Mode.RECORDING:
        effects.append(X.CloseRecording())
    if s.mode is not Mode.IDLE:
        effects.append(X.StopAcquisition())
    return _ok(replace(_end_recording(s, Mode.IDLE), tracking=TrackingStatus.OFF), *effects)


def _session_opened(s: AppState, ev: E.SessionOpened) -> Transition:
    return _ok(replace(s, session_dir=ev.path, next_recording=1))


def _acquiring(s: AppState) -> bool:
    return s.mode is not Mode.IDLE


def _set_tracker(s: AppState, enabled: bool) -> X.SetTracker:
    return X.SetTracker(enabled=enabled, estimator=s.estimator, source_channel=s.source_channel)


def _set_tracking(s: AppState, cmd: C.SetTracking) -> Transition:
    if cmd.enabled and s.calibrating:
        return _reject(s, "Wait for the calibration to finish.")
    if cmd.enabled and not _acquiring(s):
        return _reject(s, "Start the live view before tracking.")
    if cmd.enabled and not s.calibrated:
        return _reject(s, "Calibrate the stage and cameras before tracking.")
    status = TrackingStatus.TRACKING if cmd.enabled else TrackingStatus.OFF
    return _ok(replace(s, tracking=status), _set_tracker(s, cmd.enabled))


def _toggle_tracking(s: AppState, _: C.ToggleTracking) -> Transition:
    return _set_tracking(s, C.SetTracking(enabled=s.tracking is TrackingStatus.OFF))


def _tracking_status_changed(s: AppState, ev: E.TrackingStatusChanged) -> Transition:
    if s.tracking is TrackingStatus.OFF:
        return _ok(s)  # stale report from a tracker that was already switched off
    return _ok(replace(s, tracking=ev.status))


def _select_estimator(s: AppState, cmd: C.SelectEstimator) -> Transition:
    if s.tracking is not TrackingStatus.OFF:
        return _reject(s, "Turn tracking off before changing the estimator.")
    return _ok(replace(s, estimator=cmd.name, source_channel=cmd.source_channel))


def _set_focus(s: AppState, _: C.SetFocus) -> Transition:
    return _ok(s, X.CaptureFocus())


def _focus_captured(s: AppState, ev: E.FocusCaptured) -> Transition:
    return _ok(replace(s, focus_z_um=ev.z_um))


def _return_to_focus(s: AppState, _: C.ReturnToFocus) -> Transition:
    if s.calibrating:
        return _reject(s, "Wait for the calibration to finish.")
    if s.focus_z_um is None:
        return _reject(s, "No focus stored yet. Use Set Focus first.")
    return _ok(s, X.MoveZTo(z_um=s.focus_z_um))


def _mark_event(s: AppState, cmd: C.MarkEvent) -> Transition:
    if s.mode is not Mode.RECORDING:
        return _reject(s, "Event markers can only be placed while recording.")
    return _ok(s, X.WriteEvent(label=cmd.label))


def _centre_on(s: AppState, cmd: C.CentreOn) -> Transition:
    if not _acquiring(s):
        return _reject(s, "Start the live view before centring.")
    if s.calibrating:
        return _reject(s, "Wait for the calibration to finish.")
    if not s.calibrated:
        return _reject(s, "Calibrate the stage and cameras before click-to-centre.")
    return _ok(s, X.CentreOn(channel=cmd.channel, x_px=cmd.x_px, y_px=cmd.y_px))


def _camera_locked(s: AppState) -> str | None:
    """Changing camera settings restarts acquisition, so nothing may depend on it."""
    if s.mode is Mode.RECORDING:
        return "Camera settings are locked while recording."
    if s.calibrating:
        return "Wait for the calibration to finish."
    if s.tracking is not TrackingStatus.OFF:
        return "Turn tracking off before changing camera settings."
    return None


def _preset_named(s: AppState, name: str) -> CameraSettings | None:
    return next((settings for n, settings in s.presets if n == name), None)


def _apply_preset(s: AppState, cmd: C.ApplyPreset) -> Transition:
    if reason := _camera_locked(s):
        return _reject(s, reason)
    settings = _preset_named(s, cmd.name)
    if settings is None:
        return _reject(s, f"There is no preset called {cmd.name!r}.")
    return _ok(s, X.ApplyCameraSettings(settings=settings, preset=cmd.name))


def _apply_camera_settings(s: AppState, cmd: C.ApplyCameraSettings) -> Transition:
    if reason := _camera_locked(s) or cmd.settings.problem():
        return _reject(s, reason)
    # Keep the current preset's name if unchanged, else any preset that matches exactly.
    names = [s.preset] if _preset_named(s, s.preset or "") == cmd.settings else []
    names += [n for n, settings in s.presets if settings == cmd.settings]
    return _ok(s, X.ApplyCameraSettings(settings=cmd.settings,
                                        preset=names[0] if names else None))


def _camera_settings_applied(s: AppState, ev: E.CameraSettingsApplied) -> Transition:
    return _ok(_without_alarm(replace(s, camera_settings=ev.settings, preset=ev.preset),
                              "camera_settings"))


def _camera_settings_failed(s: AppState, ev: E.CameraSettingsFailed) -> Transition:
    return _ok(_with_alarm(s, "camera_settings", f"Camera settings not applied: {ev.reason}"))


def _save_preset(s: AppState, cmd: C.SavePreset) -> Transition:
    name = cmd.name.strip()
    if not name:
        return _reject(s, "Give the preset a name.")
    if name == DEFAULT_PRESET:
        return _reject(s, f"{DEFAULT_PRESET} comes from rig.toml; save under another name.")
    if s.camera_settings is None:
        return _reject(s, "No camera settings have been applied yet.")
    if name in dict(s.presets):
        presets = tuple((n, s.camera_settings if n == name else v) for n, v in s.presets)
    else:
        presets = (*s.presets, (name, s.camera_settings))
    return _ok(replace(s, presets=presets, preset=name), X.WritePresets(presets=presets))


def _delete_preset(s: AppState, cmd: C.DeletePreset) -> Transition:
    if cmd.name == DEFAULT_PRESET:
        return _reject(s, f"{DEFAULT_PRESET} comes from rig.toml and can't be deleted.")
    if _preset_named(s, cmd.name) is None:
        return _reject(s, f"There is no preset called {cmd.name!r}.")
    presets = tuple((n, v) for n, v in s.presets if n != cmd.name)
    preset = None if s.preset == cmd.name else s.preset
    return _ok(replace(s, presets=presets, preset=preset), X.WritePresets(presets=presets))


def _with_alarm(s: AppState, kind: str, message: str) -> AppState:
    """Raise an alarm, replacing any existing alarm of the same kind."""
    others = tuple(a for a in s.alarms if a.kind != kind)
    return replace(s, alarms=(*others, Alarm(kind=kind, message=message)))


def _without_alarm(s: AppState, kind: str) -> AppState:
    return replace(s, alarms=tuple(a for a in s.alarms if a.kind != kind))


def _frames_dropped(s: AppState, ev: E.FramesDropped) -> Transition:
    drops = s.recording_drops + ev.count if s.mode is Mode.RECORDING else s.recording_drops
    s = replace(s, recording_drops=drops)
    return _ok(_with_alarm(s, "frames_dropped",
                           f"{drops} frame(s) dropped in this recording (last: {ev.channel})."))


def _alarm_raised(s: AppState, ev: E.AlarmRaised) -> Transition:
    return _ok(_with_alarm(s, ev.kind, ev.message))


def _alarm_cleared(s: AppState, ev: E.AlarmCleared) -> Transition:
    return _ok(_without_alarm(s, ev.kind))


def _acknowledge_alarm(s: AppState, cmd: C.AcknowledgeAlarm) -> Transition:
    return _ok(_without_alarm(s, cmd.kind))


def _recording_failed(s: AppState, ev: E.RecordingFailed) -> Transition:
    effects = []
    if s.mode is Mode.RECORDING:
        s = _end_recording(s, Mode.LIVE)
        effects.append(X.CloseRecording())   # finalise whatever was written
    return _ok(_with_alarm(s, "recording_failed", f"Recording stopped: {ev.reason}"), *effects)


def _acquisition_failed(s: AppState, ev: E.AcquisitionFailed) -> Transition:
    effects = [*_disable_tracker(s), *([X.CloseRecording()] if s.mode is Mode.RECORDING else [])]
    s = replace(_end_recording(s, Mode.IDLE), tracking=TrackingStatus.OFF)
    return _ok(_with_alarm(s, "acquisition_failed", f"Acquisition stopped: {ev.reason}"),
               *effects)


def _calibrate(s: AppState, _: C.Calibrate) -> Transition:
    if s.calibrating:
        return _reject(s, "Calibration is already running.")
    if s.mode is not Mode.LIVE:
        return _reject(s, "Calibrate from the live view, not while idle or recording.")
    if s.tracking is not TrackingStatus.OFF:
        return _reject(s, "Turn tracking off before calibrating.")
    return _ok(replace(s, calibrating=True), X.RunCalibration())


def _calibration_completed(s: AppState, _: E.CalibrationCompleted) -> Transition:
    return _ok(_without_alarm(replace(s, calibrated=True, calibrating=False),
                              "calibration_failed"))


def _calibration_failed(s: AppState, ev: E.CalibrationFailed) -> Transition:
    return _ok(_with_alarm(replace(s, calibrating=False), "calibration_failed",
                           f"Calibration failed: {ev.reason}"))


_HANDLERS: dict[type, Callable[[AppState, object], Transition]] = {
    C.StartLive: _start_live,
    C.StopLive: _stop_live,
    C.NewSession: _new_session,
    C.StartRecording: _start_recording,
    C.StopRecording: _stop_recording,
    C.ToggleRecording: _toggle_recording,
    C.Shutdown: _shutdown,
    E.SessionOpened: _session_opened,
    C.SetTracking: _set_tracking,
    C.ToggleTracking: _toggle_tracking,
    E.TrackingStatusChanged: _tracking_status_changed,
    C.SelectEstimator: _select_estimator,
    C.SetFocus: _set_focus,
    E.FocusCaptured: _focus_captured,
    C.ReturnToFocus: _return_to_focus,
    C.MarkEvent: _mark_event,
    C.CentreOn: _centre_on,
    C.ApplyPreset: _apply_preset,
    C.ApplyCameraSettings: _apply_camera_settings,
    E.CameraSettingsApplied: _camera_settings_applied,
    E.CameraSettingsFailed: _camera_settings_failed,
    C.SavePreset: _save_preset,
    C.DeletePreset: _delete_preset,
    C.AcknowledgeAlarm: _acknowledge_alarm,
    E.FramesDropped: _frames_dropped,
    E.AlarmRaised: _alarm_raised,
    E.AlarmCleared: _alarm_cleared,
    E.RecordingFailed: _recording_failed,
    E.AcquisitionFailed: _acquisition_failed,
    C.Calibrate: _calibrate,
    E.CalibrationCompleted: _calibration_completed,
    E.CalibrationFailed: _calibration_failed,
}


def reduce(state: AppState, message: object) -> Transition:
    handler = _HANDLERS.get(type(message))
    if handler is None:
        raise TypeError(f"unhandled message {message!r}")
    return handler(state, message)
