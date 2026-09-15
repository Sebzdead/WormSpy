import cv2
import math
import time
import copy
import pytz
import queue
import pygame
import pathlib
import threading
import EasyPySpin
from pypylon import pylon
import numpy as np
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from zaber_motion import Library, Units
from plot_worm_path import plot_worm_path
from flask_cors import CORS, cross_origin
from skimage.filters import threshold_yen
from skimage.measure import label, regionprops
from zaber_motion.ascii import Connection, Device
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

app = Flask(__name__, template_folder='production\\templates',
            static_url_path='/static',
            static_folder='production\\static')
CORS(app, origins=['http://localhost:4200', 'http://localhost:5000'])
app.config['CORS_HEADERS'] = 'Content-Type'

# Start the Flask app locally on host IP address 127.0.0.1 for testing
# python -m flask run --host=127.0.0.1

# SET UP CONFIGURATION
XYmotorport = 'COM6' # Will vary depending on the computer
Zmotorport = 'COM3' # Will vary depending on the computer
TOTAL_MM_X = 1.92  # total width of the FOV in mm (measure)
TOTAL_MM_Y = 1.2  # total height of the FOV in mm (measure)
MM_MST = 20997  # millimeters per microstep
# Zaber device boundaries
MAXIMUM_DEVICE_XY_POSITION = 1066667 # initialized, wil be updated by the motors
MAXIMUM_DEVICE_Z_POSITION = 209974 # may vary if using different motors
MINIMUM_DEVICE_POSITION = 0
ZABER_ORIENTATION_X = 1 # whether the x direction of the zaber is inverted from the video feed (-1 if they are inverted)
ZABER_ORIENTATION_Y = -1 # whether the y direction of the zaber is inverted from the video feed (-1 if they are inverted)
hist_frame = np.zeros((1200, 1920, 1), dtype=np.uint8) #change to match frame size of the right camera feed (y,x,1)

# settings for the recording
FPS = 10 # Brightfield value: MUST MANUALLY CHANGE :( depending on camera model you could detect it from the camera
timeZone = pytz.timezone("US/Eastern")
settings = {
    "filepath": str(pathlib.Path.home() / 'WormSpy_video'),
    "filename": 'default',
    "use_avi_l": True, # Set to True if you want to record the left camera as a compressed avi file, False if you want to record as uncompressed tiff files
    "use_avi_r": True, # Set to True if you want to record the right camera as a compressed avi file, False if you want to record as uncompressed tiff files
}

from core.frames import FrameHub
from core.cameras_spinnaker import SpinnakerCamera
from core.stage_zaber import connect_stage
from core.acquisition import AcquisitionEngine
import threading as _threading

# Shared singletons constructed once at startup (replaces per-request setup).
HUB = FrameHub()
STAGE = connect_stage(XYmotorport, Zmotorport)   # NullStage if no hardware
ENGINE = None            # set when the live feed starts (cameras chosen in the UI)
ACQ_THREAD = None

def _start_engine(left_id, right_id):
    global ENGINE, ACQ_THREAD
    left = SpinnakerCamera(left_id, frame_rate=FPS, exposure_us=10000)
    right = SpinnakerCamera(right_id, frame_rate=FPS, exposure_us=10000)
    ENGINE = AcquisitionEngine(left, right, HUB, sink=lambda pair: None)  # recorder: Phase 2
    ACQ_THREAD = _threading.Thread(target=ENGINE.run, daemon=True)
    ACQ_THREAD.start()

# DLC Live Settings:
# from dlclive import DLCLive, Processor
# dlc_proc = Processor() # Import the DLC NN model
dlc_live = None # remove this line if using DLC
# dlc_live = DLCLive(r'C:\Users\User\Documents\WormSpy\DLC_models/3-node',
#                    processor=dlc_proc, display=False)

# Global Variables (leave alone)
stop_stream = False
is_recording = False
start_recording = False
stop_recording = False
start_recording_r = False
stop_recording_r = False
af_enabled = False
start_af = False
heatmap_enabled = False
track_algorithm = 0
is_tracking = False
nodeIndex = 0
mutex = Lock()
isManualEnabled = False
hist_max = 0
leftCam = None # leave as None, will be set by the user in the UI
rightCam = None # leave as None, will be set by the user in the UI
frame_queue_left = queue.Queue() # Initialize the queue
frame_queue_right = queue.Queue() # Initialize the queue 
Library.enable_device_db_store() # Initialize the device database for Zaber communication

def tiff_writer(project_path, frame_queue):
    while True:
        dt, frame = frame_queue.get()
        if frame is None:
            break
        frame_name = f"frame_{dt}.tiff"
        cv2.imwrite(str(project_path / frame_name), frame)

# Add route to serve Angular app
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
@cross_origin()
def serve_angular(path):
    if path and Path(app.static_folder + '/' + path).exists():
        return send_from_directory(app.static_folder, path)
    return render_template('index.html')

def _mjpeg_from_hub(stream):
    def gen():
        while True:
            frame = HUB.latest(stream)
            if frame is None:
                time.sleep(0.03)
                continue
            ok, buf = cv2.imencode('.jpg', frame.image)   # jpeg matches the declared mimetype
            if ok:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
            time.sleep(0.03)
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@cross_origin()
@app.route('/video_feed')
def video_feed():
    return _mjpeg_from_hub("left")

@cross_origin()
@app.route('/video_feed_fluorescent')
def video_feed_fluorescent():
    return _mjpeg_from_hub("right")

@cross_origin()
@app.route("/get_hist")
def get_hist():
    def gen():
        global hist_frame
        current_frame = np.zeros((1200, 1920, 1), dtype=np.uint8)
        first = True
        while True: 
            if first:
                first = False
            if hist_frame is not None:
                current_frame = hist_frame
            current_frame = hist_frame
            if current_frame is not None:
                hist_size = 256
                hist_w = 512
                hist_h = 400
                bin_w = int(round(hist_w / hist_size))
                hist = cv2.calcHist(current_frame, [0], None, [hist_size], (0, 256), accumulate=False)
                norm_hist = cv2.normalize(hist, hist, 0, hist_h, cv2.NORM_MINMAX)
                histImage = np.zeros((hist_h, hist_w, 3), dtype=np.uint8)
                for i in range(1, hist_size):
                    cv2.line(histImage, (bin_w * (i - 1), hist_h - int(norm_hist[i - 1])),
                             (bin_w * (i), hist_h - int(norm_hist[i])), (0, 255, 0), thickness=2)
                ret, image_data = cv2.imencode('.png', histImage)
                png = image_data.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + png + b'\r\n')
            else:
                # If the frame was not successfully read, yield a blank frame
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n\r\n')
    # Return the video feed as a multipart/x-mixed-replace response
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@cross_origin()
@app.route('/stream_max')
def stream_max():
    def gen():
        global hist_max
        yield str(hist_max)
    return Response(gen(), mimetype='text/plain')

@cross_origin()
@app.route("/start_recording", methods=['POST'])
def start_record():
    global start_recording, start_recording_r, settings
    # Update the settings with the data from the request body
    try:
        settings["filepath"] = request.json["filepath"]
        settings["filename"] = request.json["filename"]
        settings['use_avi_l'] = request.json['use_avi_left']
        settings['use_avi_r'] = request.json['use_avi_right']
    except KeyError as e:
        settings['use_avi_l'] = True
        settings['use_avi_r'] = True
        print("Using previous or default filepath and filename of this session for this recording.\nTo avoid this, please set the filepath and filename in the UI and start the recording via the UI.")
    
    # Set the recording flag to True
    start_recording = True
    start_recording_r = True
    # return jsonify({"message": "Recording started"})
    return str(settings)

@cross_origin()
@app.route("/stop_recording", methods=['POST'])
def stop_record():
    global stop_recording, stop_recording_r
    # Stop the recording of both video feeds
    stop_recording = True
    stop_recording_r = True
    return jsonify({"message": "Recording stopped"})

@cross_origin()
@app.route("/toggle_heatmap", methods=['POST']) ##################### FIX THIS
def heatmap():
    global heatmap_enable, heatmap_enabled
    heatmap_enabled = request.json['heatmap_enabled'] == "True"
    return str(heatmap_enabled)

@cross_origin()
@app.route("/stop_live_stream", methods=['POST'])
def stop_live_stream():
    global stop_stream
    # Stop both video feeds
    stop_stream = True
    return jsonify({"message": "Streams stopped"})

@cross_origin()
@app.route("/camera_settings", methods=['POST'])
def camera_settings():
    leftCam = request.json['leftCam']
    rightCam = request.json['rightCam']
    _start_engine(leftCam, rightCam)
    return jsonify({"message": "Engine started"})

@cross_origin()
@app.route("/node_index", methods=['POST'])
def node_index():
    global nodeIndex
    # Set the camera settings before starting the video feeds
    nodeIndex = request.json['index']
    return jsonify({f"message": "NodeIndex Recieved: {nodeIndex}"})

@cross_origin()
@app.route("/toggle_tracking", methods=['POST'])
def toggle_tracking():
    global is_tracking, track_algorithm
    is_tracking = request.json['is_tracking'] == "True"
    track_algorithm = int(request.json['tracking_algorithm'])
    return str(is_tracking)

@cross_origin()
@app.route("/toggle_af", methods=['POST'])
def toggle_af():
    global af_enabled, af_thread
    af_request = request.json['af_enabled'] == "True"
    if af_request and not af_enabled:
        start_autofocus()
    elif not af_request and af_enabled:
        af_enabled = False
        af_thread.join()
    return str(af_enabled)

@cross_origin()
@app.route("/toggle_manual", methods=['POST'])
def toggle_manual():
    global isManualEnabled
    manual_mode = request.json['toggle_manual'] == "True"
    if isManualEnabled and not manual_mode: # Turning off manual mode
        isManualEnabled = False
    elif not isManualEnabled and manual_mode: # Turning on manual mode and starting controller
        isManualEnabled = True
        start_controller()
    return str(manual_mode)

@cross_origin()
@app.route("/move_to_center", methods=['POST'])
def move_to_center():
    lo_x, hi_x = STAGE.limits("x")
    lo_y, hi_y = STAGE.limits("y")
    STAGE.move_absolute("x", (lo_x + hi_x) / 2)
    STAGE.move_absolute("y", (lo_y + hi_y) / 2)
    return jsonify({"message": "Moved to center"})

def calculate_focus_metric(frame):
    """
    Calculate a focus metric using the variance of the Laplacian.
    A higher variance indicates a sharper image.
    """
    laplacian = cv2.Laplacian(frame, cv2.CV_64F)
    return laplacian.var()

class PIDController:
    def __init__(self, kp, ki, kd, setpoint):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.setpoint = setpoint
        self.integral = 0.0
        self.prev_error = 0.0

    def compute(self, measurement, dt):
        error = self.setpoint - measurement
        self.integral += error * dt
        derivative = (error - self.prev_error) / dt if dt > 0 else 0.0
        output = self.kp * error + self.ki * self.integral + self.kd * derivative
        self.prev_error = error
        return output

def continuous_autofocus(optimal_z, optimal_focus_metric, frame_interval=0.2):
    """
    Continuously adjusts the zMotor based on the focus metric from the latest frame.
    Updates at most 5 times per second.
    """
    global af_enabled, zMotor, latest_frame
    pid = PIDController(kp=0.001, ki=0, kd=0.001, setpoint=optimal_focus_metric)
    current_z = optimal_z
    af_enabled = True
    while af_enabled:
        start_time = time.time()
        if latest_frame is None:
            time.sleep(frame_interval)
            continue
        metric = calculate_focus_metric(latest_frame)
        adjustment = pid.compute(metric, frame_interval)
        max_step = 20  # Maximum z movement per iteration (adjust as needed)
        adjustment = max(-max_step, min(max_step, adjustment))
        new_z = current_z + adjustment
        print("Focus metric:", metric, "Adjustment:", adjustment, "New z:", new_z)
        try:
            zMotor.move_absolute(new_z, unit=Units.NATIVE, wait_until_idle=True)
        except Exception as e:
            print("Autofocus: Error moving zMotor:", e)
        current_z = new_z
        elapsed = time.time() - start_time
        if elapsed < frame_interval:
            time.sleep(frame_interval - elapsed)
    print("Autofocus stopped.")

def start_autofocus():
    """
    Initializes autofocus by capturing the current frame and zMotor position,
    then starting the continuous autofocus loop in a daemon thread.
    """
    global af_enabled, zMotor, latest_frame, af_thread
    if latest_frame is None:
        print("No frame available to initialize autofocus.")
        return
    optimal_z = zMotor.get_position(unit=Units.NATIVE)
    optimal_focus_metric = calculate_focus_metric(latest_frame)
    print("Autofocus started with optimal_z =", optimal_z, "and optimal_focus_metric =", optimal_focus_metric)
    af_thread = threading.Thread(target=continuous_autofocus, args=(optimal_z, optimal_focus_metric))
    af_thread.daemon = True
    af_thread.start()

def smoothing(worm_coords, previous_worm_coords): # exponential smoothing function. Alpha determines how much smoothing is desirable.
    alpha = 0.7
    new_stage_x = previous_worm_coords[0] * alpha + worm_coords[0] * (1 - alpha)  
    new_stage_y = previous_worm_coords[1] * alpha + worm_coords[1] * (1 - alpha)
    # print new smoothed coords
    print("X: " + str(new_stage_x) + " Y: " + str(new_stage_y))
    return (new_stage_x, new_stage_y)

def simpleToCenter(centroidX, centroidY, resolution):
    # calculate the percent the position is from the edge of the frame
    percentX = float(centroidX) / float(resolution[0])
    percentY = float(centroidY) / float(resolution[1])
    # millimeters the position is from the edge
    millisX = percentX * TOTAL_MM_X
    millisY = percentY * TOTAL_MM_Y
    # millimeters the stage needs to move to catch up to the worm's position
    millisMoveX = ZABER_ORIENTATION_X * (millisX - TOTAL_MM_X/2)
    millisMoveY = ZABER_ORIENTATION_Y * (millisY - TOTAL_MM_Y/2)
    return millisMoveX, millisMoveY 

def trackWorm(input, deviceX: Device, deviceY: Device, deviceXPos, deviceYPos, resolution):
    global MAXIMUM_DEVICE_XY_POSITION, MINIMUM_DEVICE_POSITION
    # check if the input is NaN float value and return if so  
    if math.isnan(input[0]):
        return 0, 0
    # relative worm position is relative to the (0, 0) of the video feed
    master = simpleToCenter(input[0], input[1], resolution)
    # convert the millimeters back to microsteps
    xCmdAmt = master[0] * MM_MST
    yCmdAmt = master[1] * MM_MST

    # move device if the bounds of the device are not exceeded
    if (deviceXPos + xCmdAmt < MAXIMUM_DEVICE_XY_POSITION
    or deviceXPos + xCmdAmt > MINIMUM_DEVICE_POSITION
    or deviceYPos + yCmdAmt < MAXIMUM_DEVICE_XY_POSITION
    or deviceYPos + yCmdAmt > MINIMUM_DEVICE_POSITION):
        # move the device to the new position.
        x_data =int(xCmdAmt/5)
        y_data =int(yCmdAmt/5)
        deviceX.generic_command_no_response(f'move rel {x_data} 10000 5')
        deviceY.generic_command_no_response(f'move rel {y_data} 10000 5')
    return (deviceXPos + xCmdAmt), (deviceYPos + yCmdAmt)#, xCmdAmt, yCmdAmt

def Thresh_Light_Background(frame):
    # Increase contrast using histogram equalization and CLAHE
    # clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    # frame = clahe.apply(frame)
    blurred_frame = cv2.GaussianBlur(frame, (33, 33), 99)
    thresh = cv2.adaptiveThreshold(blurred_frame, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 99, 5)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    eroded_image = cv2.erode(thresh, kernel, iterations=3)
    processed_frame = cv2.dilate(eroded_image, kernel, iterations=1)
    return processed_frame

def Thresh_Fluorescent_Marker(frame):
    inv_gamma = 1.5
    table = np.array([((i / 255.0) ** inv_gamma) * 255 for i in np.arange(0, 256)]).astype("uint8")
    gamma = cv2.LUT(frame, table)
    # Apply Gaussian blur to the frame
    blurred_frame = cv2.GaussianBlur(gamma, (1, 1), 11)
    # Apply Yen's thresholding
    thresh = threshold_yen(blurred_frame)
    # Create a binary image by applying the threshold
    processed_frame = blurred_frame > thresh
    processed_frame = processed_frame.astype(np.uint8) * 255
    return processed_frame

def find_worm_cms(processed_frame, factor, initial_coords):
    labeled_image = label(processed_frame)  #scans binary image and groups connected pixels with the value 1 into labeled regions
    regions = regionprops(labeled_image) #calculates properties of the labelled region, returns a list of regionProperties objects, each corresponding to properties of one of the labelled regions.
    regions_by_area = sorted(regions, key=lambda x: x.area, reverse=True)
    if regions_by_area:
        coords = regions_by_area[0].centroid
    else:
        coords = initial_coords  # center of FOV
    coords = (round(coords[1]), round(coords[0])) # flip x and y coordinates to match the video feed
    coords = (coords[0] * factor, coords[1] * factor)
    return coords

def DLC_tracking(dlc_live,firstIt,frame,new_size,factor): ## DLC TRACKING FUNCTION, SHOULD WORK IF VARIABLES ARE SET CORRECTLY
    # Resize the image
    img_tracking = cv2.resize(frame, new_size, interpolation = cv2.INTER_AREA)
    if firstIt: 
        dlc_live.init_inference(img_tracking)
        firstIt = False
    poseArr = dlc_live.get_pose(img_tracking)
    # rescale the pose array to the original image size
    poseArr = poseArr * factor
    return poseArr

def draw_skeleton(frame, posArr): ### DLC SKELETON DRAWING FUNCTION EDIT AS NEEDED
    # Line and circle attributes
    linecolor = (0, 0, 0)
    lineThickness = 2
    circleThickness = -1
    circleRadius = 5

    # Colors of the different worm parts
    noseTipColor = (0, 0, 255)
    pharynxColor = (0, 128, 255)
    nerveRingColor = (0, 255, 255)
    # Overlay the tracking data onto the image
    # line from nose tip to pharynx
    cv2.line(frame, posArr[0], posArr[1], linecolor, lineThickness)
    # line from pharynx to nerve_ring
    cv2.line(frame, posArr[1], posArr[2], linecolor, lineThickness)

    # draw circles on top of each worm part
    cv2.circle(frame, posArr[0], circleRadius, noseTipColor, circleThickness)
    cv2.circle(frame, posArr[1], circleRadius, pharynxColor, circleThickness)
    cv2.circle(frame, posArr[2], circleRadius, nerveRingColor, circleThickness)
    return frame

def filepathToDirectory(str_path: str):
    # global record_path
    path = pathlib.Path(str_path)
    if not path.exists():
        path.mkdir(parents=True)
    return path

def start_controller():
    # Check if any joystick is connected
    pygame.init()
    pygame.joystick.init()
    global isManualEnabled, xMotor, yMotor, zMotor, is_tracking, is_recording, start_recording, start_recording_r, af_enabled, MAXIMUM_DEVICE_XY_POSITION, MAXIMUM_DEVICE_Z_POSITION
    # Track last button state to detect button press (not held down)
    last_x_button_state = False
    last_a_button_state = False
    last_b_button_state = False
    
    if pygame.joystick.get_count() > 0:
        # Get the first joystick
        joystick = pygame.joystick.Joystick(0)
        joystick.init()

        # Get the number of axes
        num_axes = joystick.get_numaxes()

        # Check if the joystick has at least 2 axes
        if num_axes >= 2:
            sticks_good = True
            print("Joystick count: ", pygame.joystick.get_count())
            print("Joystick axes: ", joystick.get_numaxes())
            print("Joystick buttons: ", joystick.get_numbuttons())
        else:
            print("Joystick does not have enough axes")
    else:
        print("No joystick connected.")
        sticks_good = False
        
    try: 
        while isManualEnabled:
            
            pygame.event.pump()

            xPos = xMotor.get_position(unit=Units.NATIVE)
            yPos = yMotor.get_position(unit=Units.NATIVE)
            zPos = zMotor.get_position(unit=Units.NATIVE)

            if sticks_good == True:
                input_x = joystick.get_axis(0)
                input_y = joystick.get_axis(1)
                input_z = joystick.get_axis(3)
                x_data = int(input_x * 2000)
                y_data = int(input_y * 2000) * -1
                z_data = int(input_z * 100)
                    
            # Check X button (button 2) for tracking toggle
            if joystick.get_numbuttons() > 2:  # Make sure X button exists
                current_x_button_state = joystick.get_button(2)
                current_a_button_state = joystick.get_button(0)
                current_b_button_state = joystick.get_button(1)
                # Detect rising edge for X button
                if current_x_button_state and not last_x_button_state:
                    # Toggle tracking state
                    is_tracking = not is_tracking
                    print(f"X button pressed: {'Enabling' if is_tracking else 'Disabling'} tracking")
                if current_a_button_state and not last_a_button_state:
                    # Toggle recording state
                    af_enabled = not af_enabled
                    print(f"A button pressed: {'Starting' if af_enabled else 'Stopping'} focus lock")
                if current_b_button_state and not last_b_button_state:
                    # Toggle recording state
                    if not is_recording: # check if already recording
                        start_recording = True
                        start_recording_r = True
                    is_recording = not is_recording
                    print(f"B button pressed: {'Starting' if is_recording else 'Stopping'} recording")

                # Update last X button state
                last_x_button_state = current_x_button_state
                last_a_button_state = current_a_button_state
                last_b_button_state = current_b_button_state

            if ((xPos + x_data < MAXIMUM_DEVICE_XY_POSITION
            or xPos + x_data > MINIMUM_DEVICE_POSITION) and input_x != 0):
                mutex.acquire(blocking=True, timeout=-1)
                xMotor.generic_command_no_response(f'move rel {x_data} 10000 5')
                mutex.release()

            if ((yPos + y_data < MAXIMUM_DEVICE_XY_POSITION
            or yPos + y_data > MINIMUM_DEVICE_POSITION) and input_y != 0):
                mutex.acquire(blocking=True, timeout=-1)
                yMotor.generic_command_no_response(f'move rel {y_data} 10000 5')
                mutex.release()

            if ((zPos + z_data < MAXIMUM_DEVICE_Z_POSITION
            or zPos + z_data > MINIMUM_DEVICE_POSITION) and input_z != 0):
                zMotor.generic_command_no_response(f'move rel {z_data} 10000 5')

    except KeyboardInterrupt:
        print("Exiting...")
        mutex.release()
        pygame.quit()

# --- Basler pypylon compatibility wrapper (replaces EasyPySpin.VideoCapture) ---
class BaslerCamera:
    """
    Minimal wrapper to mimic cv2.VideoCapture using pypylon for Basler cameras.
    Accepts:
      - None or "" -> first camera
      - integer index or "index:<n>" -> camera by index
      - serial string or "serial:<SERIAL>" -> camera by serial
      - friendly or full name match
    """
    def __init__(self, identifier=None):
        self.camera = None
        self.converter = pylon.ImageFormatConverter()
        self.converter.OutputPixelFormat = pylon.PixelType_Mono8
        self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
        self._width = 0
        self._height = 0
        try:
            tl_factory = pylon.TlFactory.GetInstance()
            devices = tl_factory.EnumerateDevices()
            if not devices:
                print("BaslerCamera: No cameras found.")
                return

            idx = None
            sid = (str(identifier).strip() if identifier is not None else "")
            # Parse index:<n>
            if sid == "":
                idx = 0
            elif sid.startswith("index:"):
                try:
                    idx = int(sid.split(":", 1)[1])
                except ValueError:
                    idx = 0
            elif sid.isdigit():
                idx = int(sid)
            else:
                # serial:<SERIAL> or direct serial/friendly/full name
                target = sid.split(":", 1)[1] if sid.lower().startswith("serial:") else sid
                for i, d in enumerate(devices):
                    if d.GetSerialNumber() == target or d.GetFriendlyName() == target or d.GetFullName() == target:
                        idx = i
                        break
                if idx is None:
                    # try partial friendly/full name match
                    for i, d in enumerate(devices):
                        if target in d.GetFriendlyName() or target in d.GetFullName():
                            idx = i
                            break

            if idx is None or idx < 0 or idx >= len(devices):
                print(f"BaslerCamera: Identifier did not match any camera: {identifier}")
                return

            self.camera = pylon.InstantCamera(tl_factory.CreateDevice(devices[idx]))
            self.camera.Open()
            # Force Mono8 if available
            try:
                if "Mono8" in self.camera.PixelFormat.Symbolics:
                    self.camera.PixelFormat.SetValue("Mono16")
            except Exception as e:
                print("BaslerCamera: Unable to set PixelFormat Mono16:", e)

            # Cache current width/height
            try:
                self._width = float(self.camera.Width.Value)
                self._height = float(self.camera.Height.Value)
            except Exception:
                self._width = 0
                self._height = 0

            self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
        except Exception as e:
            print("BaslerCamera: Error initializing camera:", e)
            self.camera = None

    def isOpened(self):
        return self.camera is not None and self.camera.IsOpen() and self.camera.IsGrabbing()

    def read(self, timeout_ms=5000):
        if self.camera is None:
            return False, None
        try:
            grab = self.camera.RetrieveResult(timeout_ms, pylon.TimeoutHandling_ThrowException)
            try:
                if grab.GrabSucceeded():
                    img = self.converter.Convert(grab)
                    frame = img.GetArray()  # numpy uint8 2D
                    # populate width/height on first successful grab if unknown
                    if self._width == 0 or self._height == 0:
                        try:
                            self._width = frame.shape[1]
                            self._height = frame.shape[0]
                        except Exception:
                            pass
                    return True, frame
                else:
                    return False, None
            finally:
                grab.Release()
        except Exception as e:
            print("BaslerCamera: Error grabbing frame:", e)
            return False, None

    def get(self, prop_id):
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return self._width
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return self._height
        return 0.0

    def release(self):
        try:
            if self.camera:
                if self.camera.IsGrabbing():
                    self.camera.StopGrabbing()
                if self.camera.IsOpen():
                    self.camera.Close()
        except Exception:
            pass
        finally:
            self.camera = None

if __name__ == "__main__":
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)