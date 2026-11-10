import time
import os
import board
import terminalio
from adafruit_matrixportal.matrix import Matrix
import adafruit_requests as requests
import json
from json_filter import JsonFilter
import clock
import themes
import random

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

# Clock and night mode. Both need UTC_OFFSET (standard time, in hours, e.g. "-5" for US Eastern).
UTC_OFFSET=clock.parse_offset(os.getenv("UTC_OFFSET"))
DST_RULE=str(os.getenv("DST_RULE") or "none").strip().upper()  # "US", "EU" or "none"
# Show the time and date when no flight is overhead
SHOW_CLOCK=setting_bool("SHOW_CLOCK", True)
CLOCK_24H=setting_bool("CLOCK_24H", False)
# Show how many planes have been shown today in the top-right corner of the idle clock
SHOW_COUNT=setting_bool("SHOW_COUNT", True)
# Show the outside temperature in the top-left of the idle clock, in "C" or "F"
SHOW_TEMP=setting_bool("SHOW_TEMP", True)
TEMP_UNIT="F" if str(os.getenv("TEMP_UNIT") or "C").strip().upper().startswith("F") else "C"
# The plane animation flies the way the real plane is going (east = right).
# Set True if your display faces north, so east is on your left.
FLIP_PLANE_DIRECTION=setting_bool("FLIP_PLANE_DIRECTION", False)
# The edge of the display fills up over each minute on the idle clock
SECONDS_RING=setting_bool("SECONDS_RING", True)
# Night mode: between these local hours (0-23) don't look up flights. -1 = off.
NIGHT_START=setting_int("NIGHT_START", -1)
NIGHT_END=setting_int("NIGHT_END", -1)
if NIGHT_START<0 or NIGHT_END<0:
    NIGHT_START=NIGHT_END=None
# At night show a dim clock (True) or turn the display off (False)
NIGHT_CLOCK=setting_bool("NIGHT_CLOCK", True)
if UTC_OFFSET is None and (SHOW_CLOCK or NIGHT_START is not None):
    print("Clock and night mode need UTC_OFFSET in settings.toml")

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

# Colour theme: a name in themes.THEMES, or "auto" to pick one from the date (themes.AUTO_THEMES).
# Colours are applied by apply_theme() once everything is set up, and again when "auto" switches.
THEME_SETTING=str(os.getenv("THEME") or "classic").strip().lower()
if THEME_SETTING!="auto" and THEME_SETTING not in themes.THEMES:
    print("Unknown THEME "+THEME_SETTING+", using classic")
    THEME_SETTING="classic"
theme_name=None
CLOCK_COLOUR=0x888888
NIGHT_COLOUR=0x440000
RING_STRIPE=4
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
planePalette[1] = 0x880000
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
    color=0x888888,
    text="")
label1.x = 1
label1.y = 4

label2 = adafruit_display_text.label.Label(
    FONT,
    color=0x888888,
    text="")
label2.x = 1
label2.y = 15

label3 = adafruit_display_text.label.Label(
    FONT,
    color=0x888888,
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

# Theme decorations drawn behind the flight text (see themes.ACCENTS). Built when a theme is applied.
def make_accent_group(name):
    parts=themes.ACCENTS.get(name)
    if parts is None:
        print("Unknown accent "+str(name))
        return None
    group=displayio.Group()
    for rows, colours, placements in parts:
        w=len(rows[0])
        h=len(rows)
        chars=list(colours.keys())
        bitmap=displayio.Bitmap(w, h, len(chars)+1)
        palette=displayio.Palette(len(chars)+1)
        palette[0]=0x000000
        palette.make_transparent(0)
        for i, ch in enumerate(chars):
            palette[i+1]=colours[ch]
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch in colours:
                    bitmap[x, y]=chars.index(ch)+1
        for x, y, flip in placements:
            if x<0:
                x=display.width-w+x+1
            if y is None:
                y=(display.height-h)//2
            elif y<0:
                y=display.height-h+y+1
            tg=displayio.TileGrid(bitmap, pixel_shader=palette, x=x, y=y)
            tg.flip_y=flip  # not accepted as a constructor argument
            group.append(tg)
    return group

# The theme's decorations take turns: each new flight gets the next one
accent_groups=[]
accent_index=-1

# Add the labels to the display, on top of any theme decoration (slot 0)
g = displayio.Group()
g.append(displayio.Group())
g.append(label1)
g.append(label2)
g.append(label3)
display.root_group = g

# Heading (degrees, 0=north, 90=east) of the flight being shown, from the search results
flight_heading=None

# Should the plane fly left to right? Eastbound flights do, as on a map with north up.
# FLIP_PLANE_DIRECTION swaps this, e.g. if the display faces north so east is on your left.
def plane_flies_right():
    if flight_heading is None or flight_heading%180==0:
        return False  # unknown, or due north/south: use the original right-to-left
    eastbound=0<flight_heading%360<180
    return eastbound!=FLIP_PLANE_DIRECTION

# Scroll the plane bitmap across the screen in the direction the real plane is flying
# (the bitmap points left, so it's mirrored to fly right)
def plane_animation():
    display.root_group = planeG
    right=plane_flies_right()
    print("Heading "+str(flight_heading)+", plane flies "+("right" if right else "left"))
    planeTg.flip_x=right
    if right:
        positions=range(-24, display.width+12)
    else:
        positions=range(display.width+24, -12, -1)
    for i in positions:
            planeG.x=i
            w.feed()
            feed_sleep(PLANE_SPEED, step_seconds=PLANE_SPEED)

# Put the theme's next decoration behind the flight text (no-op without accents)
def next_accent():
    global accent_index
    if not accent_groups:
        return
    accent_index=(accent_index+1)%len(accent_groups)
    g[0]=accent_groups[accent_index]

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

    next_accent()
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


# ---- Clock and night mode ----

# Time comes from the Date header of FR24's search responses (esp.get_time() in
# adafruit_esp32spi fails with ESP32 firmware 3.x). We remember the UTC time at a
# point on the monotonic clock and count forward from there, resyncing every few hours.
TIME_RESYNC_NS=3*3600*1000000000
utc_base=None
mono_base=0
last_time_sync=0

def update_time_from_response(response):
    global utc_base, mono_base, last_time_sync
    if UTC_OFFSET is None:
        return
    now=time.monotonic_ns()
    if utc_base is not None and now-last_time_sync<TIME_RESYNC_NS:
        return
    t=clock.parse_http_date(get_header(response, "Date"))
    if t is None:
        return
    first=utc_base is None
    utc_base=t
    mono_base=now
    last_time_sync=now
    if first:
        lt=clock.local_time(t, UTC_OFFSET, DST_RULE)
        print("Time set: "+clock.format_date(lt[1], lt[2], lt[6])+" "+clock.format_time(lt[3], lt[4], True))

# Local time as (year, month, day, hour, minute, second, weekday), or None if unknown
def local_now():
    if utc_base is None:
        return None
    utc=utc_base+(time.monotonic_ns()-mono_base)//1000000000
    return clock.local_time(utc, UTC_OFFSET, DST_RULE)

# ---- Planes-seen-today counter ----
# Counts flights shown on the display, resetting at local midnight. Saved in the
# chip's non-volatile memory (microcontroller.nvm) so it survives restarts:
# 2 magic bytes, 2 bytes local day number, 2 bytes count.
COUNT_MAGIC=b"\xf1\x1c"
count_day=-1
count_value=0

def load_count():
    global count_day, count_value
    try:
        nvm=microcontroller.nvm
        if nvm is not None and len(nvm)>=6 and nvm[0:2]==COUNT_MAGIC:
            count_day=nvm[2]<<8|nvm[3]
            count_value=nvm[4]<<8|nvm[5]
    except Exception as e:
        print("Could not load plane count:", e)

def save_count():
    try:
        nvm=microcontroller.nvm
        if nvm is not None and len(nvm)>=6:
            nvm[0:6]=COUNT_MAGIC+bytes((count_day>>8&0xFF, count_day&0xFF, count_value>>8&0xFF, count_value&0xFF))
    except Exception as e:
        print("Could not save plane count:", e)

def local_day(lt):
    return clock.days_from_civil(lt[0], lt[1], lt[2])

# Planes shown so far today (0 if the saved count is from an earlier day)
def planes_today(lt):
    return count_value if lt is not None and local_day(lt)==count_day else 0

def count_plane():
    global count_day, count_value
    lt=local_now()
    if lt is None:
        return  # don't know which day it is yet
    today=local_day(lt)
    if today!=count_day:
        count_day=today
        count_value=0
    count_value=min(count_value+1, 0xFFFF)
    save_count()
    print("Planes today:", count_value)

load_count()

# ---- Outside temperature (Open-Meteo, free, no key) ----
# Looked up for the centre of BOUNDS_BOX, rounded to 2 decimal places (~1km).
WEATHER_INTERVAL_NS=30*60*1000000000  # refresh every 30 minutes
WEATHER_RETRY_NS=5*60*1000000000      # after a failure, try again in 5 minutes
WEATHER_STALE_NS=2*3600*1000000000    # stop showing a reading older than this
temp_value=None
temp_time=0
last_weather_attempt=None
# The built-in font has no degree sign, so unless it does, a tiny circle is drawn
# between the number and the unit letter
FONT_HAS_DEGREE=FONT.get_glyph(0xB0) is not None
DEGREE="°" if FONT_HAS_DEGREE else ""

def weather_url():
    try:
        b=[float(v) for v in BOUNDS_BOX.split(",")]
        lat="%.2f" % ((b[0]+b[1])/2)
        lon="%.2f" % ((b[2]+b[3])/2)
    except (ValueError, IndexError):
        return None
    unit="fahrenheit" if TEMP_UNIT=="F" else "celsius"
    return ("https://api.open-meteo.com/v1/forecast?latitude="+lat+"&longitude="+lon+
            "&current=temperature_2m&temperature_unit="+unit)

def update_weather():
    global temp_value, temp_time, last_weather_attempt
    if not SHOW_TEMP:
        return
    now=time.monotonic_ns()
    if last_weather_attempt is not None:
        wait=WEATHER_INTERVAL_NS if (temp_value is not None and now-temp_time<WEATHER_INTERVAL_NS*2) else WEATHER_RETRY_NS
        if now-last_weather_attempt<wait:
            return
    last_weather_attempt=now
    url=weather_url()
    if url is None:
        print("Can't work out a location for the temperature from BOUNDS_BOX")
        return
    response=None
    try:
        w.feed()
        response=wifi.get(url, headers={"Accept": "application/json"}, timeout=REQUEST_TIMEOUT)
        if response.status_code!=200:
            print("Weather HTTP status:", response.status_code)
            return
        t=safe_get(response.json(), "current", "temperature_2m")
        if t is None:
            print("Weather response had no temperature")
            return
        temp_value=round(t)
        temp_time=now
        print("Temperature:", str(temp_value)+TEMP_UNIT)
    except (RuntimeError, OSError, ValueError, TimeoutError, MemoryError, requests.OutOfRetries) as e:
        print("Weather error:", e)
    finally:
        if response is not None:
            response.close()
        w.feed()
        gc.collect()

def current_temp_text():
    if not SHOW_TEMP or temp_value is None or time.monotonic_ns()-temp_time>WEATHER_STALE_NS:
        return ""
    return str(temp_value)+DEGREE+TEMP_UNIT

def in_night():
    if NIGHT_START is None:
        return False
    lt=local_now()
    return lt is not None and clock.is_night(lt[3], NIGHT_START, NIGHT_END)

clock_label = adafruit_display_text.label.Label(FONT, color=0x888888, text="")
date_label = adafruit_display_text.label.Label(FONT, color=0x888888, text="")
count_label = adafruit_display_text.label.Label(FONT, color=0x888888, text="")
# Centred time and date. Once a plane has been shown today, its count goes in the
# top-right corner and the time and date move down a little to make room.
# The outer rows/columns are left clear for the seconds ring.
CLOCK_Y=(10, 23)             # time, date with no count
CLOCK_Y_WITH_COUNT=(15, 25)  # time, date with the count above
count_label.y=5
COUNT_RIGHT=display.width-2  # count's right edge, inside the ring
temp_label = adafruit_display_text.label.Label(FONT, color=0x888888, text="")
temp_label.x=2
temp_label.y=5
# Unit letter, used when the degree sign is drawn so it can sit 1 pixel after it
unit_label = adafruit_display_text.label.Label(FONT, color=0x888888, text="")
unit_label.y=5
DEGREE_SIZE=2  # a solid 2x2 square
degree_bitmap=displayio.Bitmap(DEGREE_SIZE, DEGREE_SIZE, 2)
degree_bitmap.fill(1)
degree_palette=displayio.Palette(2)
degree_palette[0]=0x000000
degree_palette.make_transparent(0)
degree_palette[1]=0x888888
degree_tg=displayio.TileGrid(degree_bitmap, pixel_shader=degree_palette)
degree_tg.hidden=True

# Tiny plane icon (nose pointing right) shown beside the count
ICON_ROWS=(
    "...#...",
    "#..##..",
    "#######",
    "#..##..",
    "...#...",
)
ICON_W=len(ICON_ROWS[0])
icon_bitmap=displayio.Bitmap(ICON_W, len(ICON_ROWS), 2)
icon_palette=displayio.Palette(2)
icon_palette[0]=0x000000
icon_palette.make_transparent(0)
icon_palette[1]=0x888888
for iy, row in enumerate(ICON_ROWS):
    for ix, ch in enumerate(row):
        if ch=="#":
            icon_bitmap[ix, iy]=1
icon_tg=displayio.TileGrid(icon_bitmap, pixel_shader=icon_palette, y=3)
icon_tg.hidden=True
# Seconds ring: the edge of the display fills clockwise from top centre over each minute,
# with a brighter head at the current second, then clears for the next minute.
# Drawn into a 2-bit bitmap (512 bytes).
RING_W=display.width
RING_H=display.height
RING_LEN=2*(RING_W+RING_H)-4
ring_bitmap=displayio.Bitmap(RING_W, RING_H, 4)
ring_palette=displayio.Palette(4)
ring_palette[0]=0x000000
ring_palette.make_transparent(0)
ring_palette[1]=0xFFFFFF  # head
ring_palette[2]=0x888888  # the part of the minute already gone
ring_palette[3]=0x888888  # second stripe colour
ring_tg=displayio.TileGrid(ring_bitmap, pixel_shader=ring_palette)
ring_pos=-1

def ring_xy(i):
    # Position i along the edge, starting at top centre and going clockwise
    i=(i+RING_W//2)%RING_LEN
    if i<RING_W:
        return i, 0
    i-=RING_W
    if i<RING_H-1:
        return RING_W-1, i+1
    i-=RING_H-1
    if i<RING_W-1:
        return RING_W-2-i, RING_H-1
    i-=RING_W-1
    return 0, RING_H-2-i

def update_ring():
    global ring_pos
    # Sub-second position within the current minute (UTC and local seconds are the same)
    ns=(utc_base%60)*1000000000+(time.monotonic_ns()-mono_base)
    pos=(ns%60000000000)*RING_LEN//60000000000
    if pos==ring_pos:
        return
    if ring_pos<0 or pos<ring_pos:
        # New minute (or first draw): start again from the top
        ring_bitmap.fill(0)
        start=0
    else:
        # Only draw what's new since last time; the old head becomes fill
        start=ring_pos
    for i in range(start, pos):
        x, y=ring_xy(i)
        ring_bitmap[x, y]=2+(i//RING_STRIPE)%2  # palette 2/3 alternate in stripes (same colour if no stripes)
    x, y=ring_xy(pos)
    ring_bitmap[x, y]=1
    ring_pos=pos

clock_group = displayio.Group()
clock_group.append(ring_tg)
clock_group.append(clock_label)
clock_group.append(date_label)
clock_group.append(count_label)
clock_group.append(icon_tg)
clock_group.append(temp_label)
clock_group.append(degree_tg)
clock_group.append(unit_label)
blank_group = displayio.Group()

# ---- Particles: snow/confetti drifting down behind the idle clock ----
# A full-screen 2-bit bitmap (512 bytes); each particle is one pixel kept off the edge rows/columns
# so it doesn't touch the seconds ring.
PARTICLE_STEP_NS=250000000  # move every 0.25s
particle_bitmap=displayio.Bitmap(display.width, display.height, 4)
particle_palette=displayio.Palette(4)
particle_palette[0]=0x000000
particle_palette.make_transparent(0)
particle_tg=displayio.TileGrid(particle_bitmap, pixel_shader=particle_palette)
particle_tg.hidden=True
clock_group.insert(1, particle_tg)  # above the ring, behind the text
particles=[]  # [x, y, colour index]
last_particle_ns=0

def setup_particles(config):
    global particles
    particle_bitmap.fill(0)
    particles=[]
    if not config:
        return
    colours=config["colours"][:3]
    for i, c in enumerate(colours):
        particle_palette[i+1]=c
    for i in range(config["count"]):
        particles.append([random.randint(1, display.width-2), random.randint(1, display.height-2), 1+i%len(colours)])

def update_particles():
    global last_particle_ns
    now=time.monotonic_ns()
    if now-last_particle_ns<PARTICLE_STEP_NS:
        return
    last_particle_ns=now
    for p in particles:
        particle_bitmap[p[0], p[1]]=0
        p[1]+=1
        p[0]=min(display.width-2, max(1, p[0]+random.choice((-1, 0, 0, 1))))  # drift
        if p[1]>display.height-2:
            p[0]=random.randint(1, display.width-2)
            p[1]=1
        particle_bitmap[p[0], p[1]]=p[2]

def particles_active(night=False):
    return bool(particles) and not night

# ---- Applying a theme ----
def apply_theme(name):
    global theme_name, CLOCK_COLOUR, NIGHT_COLOUR, RING_STRIPE, accent_groups, accent_index, clock_shown_key, ring_pos
    t=themes.THEMES[name]
    label1.color=t["row1"]
    label2.color=t["row2"]
    label3.color=t["row3"]
    planePalette[1]=t["plane"]
    CLOCK_COLOUR=t["clock"]
    NIGHT_COLOUR=t["night"]
    date_label.color=t["date"]
    count_label.color=t["count"]
    icon_palette[1]=t["count"]
    temp_label.color=t["temp"]
    degree_palette[1]=t["temp"]
    unit_label.color=t["temp"]
    ring_palette[1]=t["ring"]
    ring_palette[2]=t["ring_fill"]
    ring_palette[3]=t["ring_fill_2"] if t["ring_fill_2"] is not None else t["ring_fill"]
    RING_STRIPE=t["ring_stripe"]
    ring_pos=-1  # redraw the ring in the new colours
    # Decorations: drop the old theme's before building the new ones
    g[0]=displayio.Group()
    accent_groups=[]
    gc.collect()
    for accent in t["accent"]:
        ag=make_accent_group(accent)
        if ag is not None:
            accent_groups.append(ag)
    accent_index=-1
    setup_particles(t["particles"])
    clock_shown_key=None  # redraw the clock text
    theme_name=name
    gc.collect()
    print("Theme:", name)

# With THEME = "auto", switch theme when the date calls for it (needs the time)
def check_auto_theme():
    if THEME_SETTING!="auto":
        return
    lt=local_now()
    if lt is None:
        return
    wanted=themes.auto_theme(lt[1], lt[2])
    if wanted!=theme_name:
        apply_theme(wanted)

def set_centered(label, text):
    # Only touch the label when the text changes, to avoid needless allocations
    if label.text!=text:
        label.text=text
        label.x=max(0, (display.width-label.bounding_box[2])//2)

clock_shown_key=None

def ring_active(night=False):
    return SECONDS_RING and not night and utc_base is not None

# Show the clock (dim and without the date at night). Returns False if the time isn't known.
def show_clock(night=False):
    global clock_shown_key
    lt=local_now()
    if lt is None:
        return False
    # Top row: temperature on the left, plane icon + today's count on the right
    planes=planes_today(lt) if (SHOW_COUNT and not night) else 0
    temp=current_temp_text() if not night else ""
    # Text only needs redrawing when the minute, day/night, count or temperature changes
    key=(((lt[3]*60+lt[4])*2+(1 if night else 0))*100000+planes, temp)
    if key!=clock_shown_key:
        clock_shown_key=key
        set_centered(clock_label, clock.format_time(lt[3], lt[4], CLOCK_24H))
        if temp and not FONT_HAS_DEGREE:
            # "46", then the drawn degree circle, then "F", each 1 pixel apart. Characters are
            # 6 pixels wide with the last column blank, which gives the gap after the number.
            number=str(temp_value)
            temp_label.text=number
            degree_tg.x=temp_label.x+len(number)*6
            # level with the top of the digits (which start 2 rows into the font's 12-row cell)
            degree_tg.y=temp_label.y+temp_label.bounding_box[1]+2
            degree_tg.hidden=False
            unit_label.text=TEMP_UNIT
            unit_label.x=degree_tg.x+DEGREE_SIZE+1
        else:
            temp_label.text=temp
            degree_tg.hidden=True
            unit_label.text=""
        if planes:
            count_label.text=str(planes)
            count_label.x=COUNT_RIGHT-count_label.bounding_box[2]
            icon_tg.x=count_label.x-ICON_W-2
            icon_tg.hidden=False
        else:
            count_label.text=""
            icon_tg.hidden=True
        if night:
            clock_label.color=NIGHT_COLOUR
            set_centered(date_label, "")
            clock_label.y=CLOCK_Y[0]
        else:
            clock_label.color=CLOCK_COLOUR
            set_centered(date_label, clock.format_date(lt[1], lt[2], lt[6]))
            if planes or temp:
                clock_label.y, date_label.y=CLOCK_Y_WITH_COUNT
            else:
                clock_label.y, date_label.y=CLOCK_Y
    particle_tg.hidden=not particles_active(night)
    if not particle_tg.hidden:
        update_particles()
    ring_tg.hidden=not ring_active(night)
    if not ring_tg.hidden:
        update_ring()
    if display.root_group is not clock_group:
        display.root_group=clock_group
    return True

# Idle screen when no flight is overhead: the clock if enabled, otherwise blank labels
def show_idle():
    if not (SHOW_CLOCK and show_clock()):
        display.root_group=g

def show_night():
    if not (NIGHT_CLOCK and show_clock(night=True)):
        display.root_group=blank_group

# Wait, keeping the watchdog fed and the clock on screen up to date.
# Ticks quickly while the seconds ring is running so it moves smoothly.
def idle_wait(total_seconds, night=False):
    end=time.monotonic()+total_seconds
    while True:
        w.feed()
        if night:
            show_night()
        else:
            show_idle()
        remaining=end-time.monotonic()
        if remaining<=0:
            break
        step=0.1 if (SHOW_CLOCK and (ring_active(night) or particles_active(night))) else 5
        time.sleep(min(step, remaining))


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
        update_time_from_response(response)
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
    global flight_heading
    best_id=None
    best_alt=None
    best_heading=None
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
            best_heading=flight_info[3] if isinstance(flight_info[3], int) else None
    del data
    if best_id:
        flight_heading=best_heading
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
was_night=False
# "auto" starts as classic until the first search tells us the date
apply_theme("classic" if THEME_SETTING=="auto" else THEME_SETTING)
while True:

    w.feed()
    check_auto_theme()

    # Night mode: no flight lookups, just a dim clock or a dark display
    if in_night():
        if not was_night:
            print("Night mode on until "+str(NIGHT_END)+":00 - pausing flight lookups")
            was_night=True
            last_flight=''
            clear_flight()
        idle_wait(60, night=True)
        continue
    if was_night:
        print("Night mode off - resuming flight lookups")
        was_night=False

    update_weather()
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
                    count_plane()
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
        # No flight overhead - blank the flight labels and show the idle clock
        clear_flight()

    # Aggressive garbage collection to avoid memory fragmentation
    w.feed()
    gc.collect()

    # wait QUERY_DELAY seconds but keep watchdog fed in 5s intervals
    if flight_id:
        feed_sleep(QUERY_DELAY, step_seconds=5)
    else:
        idle_wait(QUERY_DELAY)
    w.feed()
    gc.collect()
