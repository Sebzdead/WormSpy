"""FLIR / Teledyne Spinnaker camera adapter (ADR 0005/0006). PySpin is imported lazily.

PySpin is not on PyPI: install the `spinnaker_python` wheel that ships with the Spinnaker SDK
into the environment (`uv pip install spinnaker_python-*.whl`).

Each camera is configured by the app on every start, never left to SpinView state:
- fixed exposure and gain (auto off), the channel's pixel format;
- chunk data `FrameID` and `Timestamp`, read per frame;
- an *OldestFirst* stream buffer, so every sensor frame reaches the app in order.

The **clock** camera free-runs at the frame rate and outputs ExposureActive on a GPIO line. The
**follower** fires once per rising edge on its trigger line and keeps its own exposure. The
follower is started (armed) before the clock, so both cameras' first frames belong together;
each camera's frame IDs are counted from its first frame of the run (incomplete images
included). The executor cross-checks pairs by capture time, so a misaligned pairing raises an
alarm instead of passing silently.

Frames are stamped with host time (`time.monotonic`) mapped from the camera's own exposure
timestamp, via a timestamp latch re-taken every few seconds to follow clock drift. This puts
`capture_ts` at mid-exposure rather than at the moment the app happened to read the frame.
Verified manually on the rig, not in CI (docs/rework/rig-bringup.md).
"""

import threading
import time
from collections.abc import Callable

from wormspy.config import CameraConfig
from wormspy.core.cameras import Camera
from wormspy.core.frames import Frame

READ_TIMEOUT_MS = 200
STREAM_BUFFERS = 32
RELATCH_S = 10.0
INSTALL_HINT = ("PySpin is not installed. Install the Spinnaker SDK and its Python wheel "
                "(`uv pip install spinnaker_python-*.whl`), or run with --sim.")


class HardwareError(RuntimeError):
    """The rig's hardware can't be opened as configured; the message says what to fix."""


class FlirCamera(Camera):
    def __init__(self, cam, pyspin, config: CameraConfig,
                 clock: Callable[[], float] = time.monotonic,
                 timeout_ms: int = READ_TIMEOUT_MS, relatch_s: float = RELATCH_S) -> None:
        self._cam, self._ps, self.config = cam, pyspin, config
        self._clock, self._timeout_ms, self._relatch_s = clock, timeout_ms, relatch_s
        self._lock = threading.Lock()
        self._streaming = False
        self._first_id: int | None = None
        self._skipped_before_first = 0             # incomplete frames before the first good one
        self._offset_s: float | None = None        # host time − device time
        self._latched_at = 0.0
        self.incomplete_frames = 0
        if not cam.IsInitialized():
            cam.Init()
        self._configure_common()

    # --- configuration ------------------------------------------------------------------

    def _set(self, node: str, value) -> None:
        """Write a QuickSpin node; string values name an enum entry ("Off" → ExposureAuto_Off)."""
        target = self._cam
        for part in node.split("."):
            target = getattr(target, part)
        if isinstance(value, str):
            value = getattr(self._ps, f"{node.split('.')[-1]}_{value}")
        try:
            target.SetValue(value)
        except self._ps.SpinnakerException as exc:
            raise HardwareError(f"{self.config.name} camera ({self.config.serial}): could not "
                                f"set {node} = {value}: {exc}") from exc

    def _try_set(self, node: str, value) -> None:
        """For nodes that only some models have (e.g. the 3.3 V output enable)."""
        try:
            self._set(node, value)
        except (HardwareError, AttributeError):
            pass

    def _configure_common(self) -> None:
        c = self.config
        self._set("TriggerMode", "Off")
        self._set("AcquisitionMode", "Continuous")
        self._set("PixelFormat", c.pixel_format)
        self._set("ExposureAuto", "Off")
        self._set("ExposureTime", float(c.exposure_us))
        self._set("GainAuto", "Off")
        self._set("Gain", float(c.gain_db))
        self._set("ChunkModeActive", True)
        for chunk in ("FrameID", "Timestamp"):
            self._set("ChunkSelector", chunk)
            self._set("ChunkEnable", True)
        self._set("TLStream.StreamBufferHandlingMode", "OldestFirst")
        self._set("TLStream.StreamBufferCountMode", "Manual")
        self._set("TLStream.StreamBufferCountManual", STREAM_BUFFERS)

    def configure_as_clock(self, fps: float, output_line: str) -> None:
        """Free-run at `fps` and put ExposureActive on `output_line`."""
        self._set("TriggerMode", "Off")
        self._set("AcquisitionFrameRateEnable", True)
        self._set("AcquisitionFrameRate", float(fps))
        self._set("LineSelector", output_line)
        self._set("LineMode", "Output")
        self._set("LineSource", "ExposureActive")
        self._try_set("V3_3Enable", True)       # Blackfly S Line2 needs its 3.3 V pull-up

    def configure_as_follower(self, trigger_line: str) -> None:
        """Expose once per rising edge on `trigger_line`, with this camera's own exposure."""
        self._set("TriggerMode", "Off")          # the trigger source can only change while off
        self._try_set("AcquisitionFrameRateEnable", False)
        self._set("TriggerSelector", "FrameStart")
        self._set("TriggerSource", trigger_line)
        self._set("TriggerActivation", "RisingEdge")
        self._set("TriggerOverlap", "ReadOut")   # accept the next trigger during readout
        self._set("TriggerMode", "On")

    @property
    def shape(self) -> tuple[int, int]:
        return int(self._cam.Height.GetValue()), int(self._cam.Width.GetValue())

    # --- streaming ----------------------------------------------------------------------

    def begin(self) -> None:
        with self._lock:
            if self._streaming:
                return
            self._first_id = None
            self._skipped_before_first = 0
            self._latch()
            self._cam.BeginAcquisition()
            self._streaming = True

    def end(self) -> None:
        self._streaming = False                  # the next read() returns at once
        with self._lock:                         # waits for a read in progress (≤ timeout)
            if self._cam is not None and self._cam.IsStreaming():
                self._cam.EndAcquisition()

    def read(self) -> Frame | None:
        with self._lock:
            if not self._streaming:
                return None
            try:
                image = self._cam.GetNextImage(self._timeout_ms)
            except self._ps.SpinnakerException as exc:
                if getattr(exc, "errorcode", None) == self._ps.SPINNAKER_ERR_TIMEOUT:
                    return None
                raise
            try:
                if image.IsIncomplete():
                    # Its FrameID never arrives, so the pairer records it as a dropped frame.
                    self.incomplete_frames += 1
                    if self._first_id is None:
                        self._skipped_before_first += 1
                    return None
                array = image.GetNDArray().copy()     # the SDK reuses the buffer on Release
                chunk = image.GetChunkData()
                raw_id, device_ts = int(chunk.GetFrameID()), int(chunk.GetTimestamp())
            finally:
                image.Release()
            if self._clock() - self._latched_at > self._relatch_s:
                self._latch()
        if self._first_id is None:
            # Each incomplete image was one sensor frame, so they still count toward the run.
            self._first_id = raw_id - self._skipped_before_first
        return Frame(image=array, device_frame_id=raw_id - self._first_id,
                     capture_ts=self._host_time(device_ts), device_timestamp_ns=device_ts)

    def _latch(self) -> None:
        """Pair the camera's clock with the host's; the midpoint bounds the latency error."""
        try:
            before = self._clock()
            self._cam.TimestampLatch.Execute()
            device_ns = int(self._cam.TimestampLatchValue.GetValue())
            after = self._clock()
        except (self._ps.SpinnakerException, AttributeError, TypeError):
            self._offset_s = None                # fall back to the host time at read
            self._latched_at = self._clock()
            return
        self._offset_s = (before + after) / 2 - device_ns / 1e9
        self._latched_at = after

    def _host_time(self, device_ns: int) -> float:
        if self._offset_s is None:
            return self._clock()
        return self._offset_s + device_ns / 1e9 + self.config.exposure_us / 2e6

    def is_open(self) -> bool:
        return self._cam is not None

    def release(self) -> None:
        if self._cam is None:
            return
        self.end()
        self._cam.DeInit()
        self._cam = None          # PySpin needs every camera reference gone before shutdown


class FlirSystem:
    """The Spinnaker system and its camera list, opened once at startup."""

    def __init__(self, pyspin=None) -> None:
        if pyspin is None:
            try:
                import PySpin as pyspin
            except ImportError as exc:
                raise HardwareError(INSTALL_HINT) from exc
        self._ps = pyspin
        self._system = pyspin.System.GetInstance()
        self._list = self._system.GetCameras()
        self._cameras: list[FlirCamera] = []

    def serials(self) -> list[str]:
        return [str(self._list.GetByIndex(i).TLDevice.DeviceSerialNumber.GetValue())
                for i in range(self._list.GetSize())]

    def camera(self, config: CameraConfig, key: str, **kwargs) -> FlirCamera:
        found = ", ".join(self.serials()) or "none"
        if not config.serial:
            raise HardwareError(f"Set {key}.serial in rig.toml (cameras found: {found}).")
        try:
            cam = self._list.GetBySerial(config.serial)
        except self._ps.SpinnakerException as exc:
            raise HardwareError(f"{key}: no camera with serial {config.serial} "
                                f"(cameras found: {found}).") from exc
        camera = FlirCamera(cam, self._ps, config, **kwargs)
        self._cameras.append(camera)
        return camera

    def close(self) -> None:
        for camera in self._cameras:
            camera.release()
        self._cameras.clear()
        self._list.Clear()
        self._system.ReleaseInstance()
