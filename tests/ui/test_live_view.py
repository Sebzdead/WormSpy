"""LiveView: one channel's image with histogram, fixed levels, LUT, overlays (ADR 0004)."""

import numpy as np
import pytest

from wormspy.core.frames import Frame, FrameHub
from wormspy.ui.live_view import LiveView


def _publish(hub, channel, image, fid):
    hub.publish(channel, Frame(image=image, device_frame_id=fid, capture_ts=fid / 10))


def _view(qtbot, hub, channel="gcamp"):
    view = LiveView(hub, channel=channel, title=channel)
    qtbot.addWidget(view)
    return view


def test_refresh_without_frames_does_nothing(qtbot):
    assert _view(qtbot, FrameHub()).refresh() is False


def test_refresh_shows_each_new_frame_once(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub)
    _publish(hub, "gcamp", np.full((20, 30), 1000, np.uint16), 1)
    assert view.refresh() is True
    assert view.refresh() is False                        # same frame: no redraw
    assert view.image.shape == (20, 30)


def test_16bit_levels_are_set_once_then_never_auto_stretched(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub)
    rng = np.random.default_rng(0)
    _publish(hub, "gcamp", rng.integers(1000, 2000, (20, 30), dtype=np.uint16), 1)
    view.refresh()
    first = view.levels
    assert 900 <= first[0] < first[1] <= 2100
    _publish(hub, "gcamp", rng.integers(20000, 40000, (20, 30), dtype=np.uint16), 2)
    view.refresh()
    assert view.levels == first                           # a brighter frame must look brighter


def test_8bit_views_start_at_the_full_range(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub, "bf")
    _publish(hub, "bf", np.full((20, 30), 120, np.uint8), 1)
    view.refresh()
    assert view.levels == (0.0, 255.0)


def test_auto_levels_on_request_only(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub, "bf")
    _publish(hub, "bf", np.linspace(50, 150, 600).reshape(20, 30).astype(np.uint8), 1)
    view.refresh()
    view.auto_levels()
    lo, hi = view.levels
    assert 45 <= lo < 60 and 140 < hi <= 155


def test_saturated_pixels_are_reported(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub)
    image = np.full((10, 10), 500, np.uint16)
    image[0, :5] = 65535
    _publish(hub, "gcamp", image, 1)
    view.refresh()
    assert view.saturated_fraction == pytest.approx(0.05)
    assert "5.0%" in view.saturation_label.text()


def test_double_click_asks_to_centre_that_pixel(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub)
    _publish(hub, "gcamp", np.zeros((40, 60), np.uint16), 1)
    view.refresh()
    with qtbot.waitSignal(view.centre_requested) as blocker:
        view.request_centre_at_scene(view.image_item.mapToScene(42.5, 17.5))
    assert blocker.args == pytest.approx([42.5, 17.5], abs=0.01)


def test_tracking_overlay_marks_the_tracked_point(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub)
    view.set_tracked_point((12.0, 8.0))
    assert view.tracked_point == (12.0, 8.0)
    view.set_tracked_point(None)
    assert view.tracked_point is None


def test_the_mask_overlay_can_be_toggled(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub)
    mask = np.zeros((20, 30), bool)
    mask[5:10, 5:10] = True
    view.set_mask(mask)
    assert view.mask_visible is False                     # off by default
    view.show_mask(True)
    assert view.mask_visible is True
    assert view.mask_checkbox.isChecked()                 # the checkbox always tells the truth


def test_luts_can_be_switched(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub)
    assert "inferno" in view.available_luts() and "grey" in view.available_luts()
    view.set_lut("inferno")
    assert view.lut_name == "inferno"


def test_first_levels_for_sparse_fluorescence_run_from_background_to_the_neuron(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub)
    rng = np.random.default_rng(1)
    image = rng.normal(1000, 300, (120, 160)).clip(0)
    yy, xx = np.mgrid[:120, :160]
    image += 29000 * np.exp(-((xx - 80) ** 2 + (yy - 60) ** 2) / (2 * 3.0**2))   # a neuron
    image = image.astype(np.uint16)
    image[5, 5] = 65535                                    # a hot pixel must not set the scale
    _publish(hub, "gcamp", image, 1)
    view.refresh()
    lo, hi = view.levels
    assert lo < 1000                                       # background sits near black
    assert 15000 < hi < 40000                              # the neuron isn't clipped...
    # ...and the hot pixel didn't stretch the range to 65535


def test_the_feed_is_locked_to_the_whole_image(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub)
    _publish(hub, "gcamp", np.zeros((120, 192), np.uint16), 1)
    view.refresh()
    assert view._viewbox.state["mouseEnabled"] == [False, False]
    view.show()
    qtbot.waitExposed(view)
    (x0, x1), (y0, y1) = view._viewbox.viewRange()
    assert x0 <= 0.5 and x1 >= 191.5 and y0 <= 0.5 and y1 >= 119.5   # the whole sensor


@pytest.mark.parametrize(("dtype", "top"), [(np.uint8, 255), (np.uint16, 65535)])
def test_histogram_axis_is_the_cameras_dynamic_range_and_cannot_be_moved(qtbot, dtype, top):
    hub = FrameHub()
    view = _view(qtbot, hub)
    _publish(hub, "gcamp", np.full((40, 60), 10, dtype), 1)
    view.refresh()
    vb = view._histogram.item.vb
    assert view.histogram_range == (0, top)
    assert vb.state["mouseEnabled"] == [False, False]
    assert vb.viewRange()[0] == pytest.approx([0, top])
    view.auto_levels()                                    # new levels don't move the axis
    _publish(hub, "gcamp", np.full((40, 60), 200, dtype), 2)
    view.refresh()
    assert vb.viewRange()[0] == pytest.approx([0, top])
    view._histogram.item.region.setRegion((-500, top * 3))  # handles stay inside the range
    lo, hi = view._histogram.item.region.getRegion()
    assert lo >= 0 and hi <= top


def test_histogram_shows_where_the_pixels_sit_on_a_log_scale(qtbot):
    hub = FrameHub()
    view = _view(qtbot, hub, channel="bf")
    image = np.full((40, 60), 30, np.uint8)
    image[:4, :4] = 255                                   # a few clipped pixels
    _publish(hub, "bf", image, 1)
    view.refresh()
    x, y = view._histogram.item.plot.getData()
    assert x[0] == 0 and x[-1] == pytest.approx(256)
    peak_bin = int(np.argmax(y))
    assert x[peak_bin] <= 30 < x[peak_bin + 1]
    assert y[-1] > 0                                      # clipped pixels visible at the top
    assert y.max() == pytest.approx(np.log10(1 + (image[::4, ::4] == 30).sum()))
