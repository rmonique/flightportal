# Colour themes, decorations and the auto-theme calendar for code.py.
#
# The matrix only has 4 brightness levels per colour channel (0x00, 0x44, 0x88, 0xCC/0xFF),
# so 0x44 is the dimmest that still lights.
#
# Theme keys:
#   row1/row2/row3: flight number, route, aircraft    plane: the plane that flies across
#   clock/date/count/temp: idle clock (count = planes today, temp = outside temperature)
#   night: the dim night clock
#   ring: head of the seconds ring    ring_fill: the filled part
#   ring_fill_2: if set, the fill alternates with this colour in stripes of ring_stripe pixels
#   accent: decorations drawn behind the flight text, a tuple of names in ACCENTS that
#           take turns with each new flight (empty for none)
#   particles: things drifting down behind the idle clock (snow, confetti), or None.
#              {"colours": up to 3 colours, "count": how many}

THEMES = {
    "classic": {
        "row1": 0x888888, "row2": 0xAA5500, "row3": 0x006600, "plane": 0x880000,
        # idle clock: white and grey, with amber temperature and red plane count as the only colours
        "clock": 0xCCCCCC, "date": 0x888888, "count": 0xCC0000, "temp": 0xCC8800, "night": 0x440000,
        "ring": 0xFFFFFF, "ring_fill": 0x444444, "ring_fill_2": None, "ring_stripe": 4,
        "accent": (),
        "particles": None,
    },
    "thanksgiving": {
        "row1": 0xCC4400, "row2": 0xCC8800, "row3": 0x884400, "plane": 0x884400,
        "clock": 0xCC8844, "date": 0xCC4400, "count": 0x880000, "temp": 0xCC8800, "night": 0x440000,
        "ring": 0xFFCC00, "ring_fill": 0xCC4400, "ring_fill_2": 0x884400, "ring_stripe": 4,
        "accent": ("turkey", "maple_leaves"),
        "particles": {"colours": (0xCC4400, 0x880000, 0xCC8800), "count": 8},  # falling leaves
    },
    "christmas": {
        "row1": 0xCC0000, "row2": 0x00AA00, "row3": 0xCC8800, "plane": 0xCC0000,
        "clock": 0x888888, "date": 0x00AA00, "count": 0xCC0000, "temp": 0xCC8800, "night": 0x440000,
        # candy cane: red and white stripes
        "ring": 0xFFFFFF, "ring_fill": 0xCC0000, "ring_fill_2": 0x888888, "ring_stripe": 4,
        "accent": ("holly", "santa", "candy_canes"),
        "particles": {"colours": (0x888888,), "count": 10},  # snow
    },
    "halloween": {
        "row1": 0xCC4400, "row2": 0x880088, "row3": 0x44CC00, "plane": 0x880088,
        "clock": 0xCC4400, "date": 0x880088, "count": 0x44CC00, "temp": 0xCC4400, "night": 0x440000,
        "ring": 0xFF8800, "ring_fill": 0xCC4400, "ring_fill_2": 0x880088, "ring_stripe": 4,
        "accent": ("pumpkin_bat", "ghost"),
        "particles": {"colours": (0xCC4400, 0x880088), "count": 6},  # autumn leaves
    },
    "july4": {
        "row1": 0xCC0000, "row2": 0x888888, "row3": 0x0044CC, "plane": 0x0044CC,
        "clock": 0x888888, "date": 0xCC0000, "count": 0x0044CC, "temp": 0x888888, "night": 0x440000,
        "ring": 0x0044FF, "ring_fill": 0xCC0000, "ring_fill_2": 0x888888, "ring_stripe": 4,
        "accent": ("flag_firework",),
        "particles": {"colours": (0xCC0000, 0x888888, 0x0044CC), "count": 10},
    },
    "newyear": {
        "row1": 0xCC8800, "row2": 0x888888, "row3": 0xCCCCCC, "plane": 0xCC8800,
        "clock": 0xCCCCCC, "date": 0xCC8800, "count": 0x888888, "temp": 0x0088CC, "night": 0x440000,
        "ring": 0xFFFFFF, "ring_fill": 0xCC8800, "ring_fill_2": 0x888888, "ring_stripe": 4,
        "accent": ("fireworks_champagne",),
        "particles": {"colours": (0xCC8800, 0x888888, 0xCC0088), "count": 10},  # confetti
    },
    "valentines": {
        "row1": 0xFF4488, "row2": 0xCC0000, "row3": 0xCC88CC, "plane": 0xCC0000,
        "clock": 0x888888, "date": 0xFF4488, "count": 0xCC0000, "temp": 0xCC88CC, "night": 0x440000,
        "ring": 0xFFFFFF, "ring_fill": 0xCC0000, "ring_fill_2": 0xFF4488, "ring_stripe": 4,
        "accent": ("hearts",),
        "particles": {"colours": (0xCC0000, 0xFF4488), "count": 8},
    },
}

# THEME = "auto" picks a theme from the date: (start month, day), (end month, day), theme.
# Ranges can wrap past the new year. Any other date uses "classic".
AUTO_THEMES = (
    ((2, 7), (2, 14), "valentines"),
    ((7, 1), (7, 5), "july4"),
    ((10, 15), (10, 31), "halloween"),
    ((11, 15), (11, 30), "thanksgiving"),  # US Thanksgiving is always Nov 22-28
    ((12, 1), (12, 26), "christmas"),
    ((12, 27), (1, 2), "newyear"),
)


def auto_theme(month, day):
    key = month * 100 + day
    for start, end, name in AUTO_THEMES:
        s = start[0] * 100 + start[1]
        e = end[0] * 100 + end[1]
        if (s <= key <= e) if s <= e else (key >= s or key <= e):
            return name
    return "classic"


# ---- Decorations ----
# Pixel art: one string per row, "." = transparent, other characters coloured by "colours".
# Each accent is one or more parts: (rows, colours, placements). A placement is
# (x, y, flip_upside_down); negative x/y count from the right/bottom edge (-1 = flush
# against it, -2 = one pixel in), y None = centred vertically.

HOLLY = (
    "......L..L..L..",
    ".....LLLLLLLLL.",
    "....LLllllLLlLL",
    ".....LLLLLLLlL.",
    "......L..L.LlL.",
    "......RR.RRLlLL",
    "......rR.rRLlL.",
    "...........LlL.",
    "........RRLLlLL",
    "........rR.LLL.",
    "............L..",
)
SANTA = (
    ".....RRRR....",
    "....RRRRRR...",
    "...RRRRRRRR..",
    "..RRRRRRRRRRW",
    ".WWWWWWWWWWWW",
    ".WWWWWWWWWW..",
    "..SSSSSSSS...",
    "..S.SSSS.S...",
    "..SSSSSSSS...",
    ".WSSSRRSSSW..",
    ".WWSWWWWSWW..",
    ".WWWWWWWWWW..",
    "..WWWWWWWW...",
    "...WWWWWW....",
    ".....WW......",
)
_CANE_SHAPE = (
    "..SSS..",
    ".SSSSS.",
    "SS...SS",
    "SS...SS",
    "S....SS",
    ".....SS",
    ".....SS",
    ".....SS",
    ".....SS",
    ".....SS",
    ".....SS",
    ".....SS",
    ".....SS",
)
# striped red and white diagonally
CANE = tuple(
    "".join((("R" if ((x + y) // 2) % 2 == 0 else "W") if c == "S" else ".") for x, c in enumerate(row))
    for y, row in enumerate(_CANE_SHAPE)
)
PUMPKIN = (
    "....GG.....",
    ".....G.....",
    "..ooOOOoo..",
    ".oOOOOOOOo.",
    "oOYYYOYYYOo",
    "oOOYOOOYOOo",
    "oOOOOYOOOOo",
    "oOYOYOYOYOo",
    ".oOYYYYYOo.",
    "..ooOOOoo..",
)
BAT = (
    "P...P.P...P",
    "PP..PPP..PP",
    "PPPPPPPPPPP",
    ".PPP.P.PPP.",
    "..P.....P..",
)
GHOST = (
    "..WWWWW..",
    ".WWWWWWW.",
    "WW.WWW.WW",
    "WW.WWW.WW",
    "WWWWWWWWW",
    "WWWW.WWWW",
    "WWWWWWWWW",
    "WWWWWWWWW",
    "WW.WWW.WW",
    "W...W...W",
)
FLAG = (
    "BBBBBRRRRRRRR",
    "BWBWBWWWWWWWW",
    "BBBBBRRRRRRRR",
    "BWBWBWWWWWWWW",
    "RRRRRRRRRRRRR",
    "WWWWWWWWWWWWW",
    "RRRRRRRRRRRRR",
)
FIREWORK = (
    ".....T.....",
    "..T..A..T..",
    "...A.A.A...",
    "....AAA....",
    "TAAAACAAAAT",
    "....AAA....",
    "...A.A.A...",
    "..T..A..T..",
    ".....T.....",
)
CHAMPAGNE = (
    "..W.W..",
    "...W...",
    ".S...S.",
    ".SYYYS.",
    ".SYWYS.",
    ".SYYYS.",
    ".SYYYS.",
    "..SYS..",
    "...S...",
    "...S...",
    "...S...",
    ".SSSSS.",
)
HEART = (
    ".HH...HH.",
    "HhHH.HHHH",
    "HhHHHHHHH",
    "HHHHHHHHH",
    ".HHHHHHH.",
    "..HHHHH..",
    "...HHH...",
    "....H....",
)
SMALL_HEART = (
    ".HH.HH.",
    "HHHHHHH",
    "HHHHHHH",
    ".HHHHH.",
    "..HHH..",
    "...H...",
)

TURKEY = (
    "...ROYYYOR...",
    ".RROOYYYOORR.",
    "RROOYYYYYOORR",
    "RROOYYBYYOORR",
    "ROOYYB.BYYOOR",
    "ROOYBBYBBYOOR",
    ".OOBBBrBBBOO.",
    "..OBBBrBBBO..",
    "...BBBBBBB...",
    "...BBBBBBB...",
    "....BBBBB....",
    "....Y...Y....",
)
MAPLE = (
    ".....L.....",
    "..L..L..L..",
    "..LL.L.LL..",
    "L.LLLLLLL.L",
    "LLLLLLLLLLL",
    ".LLLLLLLLL.",
    "..LLLLLLL..",
    "...LLLLL...",
    "..LL.L.LL..",
    ".....L.....",
    ".....L.....",
)

ACCENTS = {
    # Thanksgiving
    "turkey": (
        (TURKEY, {"R": 0xCC0000, "O": 0xCC4400, "Y": 0xCC8800, "B": 0x884400, "r": 0xCC0000}, ((-2, None, False),)),
    ),
    "maple_leaves": (
        (MAPLE, {"L": 0xCC4400}, ((-2, 1, False),)),
        (MAPLE, {"L": 0x880000}, ((-14, -2, False),)),
    ),
    # Christmas
    "holly": (
        # leaves along the top edge and down the side, berries tucked inside;
        # in the top-right corner and mirrored into the bottom-right
        (HOLLY, {"L": 0x008800, "l": 0x00CC00, "R": 0xCC0000, "r": 0x880000}, ((-1, 0, False), (-1, -1, True))),
    ),
    "santa": (
        (SANTA, {"R": 0xCC0000, "W": 0xCCCCCC, "S": 0xFF8844}, ((-2, None, False),)),
    ),
    "candy_canes": (
        (CANE, {"R": 0xCC0000, "W": 0xCCCCCC}, ((-2, 1, False), (-2, -2, False), (-11, -2, False))),
    ),
    # Halloween
    "pumpkin_bat": (
        (BAT, {"P": 0x880088}, ((-2, 1, False),)),
        (PUMPKIN, {"G": 0x008800, "O": 0xCC4400, "o": 0x882200, "Y": 0xCCCC00}, ((-2, -2, False),)),
    ),
    "ghost": (
        (GHOST, {"W": 0x888888}, ((-2, None, False),)),
    ),
    # 4th of July
    "flag_firework": (
        (FLAG, {"B": 0x0000CC, "W": 0x888888, "R": 0xCC0000}, ((-1, 0, False),)),
        (FIREWORK, {"A": 0x0044FF, "T": 0xCC0000, "C": 0xFFFFFF}, ((-2, -2, False),)),
    ),
    # New Year
    "fireworks_champagne": (
        (FIREWORK, {"A": 0xCC8800, "T": 0x888888, "C": 0xFFFFFF}, ((-1, 0, False),)),
        (CHAMPAGNE, {"S": 0x888888, "Y": 0xCC8800, "W": 0xFFFFFF}, ((-3, -1, False),)),
    ),
    # Valentine's Day
    "hearts": (
        (HEART, {"H": 0xCC0000, "h": 0xFF88CC}, ((-2, 1, False),)),
        (SMALL_HEART, {"H": 0xFF4488}, ((-2, -2, False),)),
    ),
}
