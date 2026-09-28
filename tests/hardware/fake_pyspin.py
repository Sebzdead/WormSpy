"""A stand-in for the parts of PySpin (Spinnaker's QuickSpin API) that FlirCamera uses.

QuickSpin enum constants are module attributes named `<Node>_<Entry>` (PySpin.TriggerMode_On,
PySpin.PixelFormat_Mono16, …). The fake returns those names as strings, so a test can read
back exactly which entry was set. Every node write is logged in order on the camera.
"""

from collections import deque

import numpy as np

SPINNAKER_ERR_TIMEOUT = -1011


class SpinnakerException(Exception):
    def __init__(self, message: str, errorcode: int = -1001) -> None:
        super().__init__(message)
        self.errorcode = errorcode


class FakePySpin:
    SpinnakerException = SpinnakerException
    SPINNAKER_ERR_TIMEOUT = SPINNAKER_ERR_TIMEOUT

    def __init__(self, cameras=()) -> None:
        self.system = FakeSystem(list(cameras))
        self.System = self                 # PySpin.System.GetInstance()

    def GetInstance(self):
        return self.system

    def __getattr__(self, name: str):
        if "_" in name and not name.startswith("_"):
            return name                    # an enum constant, e.g. "TriggerMode_On"
        raise AttributeError(name)


class FakeSystem:
    def __init__(self, cameras) -> None:
        self.cameras = cameras
        self.released = False

    def GetCameras(self):
        return FakeCameraList(self.cameras)

    def ReleaseInstance(self):
        self.released = True


class FakeCameraList:
    def __init__(self, cameras) -> None:
        self._cameras = cameras
        self.cleared = False

    def GetSize(self):
        return len(self._cameras)

    def GetByIndex(self, index):
        return self._cameras[index]

    def GetBySerial(self, serial):
        for camera in self._cameras:
            if camera.serial == serial:
                return camera
        raise SpinnakerException(f"no camera {serial}")

    def Clear(self):
        self.cleared = True


class Node:
    def __init__(self, camera, name, value=None) -> None:
        self._camera, self._name, self.value = camera, name, value

    def SetValue(self, value):
        self._camera.log.append((self._name, value))
        self.value = value

    def GetValue(self):
        return self.value

    def Execute(self):
        self._camera.log.append((self._name, "execute"))
        if self._name == "TimestampLatch":
            self._camera.TimestampLatchValue.value = self._camera.device_clock_ns


class _Nodes:
    """Attribute access creates (and remembers) a Node, like QuickSpin's node properties."""

    def __init__(self, camera, prefix="") -> None:
        self._camera, self._prefix = camera, prefix

    def __getattr__(self, name):
        node = Node(self._camera, self._prefix + name)
        setattr(self, name, node)
        return node


class FakeChunk:
    def __init__(self, frame_id, timestamp_ns) -> None:
        self._frame_id, self._timestamp = frame_id, timestamp_ns

    def GetFrameID(self):
        return self._frame_id

    def GetTimestamp(self):
        return self._timestamp


class FakeImage:
    def __init__(self, array, frame_id, timestamp_ns, incomplete=False) -> None:
        self._array, self._incomplete = array, incomplete
        self._chunk = FakeChunk(frame_id, timestamp_ns)
        self.released = False

    def IsIncomplete(self):
        return self._incomplete

    def GetImageStatus(self):
        return 7 if self._incomplete else 0

    def GetNDArray(self):
        assert not self.released, "read after Release()"
        return self._array

    def GetChunkData(self):
        return self._chunk

    def Release(self):
        self.released = True
        self._array = np.zeros_like(self._array)   # the SDK reuses the buffer


class FakeCamera(_Nodes):
    def __init__(self, serial, shape=(1200, 1920), dtype=np.uint16) -> None:
        super().__init__(self)
        self.log: list = []
        self.serial = serial
        self.shape, self.dtype = shape, dtype
        self.TLStream = _Nodes(self, "TLStream.")
        self.TLDevice = _Nodes(self, "TLDevice.")
        self.TLDevice.DeviceSerialNumber.value = serial
        self.Width.value, self.Height.value = shape[1], shape[0]
        self.queue: deque = deque()
        self.initialised = self.streaming = False
        self.device_clock_ns = 5_000_000_000
        self.timeouts: list[int] = []

    def Init(self):
        self.initialised = True

    def DeInit(self):
        self.initialised = False

    def IsInitialized(self):
        return self.initialised

    def BeginAcquisition(self):
        self.log.append(("BeginAcquisition", None))
        self.streaming = True

    def EndAcquisition(self):
        self.log.append(("EndAcquisition", None))
        self.streaming = False

    def IsStreaming(self):
        return self.streaming

    def GetNextImage(self, timeout_ms):
        assert self.streaming, "GetNextImage while not streaming"
        self.timeouts.append(timeout_ms)
        if not self.queue:
            raise SpinnakerException("timeout", SPINNAKER_ERR_TIMEOUT)
        item = self.queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    # test helpers
    def push(self, frame_id, timestamp_ns, value=0, incomplete=False):
        image = FakeImage(np.full(self.shape, value, self.dtype), frame_id, timestamp_ns,
                          incomplete)
        self.queue.append(image)
        return image

    def written(self, name):
        """Every value written to a node, in order."""
        return [value for node, value in self.log if node == name]
