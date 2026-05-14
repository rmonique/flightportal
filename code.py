import time
import os
import board
import terminalio
from adafruit_matrixportal.matrixportal import MatrixPortal
from adafruit_portalbase.network import HttpError
import adafruit_requests as requests
import json

import adafruit_display_text.label
import displayio
import framebufferio
import rgbmatrix
import gc

import busio
from digitalio import DigitalInOut
import neopixel
from adafruit_esp32spi import adafruit_esp32spi
from adafruit_esp32spi.adafruit_esp32spi_wifimanager import WiFiManager

from microcontroller import watchdog as w
from watchdog import WatchDogMode

w.timeout=16 # timeout in seconds
w.mode = WatchDogMode.RESET

FONT=terminalio.FONT

WIFI_SSID = os.getenv("CIRCUITPY_WIFI_SSID")
WIFI_PASSWORD = os.getenv("CIRCUITPY_WIFI_PASSWORD")
if not WIFI_SSID or not WIFI_PASSWORD:
    raise ValueError("Missing CIRCUITPY_WIFI_SSID or CIRCUITPY_WIFI_PASSWORD in settings.toml")

# How often to query fr24 - quick enough to catch a plane flying over, not so often as to cause any issues, hopefully
QUERY_DELAY=30
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
FLIGHT_SEARCH_TAIL="&faa=1&satellite=1&mlat=1&flarm=1&adsb=1&gnd=0&air=1&vehicles=0&estimated=0&maxage=14400&gliders=0&stats=0&ems=1&limit=1"
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
wifi = WiFiManager(esp, WIFI_SSID, WIFI_PASSWORD, status_pixel=status_light)


# Top level matrixportal object
matrixportal = MatrixPortal(
    headers=rheaders,
    esp=esp,
    rotation=0,
    debug=False
)

# Some memory shenanigans - the matrixportal doesn't do great at assigning big strings dynamically. So we create a big static array to put the JSON results in each time.
json_size=9500
json_bytes=bytearray(json_size)
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
planeG=displayio.Group(x=matrixportal.display.width+12,y=10)
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
matrixportal.display.root_group = g

# Scroll the plane bitmap right to left (same direction as scrolling text)
def plane_animation():
    matrixportal.display.root_group = planeG
    for i in range(matrixportal.display.width+24,-12,-1):
            planeG.x=i
            w.feed()
            feed_sleep(PLANE_SPEED, step_seconds=PLANE_SPEED)
            #matrixportal.display.refresh(minimum_frames_per_second=0)

# Scroll a label, start at the right edge of the screen and go left one pixel at a time
# Until the right edge of the label reaches the left edge of the screen
def scroll(line):
    line.x=matrixportal.display.width
    for i in range(matrixportal.display.width+1,0-line.bounding_box[2],-1):
        line.x=i
        w.feed()
        feed_sleep(TEXT_SPEED, step_seconds=TEXT_SPEED)
        #matrixportal.display.refresh(minimum_frames_per_second=0)
        

# Populate the labels, then scroll longer versions of the text
def display_flight():

    matrixportal.display.root_group = g
    label1.text=label1_short
    label2.text=label2_short
    label3.text=label3_short
    feed_sleep(PAUSE_BETWEEN_LABEL_SCROLLING, step_seconds=0.5)
    
    label1.x=matrixportal.display.width+1
    label1.text=label1_long
    scroll(label1)
    label1.text=label1_short
    label1.x=1
    feed_sleep(PAUSE_BETWEEN_LABEL_SCROLLING, step_seconds=0.5)
    
    label2.x=matrixportal.display.width+1
    label2.text=label2_long
    scroll(label2)
    label2.text=label2_short
    label2.x=1
    feed_sleep(PAUSE_BETWEEN_LABEL_SCROLLING, step_seconds=0.5)
    
    label3.x=matrixportal.display.width+1
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

    # the JSON from FR24 is too big for the matrixportal memory to handle. So we load it in chunks into our static array,
    # as far as the big "trails" section of waypoints at the end of it, then ignore most of that part. Should be about 9KB, we have 9.5K before we run out of room..
    global json_bytes
    global json_size
    
    # Clear memory before fetching to avoid fragmentation issues
    w.feed()
    gc.collect()
    
    byte_counter=0
    chunk_length=256  # Reduced from 512 to use less peak memory during transfer
    # Clear the json_bytes array to ensure no stale data from previous flights
    for i in range(json_size):
        json_bytes[i] = 0
    gc.collect()  # Clear memory before fetching

    # Get the URL response one chunk at a time
    response = None
    try:
        response = wifi.get(url=FLIGHT_LONG_DETAILS_HEAD+fn, headers=rheaders)
        try:
            print("Detail fetch status:", response.status_code)
        except Exception:
            pass
        try:
            content_type = response.headers.get("Content-Type")
            if content_type:
                print("Detail content type:", content_type)
        except Exception:
            pass
        for chunk in response.iter_content(chunk_size=chunk_length):

            # if the chunk will fit in the byte array, add it
            if(byte_counter+chunk_length<=json_size):
                for i in range(0,len(chunk)):
                    json_bytes[i+byte_counter]=chunk[i]
            else:
                print("Exceeded max string size while parsing JSON")
                return False

            # check if this chunk contains the "trail:" tag which is the last bit we care about
            trail_start=json_bytes.find((b"\"trail\":"))
            byte_counter+=len(chunk)

            # if it does, find the first/most recent of the many trail entries, giving us things like speed and heading
            if not trail_start==-1:
                # work out the location of the first } character after the "trail:" tag, giving us the first entry
                trail_end=json_bytes[trail_start:].find((b"}"))
                if not trail_end==-1:
                    trail_end+=trail_start
                    # characters to add to make the whole JSON object valid, since we're cutting off the end
                    closing_bytes=b'}]}'
                    for i in range (0,len(closing_bytes)):
                        json_bytes[trail_end+i]=closing_bytes[i]
                    # zero out the rest
                    for i in range(trail_end+3,json_size):
                        json_bytes[i]=0
                    # print(json_bytes.decode('utf-8'))

                    # Stop reading chunks
                    print("Details lookup saved "+str(trail_end)+" bytes.")
                    w.feed()
                    gc.collect()  # Free memory before returning
                    return True
    # Handle occasional URL fetching errors            
    except (RuntimeError, OSError, HttpError, TimeoutError, MemoryError) as e:
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

    #If we got here we got through all the JSON without finding the right trail entries
    print("Failed to find a valid trail entry in JSON")
    try:
        preview = json_bytes[:180].decode("utf-8", "replace")
        print("Detail response preview:")
        print(preview)
    except Exception:
        pass
    return False
    

# Look at the byte array that fetch_details saved into and extract any fields we want
def parse_details_json():

    global json_bytes

    try:
        # get the JSON from the bytes
        gc.collect()
        long_json=json.loads(json_bytes)

        # Some available values from the JSON. Put the details URL and a flight ID in your browser and have a look for more.

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
        #airport_destination_country=long_json["airport"]["destination"]["position"]["country"]["name"]
        #airport_destination_country_code=long_json["airport"]["destination"]["position"]["country"]["code"]
        #airport_destination_city=long_json["airport"]["destination"]["position"]["region"]["city"]
        #airport_destination_terminal=long_json["airport"]["destination"]["info"]["terminal"]
        #time_scheduled_departure=long_json["time"]["scheduled"]["departure"]
        #time_real_departure=long_json["time"]["real"]["departure"]
        #time_scheduled_arrival=long_json["time"]["scheduled"]["arrival"]
        #time_estimated_arrival=long_json["time"]["estimated"]["arrival"]
        #latitude=long_json["trail"][0]["lat"]
        #longitude=long_json["trail"][0]["lng"]
        #altitude=long_json["trail"][0]["alt"]
        #speed=long_json["trail"][0]["spd"]
        #heading=long_json["trail"][0]["hd"]


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

        label1_short=safe_text(flight_number)
        label1_long=safe_text(airline_name)
        origin_code=safe_text(airport_origin_code)
        destination_code=safe_text(airport_destination_code)
        origin_name=safe_text(airport_origin_name)
        destination_name=safe_text(airport_destination_name)
        label2_short=(origin_code+"-"+destination_code) if (origin_code or destination_code) else ""
        label2_long=(origin_name+"-"+destination_name) if (origin_name or destination_name) else ""
        label3_short=safe_text(aircraft_code)
        label3_long=safe_text(aircraft_model)


        # optional filter example - check things and return false if you want

        # if altitude > 10000:
        #    print("Altitude Filter matched so don't display anything")
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
        feed_sleep(1)  # Wait after reset
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


# Look for flights overhead
def get_flights():
    matrixportal.url=FLIGHT_SEARCH_URL
    response = None
    try:
        #response = json.loads(matrixportal.fetch())
        response = wifi.get(url=FLIGHT_SEARCH_URL, headers=rheaders)
        data = response.json()
    except (RuntimeError, OSError, HttpError, ValueError, requests.OutOfRetries, TimeoutError) as e:
        print(e.__class__.__name__+"--------------------------------------")
        print(e)
        checkConnection()
        return False
    finally:
        if response is not None:
            response.close()
    if len(data)==3:
        #print ("Flight found.")
        for flight_id, flight_info in data.items():
            # the JSON has three main fields, we want the one that's a flight ID
            if not (flight_id=="version" or flight_id=="full_count"):
                if len(flight_info)>13:
                    return flight_id
    else:
        return False



# Actual doing of things - loop forever quering fr24, processing any results and waiting to query again

checkConnection()

last_flight=''
while True:

    #checkConnection()

    w.feed()

    #print("memory free: " + str(gc.mem_free()))

    #print("Get flights...")
    flight_id=get_flights()
    w.feed()
    

    if flight_id:
        if flight_id==last_flight:
            print("Same flight found, so keep showing it")
        else:
            print("New flight "+flight_id+" found, clear display")
            clear_flight()
            w.feed()
            gc.collect()  # Clear before fetching
            if get_flight_details(flight_id):
                w.feed()
                gc.collect()  # Clear after fetching
                if parse_details_json():
                    w.feed()
                    gc.collect()  # Clear after parsing
                    plane_animation()
                    display_flight()
                    # Clear any remaining objects from display
                    w.feed()
                    gc.collect()
                else:
                    print("error parsing JSON, skip displaying this flight")
                    gc.collect()
            else:
                print("error loading details, skip displaying this flight")
                gc.collect()
            
            last_flight=flight_id
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
