import time
import os
import board
import terminalio
from adafruit_matrixportal.matrix import Matrix
import adafruit_requests as requests
import json
from json_filter import JsonFilter

import adafruit_display_text.label
import displayio
import gc

import busio
from digitalio import DigitalInOut
import neopixel
from adafruit_esp32spi import adafruit_esp32spi
from adafruit_esp32spi.adafruit_esp32spi_wifimanager import WiFiManager

import microcontroller
from microcontroller import watchdog as w
from watchdog import WatchDogMode

# Log why we (re)started - tells us if the watchdog fired, power dropped, etc.
print("Boot. Reset reason:", microcontroller.cpu.reset_reason)

w.timeout=16 # timeout in seconds
w.mode = WatchDogMode.RESET

FONT=terminalio.FONT

# settings.toml only supports strings and integers, so accept "True"/"False" or 1/0
def setting_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in ("true", "1", "yes", "on")

def setting_int(name, default):
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        print("Ignoring invalid "+name+" in settings.toml:", value)
        return default

WIFI_SSID = os.getenv("CIRCUITPY_WIFI_SSID")
WIFI_PASSWORD = os.getenv("CIRCUITPY_WIFI_PASSWORD")
if not WIFI_SSID or not WIFI_PASSWORD:
    raise ValueError("Missing CIRCUITPY_WIFI_SSID or CIRCUITPY_WIFI_PASSWORD in settings.toml")

# Show WiFi/request status on the board's status LED
STATUS_LEDS=setting_bool("STATUS_LEDS", True)
# Ignore flights above this altitude in feet (0 = no limit). Handy to skip high overflights you can't see.
MAX_ALTITUDE=setting_int("MAX_ALTITUDE", 0)

# How often to query fr24 - quick enough to catch a plane flying over, not so often as to cause any issues, hopefully
QUERY_DELAY=30
# After failed searches, wait QUERY_DELAY * 2^errors seconds (capped) before trying again
MAX_BACKOFF=600
# Longest Retry-After from the server we'll honour
MAX_RETRY_AFTER=1800
# How many times to try loading details for the same flight before giving up on it
MAX_DETAIL_ATTEMPTS=3
# Seconds to wait on a network request before giving up. Must stay well under the
# 16s watchdog, or a slow server reboots the board instead of logging an error.
REQUEST_TIMEOUT=10
# How many flights to ask for per search; we show the lowest one
SEARCH_LIMIT=5
# Seconds to let the ESP32 WiFi chip boot after a reset before connecting.
# (3s didn't stop the first connect failing on ESP32 firmware 1.2.2 - updating the firmware is the likely fix)
ESP_RESET_WAIT=1
#Area to search for flights, see settings.toml
BOUNDS_BOX=os.getenv("BOUNDS_BOX")
if not BOUNDS_BOX:
    raise ValueError("Missing BOUNDS_BOX in settings.toml")

# Colours and timings
ROW_ONE_COLOUR=0x888888
ROW_TWO_COLOUR=0xAA5500
ROW_THREE_COLOUR=0x006600
PLANE_COLOUR=0x880000
# Time in seconds to wait between scrolling one label and the next
PAUSE_BETWEEN_LABEL_SCROLLING=3
# speed plane animation will move - pause time per pixel shift in seconds
PLANE_SPEED=0.04
# speed text labels will move - pause time per pixel shift in seconds
TEXT_SPEED=0.04

#URLs
FLIGHT_SEARCH_HEAD="https://data-cloud.flightradar24.com/zones/fcgi/feed.js?bounds="
FLIGHT_SEARCH_TAIL="&faa=1&satellite=1&mlat=1&flarm=1&adsb=1&gnd=0&air=1&vehicles=0&estimated=0&maxage=14400&gliders=0&stats=0&ems=1&limit="+str(SEARCH_LIMIT)
FLIGHT_SEARCH_URL=FLIGHT_SEARCH_HEAD+BOUNDS_BOX+FLIGHT_SEARCH_TAIL
# Deprecated URL used to return less JSON than the long details URL, but can give ambiguous results
# FLIGHT_DETAILS_HEAD="https://api.flightradar24.com/common/v1/flight/list.json?&fetchBy=flight&page=1&limit=1&maxage=14400&query="

# Used to get more flight details with a fr24 flight ID from the initial search
FLIGHT_LONG_DETAILS_HEAD="https://data-live.flightradar24.com/clickhandler/?flight="

# Request headers
rheaders = {
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:106.0) Gecko/20100101 Firefox/106.0",
     "Accept": "application/json",
     "Referer": "https://www.flightradar24.com/",
     "Origin": "https://www.flightradar24.com",
     "Accept-Language": "en-US,en;q=0.5",
     "Connection": "keep-alive",
     "Accept-Encoding": "identity"
}

esp32_cs = DigitalInOut(board.ESP_CS)
esp32_ready = DigitalInOut(board.ESP_BUSY)
esp32_reset = DigitalInOut(board.ESP_RESET)
spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
esp = adafruit_esp32spi.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)
status_light = neopixel.NeoPixel(
    board.NEOPIXEL, 1, brightness=0.2
)
if not STATUS_LEDS:
    status_light.fill(0)
wifi = WiFiManager(esp, WIFI_SSID, WIFI_PASSWORD, status_pixel=status_light if STATUS_LEDS else None)
try:
    fw = esp.firmware_version  # str in newer adafruit_esp32spi, bytes in older
    if not isinstance(fw, str):
        fw = str(fw, "utf-8")
    print("ESP32 firmware:", fw.strip("\x00"))
except Exception as e:
    print("Could not read ESP32 firmware version:", e)


# The LED matrix display. We only need the display, so use Matrix rather than the full
# MatrixPortal helper, which also sets up its own network stack and Adafruit IO client (costs RAM).
display = Matrix(width=64, height=32, bit_depth=2, rotation=0).display

# Some memory shenanigans - the board doesn't do great at assigning big strings dynamically.
# So we create one static array at startup and reuse it for every flight. The details response is
# filtered as it streams in (see json_filter.py) so only the fields below are kept - a few hundred bytes.
json_size=2048
json_bytes=bytearray(json_size)
json_len=0  # bytes of valid JSON currently in json_bytes

# Fields of the FR24 details JSON we keep. Add a dotted path here to use another field in parse_details_json().
# (Arrays like "trail" can only be kept whole, which is too big - so altitude/speed aren't available.)
DETAIL_FIELDS = (
    "identification.number.default",
    "identification.callsign",
    "aircraft.model.code",
    "aircraft.model.text",
    "airline.name",
    "airport.origin.name",
    "airport.origin.code.iata",
    "airport.destination.name",
    "airport.destination.code.iata",
)
details_filter=JsonFilter(json_bytes, DETAIL_FIELDS, stop_key="trail")
gc.collect()  # Clear memory after initial allocation

# Helper: sleep while periodically feeding the watchdog so long sleeps
def feed_sleep(total_seconds, step_seconds=0.5):
    """Sleep for total_seconds but call watchdog.feed() every step_seconds.
    Keeps the device responsive to the watchdog while waiting.
    """
    if total_seconds <= 0:
        return
    end = time.monotonic() + total_seconds
    while True:
        w.feed()
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(step_seconds, remaining))

def safe_text(value):
    return value if value else ""

def safe_get(obj, *keys):
    """Safely navigate nested dict/JSON, return None if any level is missing or None."""
    for key in keys:
        if obj is None or not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj

# Little plane to scroll across when we find a flight overhead
planeBmp = displayio.Bitmap(12, 12, 2)
planePalette = displayio.Palette(2)
planePalette[1] = PLANE_COLOUR
planePalette[0] = 0x000000
planeBmp[6,0]=planeBmp[6,1]=planeBmp[5,1]=planeBmp[4,2]=planeBmp[5,2]=planeBmp[6,2]=1
planeBmp[9,3]=planeBmp[5,3]=planeBmp[4,3]=planeBmp[3,3]=1
planeBmp[1,4]=planeBmp[2,4]=planeBmp[3,4]=planeBmp[4,4]=planeBmp[5,4]=planeBmp[6,4]=planeBmp[7,4]=planeBmp[8,4]=planeBmp[9,4]=1
planeBmp[1,5]=planeBmp[2,5]=planeBmp[3,5]=planeBmp[4,5]=planeBmp[5,5]=planeBmp[6,5]=planeBmp[7,5]=planeBmp[8,5]=planeBmp[9,5]=1
planeBmp[9,6]=planeBmp[5,6]=planeBmp[4,6]=planeBmp[3,6]=1
planeBmp[6,9]=planeBmp[6,8]=planeBmp[5,8]=planeBmp[4,7]=planeBmp[5,7]=planeBmp[6,7]=1
planeTg= displayio.TileGrid(planeBmp, pixel_shader=planePalette)
planeG=displayio.Group(x=display.width+12,y=10)
planeG.append(planeTg)

# We can fit three rows of text on a panel, so one label for each. We'll change their text as needed
label1 = adafruit_display_text.label.Label(
    FONT,
    color=ROW_ONE_COLOUR,
    text="")
label1.x = 1
label1.y = 4

label2 = adafruit_display_text.label.Label(
    FONT,
    color=ROW_TWO_COLOUR,
    text="")
label2.x = 1
label2.y = 15

label3 = adafruit_display_text.label.Label(
    FONT,
    color=ROW_THREE_COLOUR,
    text="")
label3.x = 1
label3.y = 25

# text strings to go in the labels
label1_short=''
label1_long=''
label2_short=''
label2_long=''
label3_short=''
label3_long=''

# Add the labels to the display
g = displayio.Group()
g.append(label1)
g.append(label2)
g.append(label3)
display.root_group = g

# Scroll the plane bitmap right to left (same direction as scrolling text)
def plane_animation():
    display.root_group = planeG
    for i in range(display.width+24,-12,-1):
            planeG.x=i
            w.feed()
            feed_sleep(PLANE_SPEED, step_seconds=PLANE_SPEED)

# Scroll a label, start at the right edge of the screen and go left one pixel at a time
# Until the right edge of the label reaches the left edge of the screen
def scroll(line):
    line.x=display.width
    for i in range(display.width+1,0-line.bounding_box[2],-1):
        line.x=i
        w.feed()
        feed_sleep(TEXT_SPEED, step_seconds=TEXT_SPEED)
        

# Populate the labels, then scroll longer versions of the text
def display_flight():

    display.root_group = g
    label1.text=label1_short
    label2.text=label2_short
    label3.text=label3_short
    feed_sleep(PAUSE_BETWEEN_LABEL_SCROLLING, step_seconds=0.5)
    
    label1.x=display.width+1
    label1.text=label1_long
    scroll(label1)
    label1.text=label1_short
    label1.x=1
    feed_sleep(PAUSE_BETWEEN_LABEL_SCROLLING, step_seconds=0.5)
    
    label2.x=display.width+1
    label2.text=label2_long
    scroll(label2)
    label2.text=label2_short
    label2.x=1
    feed_sleep(PAUSE_BETWEEN_LABEL_SCROLLING, step_seconds=0.5)
    
    label3.x=display.width+1
    label3.text=label3_long
    scroll(label3)
    label3.text=label3_short
    label3.x=1
    feed_sleep(PAUSE_BETWEEN_LABEL_SCROLLING, step_seconds=0.5)

# Blank the display when a flight is no longer found
def clear_flight():
    label1.text=label2.text=label3.text=""


# Take the flight ID we found with a search, and load details about it
def get_flight_details(fn):

    # The JSON from FR24 is too big for the board's memory to handle. So we stream it in chunks
    # through details_filter, which keeps only DETAIL_FIELDS in our static array and stops at the
    # huge "trail" section near the end.
    global json_len

    # Clear memory before fetching to avoid fragmentation issues
    w.feed()
    gc.collect()

    json_len=0
    byte_counter=0
    chunk_length=256  # Reduced from 512 to use less peak memory during transfer
    details_filter.reset()

    # Get the URL response one chunk at a time
    response = None
    try:
        response = wifi.get(url=FLIGHT_LONG_DETAILS_HEAD+fn, headers=rheaders, timeout=REQUEST_TIMEOUT)
        print("Detail fetch status:", response.status_code)
        if response.status_code != 200:
            print_body_preview(response)
            return False
        for chunk in response.iter_content(chunk_size=chunk_length):
            w.feed()
            byte_counter+=len(chunk)
            details_filter.feed(chunk)
            if details_filter.error:
                print("Details filter error: "+details_filter.error+" after "+str(byte_counter)+" bytes")
                break
            if details_filter.done:
                json_len=details_filter.length
                print("Details lookup read "+str(byte_counter)+" bytes, kept "+str(json_len)+".")
                return True
    # Handle occasional URL fetching errors
    except (RuntimeError, OSError, TimeoutError, MemoryError) as e:
            print("Error--------------------------------------------------")
            print(e)
            w.feed()
            gc.collect()  # Clear memory immediately after error
            checkConnection()  # Try to reconnect if we hit an error
            return False
    finally:
        if response is not None:
            response.close()
        w.feed()
        gc.collect()  # Ensure memory is cleaned up

    # If we got here the response ended (or the filter errored) before we had a complete object
    if not details_filter.error:
        print("Details response ended early after "+str(byte_counter)+" bytes")
    try:
        print("Kept so far:", bytes(json_bytes[:min(details_filter.length, 180)]).decode("utf-8", "replace"))
    except Exception:
        pass
    return False
    

# Look up an aircraft type code (e.g. "B738") in ICAO.csv and return "Manufacturer Model",
# or None if the file or code isn't there. Reads the file each time rather than keeping it in memory.
AIRCRAFT_FILE="/ICAO.csv"
def friendly_aircraft_name(code):
    if not code:
        return None
    prefix=code+","
    try:
        with open(AIRCRAFT_FILE, "r") as f:
            for line in f:
                if line.startswith(prefix):
                    parts=line.strip().split(",")
                    if len(parts)>=3:
                        return parts[1]+" "+parts[2]
                    return None
    except OSError:
        pass
    return None

# Look at the byte array that fetch_details saved into and extract any fields we want
def parse_details_json():

    try:
        # get the JSON from the bytes (only the json_len bytes the filter wrote are valid)
        gc.collect()
        long_json=json.loads(bytes(json_bytes[:json_len]))

        # Only fields listed in DETAIL_FIELDS are kept, so add a field's path there before reading it here.
        # Put the details URL and a flight ID in your browser to see what else is available.

        flight_number=safe_get(long_json, "identification", "number", "default")
        flight_callsign=safe_get(long_json, "identification", "callsign")
        aircraft_code=safe_get(long_json, "aircraft", "model", "code")
        aircraft_model=safe_get(long_json, "aircraft", "model", "text")
        airline_name=safe_get(long_json, "airline", "name")
        
        airport_origin_name=safe_get(long_json, "airport", "origin", "name")
        if airport_origin_name:
            airport_origin_name=airport_origin_name.replace(" Airport","")
        airport_origin_code=safe_get(long_json, "airport", "origin", "code", "iata")
        
        airport_destination_name=safe_get(long_json, "airport", "destination", "name")
        if airport_destination_name:
            airport_destination_name=airport_destination_name.replace(" Airport","")
        airport_destination_code=safe_get(long_json, "airport", "destination", "code", "iata")
        # Other fields you could add to DETAIL_FIELDS, e.g. airport.destination.position.region.city,
        # airport.destination.info.terminal, time.scheduled.departure, time.estimated.arrival

        if flight_number:
            print("Flight is called "+flight_number)
        elif flight_callsign:
            print("No flight number, callsign is "+flight_callsign)
        else:
            print("No number or callsign for this flight.")


        # Set up to 6 of the values above as text for display_flights to put on the screen
        # Short strings get placed on screen, then longer ones scroll over each in sequence

        global label1_short
        global label1_long
        global label2_short
        global label2_long
        global label3_short
        global label3_long

        # Fall back to the callsign for flights without a number (private, cargo, etc.)
        label1_short=safe_text(flight_number or flight_callsign)
        label1_long=safe_text(airline_name)
        origin_code=safe_text(airport_origin_code)
        destination_code=safe_text(airport_destination_code)
        origin_name=safe_text(airport_origin_name)
        destination_name=safe_text(airport_destination_name)
        label2_short=(origin_code+"-"+destination_code) if (origin_code or destination_code) else ""
        label2_long=(origin_name+"-"+destination_name) if (origin_name or destination_name) else ""
        label3_short=safe_text(aircraft_code)
        # Prefer the friendly name from ICAO.csv (e.g. "Boeing 737-800" rather than FR24's "Boeing 737-8H4(WL)")
        label3_long=friendly_aircraft_name(aircraft_code) or safe_text(aircraft_model)

        # optional filter example - check things and return False to skip displaying this flight
        # if airline_name == "Some Airline":
        #    return False

    except (KeyError, ValueError,TypeError) as e:
        print("JSON error")
        print (e)
        return False
    finally:
        # Explicitly delete the large JSON object to free memory immediately
        try:
            del long_json
        except:
            pass
        gc.collect()

    return True


def checkConnection():
    print("Check and reconnect WiFi")
    attempts=10
    attempt=1
    
    # First check if ESP32 is responsive by trying to get status
    is_connected = False
    try:
        is_connected = (esp.status == adafruit_esp32spi.WL_CONNECTED)
    except (TimeoutError, OSError) as e:
        print("ESP32 not responding to status check, forcing hard reset")
        print(e.__class__.__name__+": "+str(e))
        w.feed()
        wifi.reset()
        feed_sleep(2)  # Give ESP32 time to reset
    
    while (not is_connected) and attempt<attempts:
        print("Connect attempt "+str(attempt)+" of "+str(attempts))
        print("Reset ESP...")
        w.feed()
        wifi.reset()
        feed_sleep(ESP_RESET_WAIT)  # Let the ESP32 finish booting, or the first connect tends to fail
        print("Attempt WiFi connect...")
        w.feed()
        try:
            wifi.connect()
            is_connected = True
        except (OSError, TimeoutError) as e:
            print(e.__class__.__name__+"--------------------------------------")
            print(e)
            is_connected = False
        attempt+=1
    
    # Check final connection status
    try:
        if esp.status == adafruit_esp32spi.WL_CONNECTED:
            print("Successfully connected.")
        else:
            print("Failed to connect.")
    except (TimeoutError, OSError) as e:
        print("ESP32 still not responding after reset attempts")
        print(e.__class__.__name__+": "+str(e))


# Print the start of a response body, small enough not to blow memory.
# Useful to spot a Cloudflare/HTML block page instead of JSON.
def print_body_preview(response, max_bytes=160):
    try:
        for chunk in response.iter_content(chunk_size=max_bytes):
            print("Body preview:", bytes(chunk).decode("utf-8", "replace"))
            break
    except Exception as e:
        print("Could not read body:", e)

# Read a header case-insensitively, return None if missing
def get_header(response, name):
    try:
        name = name.lower()
        for key, value in response.headers.items():
            if key.lower() == name:
                return value
    except Exception:
        pass
    return None

# Set by get_flights when the server sends Retry-After
retry_after = 0

# Look for flights overhead.
# Returns a flight ID, False if no flight is overhead, or None if the search failed.
def get_flights():
    global retry_after
    retry_after = 0
    response = None
    try:
        response = wifi.get(url=FLIGHT_SEARCH_URL, headers=rheaders, timeout=REQUEST_TIMEOUT)
        status = response.status_code
        if status != 200:
            print("Search HTTP status:", status)
            ra = get_header(response, "Retry-After")
            if ra:
                print("Retry-After:", ra)
                try:
                    retry_after = int(ra)
                except ValueError:
                    pass
            print_body_preview(response)
            return None
        try:
            data = response.json()
        except ValueError as e:
            # 200 but not JSON - most likely an HTML challenge/block page
            print("Search returned non-JSON (possible block page):", e)
            return None
    except (RuntimeError, OSError, requests.OutOfRetries, TimeoutError) as e:
        # Network-level failure - worth checking the WiFi
        print(e.__class__.__name__+"--------------------------------------")
        print(e)
        checkConnection()
        return None
    finally:
        if response is not None:
            response.close()

    # Besides "version" and "full_count", each key is a flight ID mapped to a list:
    # [icao24, lat, lon, heading, altitude_ft, speed_kt, squawk, radar, type, reg, time, origin, dest, flight, ...]
    # Pick the lowest flight (most likely the one you can see), skipping any above MAX_ALTITUDE.
    best_id=None
    best_alt=None
    count=0
    skipped=0
    for flight_id, flight_info in data.items():
        if not isinstance(flight_info, list) or len(flight_info)<=13:
            continue
        count+=1
        alt=flight_info[4] if isinstance(flight_info[4], int) else 0
        if MAX_ALTITUDE and alt>MAX_ALTITUDE:
            skipped+=1
            continue
        if best_alt is None or alt<best_alt:
            best_id=flight_id
            best_alt=alt
    del data
    if best_id:
        if count>1:
            print("Search 200: "+str(count)+" flights, lowest is "+best_id+" at "+str(best_alt)+"ft")
        return best_id
    if skipped:
        print("Search 200, "+str(skipped)+" flight(s) above MAX_ALTITUDE. mem_free:", gc.mem_free())
    else:
        print("Search 200, no flight. mem_free:", gc.mem_free())
    return False



# Actual doing of things - loop forever quering fr24, processing any results and waiting to query again

checkConnection()

last_flight=''
failed_flight=''
failed_attempts=0
consecutive_errors=0
while True:

    w.feed()

    flight_id=get_flights()
    w.feed()

    # Search failed - back off rather than hammering the server
    if flight_id is None:
        consecutive_errors+=1
        delay=min(QUERY_DELAY*(2**consecutive_errors), MAX_BACKOFF)
        if retry_after>delay:
            delay=min(retry_after, MAX_RETRY_AFTER)
        print("Search failed ("+str(consecutive_errors)+" in a row), waiting "+str(delay)+"s")
        gc.collect()
        feed_sleep(delay, step_seconds=5)
        continue
    if consecutive_errors:
        print("Search recovered after "+str(consecutive_errors)+" failed attempt(s)")
        consecutive_errors=0


    if flight_id:
        if flight_id==last_flight:
            print("Same flight found, so keep showing it")
        else:
            print("New flight "+flight_id+" found, clear display")
            clear_flight()
            w.feed()
            gc.collect()  # Clear before fetching
            shown=False
            if get_flight_details(flight_id):
                w.feed()
                gc.collect()  # Clear after fetching
                if parse_details_json():
                    w.feed()
                    gc.collect()  # Clear after parsing
                    plane_animation()
                    display_flight()
                    shown=True
                    # Clear any remaining objects from display
                    w.feed()
                    gc.collect()
                else:
                    print("error parsing JSON, skip displaying this flight")
                    gc.collect()
            else:
                print("error loading details, skip displaying this flight")
                gc.collect()

            if shown:
                last_flight=flight_id
                failed_flight=''
                failed_attempts=0
            else:
                # Retry this flight on the next search, but give up after a few tries
                # so a flight that always fails doesn't cause a details request every loop
                if flight_id==failed_flight:
                    failed_attempts+=1
                else:
                    failed_flight=flight_id
                    failed_attempts=1
                if failed_attempts>=MAX_DETAIL_ATTEMPTS:
                    print("Giving up on flight "+flight_id+" after "+str(failed_attempts)+" attempts")
                    last_flight=flight_id
                else:
                    print("Will retry flight "+flight_id+" (attempt "+str(failed_attempts)+" of "+str(MAX_DETAIL_ATTEMPTS)+")")
            # Final cleanup after processing this flight
            w.feed()
            gc.collect()
    else:
        #print("No flights found, clear display")
        clear_flight()
    
    # Aggressive garbage collection to avoid memory fragmentation
    w.feed()
    gc.collect()
    
    # wait QUERY_DELAY seconds but keep watchdog fed in 5s intervals
    feed_sleep(QUERY_DELAY, step_seconds=5)
    w.feed()
    gc.collect()
