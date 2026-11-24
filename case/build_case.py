"""Flight Portal case v4: one-piece front shell + back cover, for MJF PA12 or FDM (PETG).

Remix of "Case for P4 64x32 LED RGB Matrix with Adafruit Matrixportal (Improved)"
by tip2tail (Thingiverse 5793070), CC-BY.

Frame: X = length (right when viewed from the front), Y = up, Z = depth (0 = front face,
increasing toward the back). Units mm.
Usage: python build_case.py OUT_DIR [panel_thickness] [--fdm]
  --fdm  print-friendly variant: shell printed front-face down, cover printed outside-face down,
         no unsupported overhangs, stronger snap hooks, FDM hole/clearance allowances.
"""
import sys, math
import numpy as np
from manifold3d import Manifold, CrossSection, JoinType, OpType, set_circular_segments

set_circular_segments(64)
FDM = "--fdm" in sys.argv
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = ARGS[0]

# ---------------- parameters ----------------
PANEL_T   = float(ARGS[1]) if len(ARGS) > 1 else 15.0  # LED face -> back of frame (Adafruit 2278 listing: 255 x 127 x 15 mm)
PANEL_L, PANEL_H = 255.7, 127.7          # Adafruit 2278 drawing
DIFF_L, DIFF_H, DIFF_T = 259.1, 129.5, 2.6  # Adafruit black diffusion acrylic
DIFF_CLR  = 0.2                          # per side
WALL      = 4.0                          # 4 mm keeps ~3.1 mm through the rounded corners (3 mm left only 1.7)
LIP_T     = 1.6                          # front face thickness
LIP_OVER  = 0.5                          # window inset beyond panel edge, per side
BEVEL     = 1.0                          # 45deg chamfer on window front edge
PRESS     = 0.2                          # panel/diffuser clamp interference
# Ribbon connection under the MatrixPortal: board 2x10 socket -> male-male header -> ribbon IDC plug.
# Adafruit #400 "extra-long" break-away header. Pin lengths are not published: MEASURE and update.
HDR_PIN_LONG, HDR_PIN_SHORT, HDR_BODY = 12.0, 3.5, 2.5   # 18 mm overall (measured); split assumed lopsided = worst case
BSOCK_H, BSOCK_DEPTH = 8.5, 6.0          # MatrixPortal 2x10 socket: height, usable pin depth
IDC_H, IDC_DEPTH = 9.0, 6.0              # ribbon IDC plug: height incl. ribbon, usable pin depth
RIBBON_BEND = 4.0                        # room for the ribbon to fold over toward the panel
# worst case: the longer pin side goes into either connector and bottoms out
HDR_EXPOSED = HDR_BODY + max(0.0, HDR_PIN_LONG - min(BSOCK_DEPTH, IDC_DEPTH)) + max(0.0, HDR_PIN_SHORT - max(BSOCK_DEPTH, IDC_DEPTH))
STACK_GAP = BSOCK_H + HDR_EXPOSED + IDC_H + RIBBON_BEND   # panel back -> MatrixPortal underside
COMP_H    = 4.0                          # tallest part on MatrixPortal top side
TOP_CLR   = 2.5                          # also clears the extension's plug overmold
COVER_T   = 2.5
REBATE_W  = 2.0                          # cover sits in a rebate, outer skin stays 2.0 wide
COVER_CLR = 0.3 if FDM else 0.2
R_OUT     = 5.0                          # outer corner radius (front view)
R_FRONT   = 2.0                          # front edge round-over

PCB_L, PCB_W, PCB_T = 63.5, 44.45, 1.6   # MatrixPortal M4 fab print (2.50" x 1.75")
POST_FROM_HUB_EDGE, POST_SPACING = 5.08, 35.81
LUG_T     = 0.8                          # power ring-lug thickness (sits on top of the board's standoff)
BSTANDOFF_H = 3.0                        # MatrixPortal's own M3 power standoffs (measured)
POST_H_MIN = 1.5                         # shortest cover pad that lands on each standoff
# Power posts: short metal M3 pan head screws from outside the cover (metal, so they don't strip),
# heads recessed below the surface so no live metal stands proud.
PWR_SCREW_L, PWR_HEAD_H, PWR_HEAD_R = 6.0, 2.4, 2.8   # M3x6 pan head (ISO 7045: 5.6 mm head, 2.4 tall)
PWR_HEAD_SINK = 0.5                      # head top this far below the cover's back face
PWR_PAD_R = 5.0                          # pad radius (wide enough to surround the head recess)
BOARD_INSET = 25.0                       # MatrixPortal's power/HUB75 edge -> +X end wall: room for the lugs and cables
HOOK_U    = 12.0                         # snap hooks: distance from USB edge (fab-print u)
HOOK_W, HOOK_T, HOOK_CATCH, HOOK_GAP = (6.0, 2.0, 0.6, 0.4) if FDM else (5.0, 1.2, 0.8, 0.4)
PILOT_R = 1.4 if FDM else 1.3            # M3 thread-forming pilot radius (FDM holes print undersize)
CLEAR_R = 1.75 if FDM else 1.7           # M3 clearance radius

# panel M3 brass inserts (from centre): 6 points, the one under the MatrixPortal is skipped
PANEL_HOLES = [(-120, 56), (-120, -56), (0, 56), (0, -56), (120, 56)]
# Long M3 screws go from the outside of the cover, down the standoff tubes, into the panel's inserts,
# so the heads sit in a shallow recess any small screwdriver can reach.
PANEL_SCREW_L = 40.0              # M3x40 pan or socket head
PANEL_SCREW_ENGAGE = 5.0          # how far the screw goes into the panel past its back face

# ---------------- derived ----------------
IN_L, IN_H = DIFF_L + 2*DIFF_CLR, DIFF_H + 2*DIFF_CLR        # inner pocket 259.5 x 129.9
OUT_L, OUT_H = IN_L + 2*WALL, IN_H + 2*WALL                  # 265.5 x 135.9
Z_DIFF = LIP_T                     # diffuser front
Z_PANEL = Z_DIFF + DIFF_T          # panel LED face
Z_PB = Z_PANEL + PANEL_T           # panel back
Z_BOARD = Z_PB + STACK_GAP         # MatrixPortal underside
Z_BTOP = Z_BOARD + PCB_T
Z_CI = Z_BTOP + max(COMP_H + TOP_CLR, BSTANDOFF_H + LUG_T + POST_H_MIN)   # cover inner face
Z_END = Z_CI + COVER_T             # back face
SOCK_Z_C = (Z_PB + Z_CI) / 2       # socket axis height

# MatrixPortal placement: HUB75 edge 1 mm from the +X end wall, USB/button edge facing inward
# (the extension cable's plug needs ~35 mm in front of the board's USB-C port).
BX_USB = IN_L/2 - BOARD_INSET - PCB_L   # X of USB/button edge
BY_V0 = -IN_H/2 + (3.5 if FDM else 2.5) + PCB_W      # Y of the fab print's bottom edge (board is rotated 180deg);
                                   # 2.5 mm off the bottom wall leaves room for a snap hook
def bxy(u, v):                     # fab-print coords (u from USB edge, v from bottom) -> X,Y
    return BX_USB + u, BY_V0 - v
POSTS = [bxy(PCB_L - POST_FROM_HUB_EDGE, PCB_W/2 - POST_SPACING/2),
         bxy(PCB_L - POST_FROM_HUB_EDGE, PCB_W/2 + POST_SPACING/2)]
USB_Y = BY_V0 - 8.4
BTN_Y = [BY_V0 - v for v in (18.6, 28.5, 38.4)]
BY_LO, BY_HI = BY_V0 - PCB_W, BY_V0   # board Y extent

# Adafruit 4218 panel-mount USB-C socket (round, threaded, plastic nut) in the +X end wall
SOCK_HOLE_D = 22.0                 # Adafruit: min 21.5, max 27
SOCK_Y = 0.0
SOCK_NUT_D, SOCK_NUT_T = 27.5, 4.3
SOCK_FLANGE_D = 29.5
SOCK_BODY_D, SOCK_BODY_L = 20.0, 27.0

# zip-tie anchors on the cover for the extension cable's slack
TIE_ANCHORS = [(30.0, 0.0), (-30.0, 0.0)]

# cover-to-shell screws. The diffuser and panel go in from the back and fill the whole pocket, so
# nothing may stick out of the shell's walls into it. Instead, blocks on the cover hang down inside
# the walls, and M3x10 countersunk screws go in from outside, through the wall, into the blocks.
BOSS_R = 4.0                       # half-width of a block along its wall
BOSSES = [(-30, IN_H/2 - BOSS_R), (30, IN_H/2 - BOSS_R), (-30, -IN_H/2 + BOSS_R), (30, -IN_H/2 + BOSS_R),
          (-IN_L/2 + BOSS_R, 35), (-IN_L/2 + BOSS_R, -35), (IN_L/2 - BOSS_R, 35)]
BOSS_DEPTH = 12.0                  # block height, forward from the cover's inner face
BLOCK_GAP = COVER_CLR              # block outer face -> wall inner face
BLOCK_T = 2*BOSS_R - BLOCK_GAP     # block thickness, wall -> inward
Z_SCREW = Z_CI - BOSS_DEPTH/2      # side-screw axis height
# 90deg countersink, oversized so the head always ends up below the surface, never proud: sized for a
# head up to 6.3 mm across, plus (FDM) holes printing up to 0.2 mm undersize and a sagging top edge.
CSK_HEAD_MAX = 6.3
CSK_R = CSK_HEAD_MAX/2 + (0.65 if FDM else 0.35)   # countersink radius at the wall surface

# ---------------- helpers ----------------
def rrect(l, h, r):
    r = max(min(r, l/2 - 0.01, h/2 - 0.01), 0.01)
    return CrossSection.square([l - 2*r, h - 2*r], center=True).offset(r, JoinType.Round)

def slab(cs, z0, z1):
    return cs.extrude(z1 - z0).translate([0, 0, z0])

def box(x0, x1, y0, y1, z0, z1):
    return Manifold.cube([x1-x0, y1-y0, z1-z0]).translate([x0, y0, z0])

def cyl(x, y, z0, z1, r, r2=None):
    return Manifold.cylinder(z1 - z0, r, r if r2 is None else r2).translate([x, y, z0])

def wall_of(x, y):
    """Which wall a block at (x, y) sits on: (axis, sign). axis 0 = end wall (X), 1 = top/bottom wall (Y)."""
    if abs(abs(y) - (IN_H/2 - BOSS_R)) < 1e-6:
        return 1, math.copysign(1, y)
    return 0, math.copysign(1, x)

def radial(x, y, z, s0, s1, r0, r1=None):
    """Cylinder along the outward normal of the wall the block at (x, y) sits on.
    s is measured outward from the wall's inner face (negative = into the pocket)."""
    axis, sg = wall_of(x, y)
    m = Manifold.cylinder(s1 - s0, r0, r0 if r1 is None else r1)      # along +Z from 0
    if axis == 0:
        m = m.rotate([0, 90*sg, 0])                                    # +Z -> sg*X
        return m.translate([sg*(IN_L/2 + s0), y, z])
    m = m.rotate([-90*sg, 0, 0])                                       # +Z -> sg*Y
    return m.translate([x, sg*(IN_H/2 + s0), z])

def slot_x(x, y, z0, z1, length, width):  # rounded slot along X, through Z
    cs = rrect(length, width, width/2 - 0.01).translate([x, y])
    return slab(cs, z0, z1)

# ---------------- front shell ----------------
def outer_body():
    # rounded front edge: hull of offset slices along a quarter circle
    sl = []
    if FDM:
        c = 1.5
        sl += [slab(rrect(OUT_L - 2*c, OUT_H - 2*c, R_OUT - c), 0, 0.01), slab(rrect(OUT_L, OUT_H, R_OUT), c, c + 0.01)]
    for i in (range(0) if FDM else range(9)):
        a = (math.pi/2) * i/8
        inset = R_FRONT * (1 - math.sin(a))
        z = R_FRONT * (1 - math.cos(a))
        sl.append(slab(rrect(OUT_L - 2*inset, OUT_H - 2*inset, R_OUT - inset), z, z + 0.01))
    sl.append(slab(rrect(OUT_L - 1.0, OUT_H - 1.0, R_OUT - 0.5), Z_END - 0.01, Z_END))  # 0.5 back chamfer
    sl.append(slab(rrect(OUT_L, OUT_H, R_OUT), Z_END - 0.5 - 0.01, Z_END - 0.5))
    return Manifold.batch_hull(sl)

def shell():
    s = outer_body()
    # main pocket behind the lip (diffuser size, square-ish corners) + corner reliefs
    pocket = slab(rrect(IN_L, IN_H, 0.3), LIP_T, Z_END + 1)
    for sx in (-1, 1):
        for sy in (-1, 1):
            pocket += cyl(sx*(IN_L/2 - 0.3), sy*(IN_H/2 - 0.3), LIP_T, Z_END + 1, 0.9)
    s -= pocket
    # rebate for the cover
    s -= slab(rrect(OUT_L - 2*REBATE_W, OUT_H - 2*REBATE_W, R_OUT - REBATE_W), Z_CI, Z_END + 1)
    # display window with front bevel
    wl, wh = PANEL_L - 2*LIP_OVER, PANEL_H - 2*LIP_OVER
    s -= Manifold.batch_hull([slab(rrect(wl + 2*BEVEL, wh + 2*BEVEL, 1.0 + BEVEL), -0.01, 0.0),
                              slab(rrect(wl, wh, 1.0), BEVEL, BEVEL + 0.01)])
    s -= slab(rrect(wl, wh, 1.0), -1, LIP_T + 1)
    # No crush ribs or bosses inside the pocket: the diffuser (which fills the pocket) and the panel
    # must slide in from the back past everything. The cover's screws locate the panel instead.
    # side-screw holes for the cover blocks: M3 clearance through the wall, 90deg countersink outside
    for (x, y) in BOSSES:
        s -= radial(x, y, Z_SCREW, -1, WALL + 1, CLEAR_R)
        s -= radial(x, y, Z_SCREW, WALL - (CSK_R - 1.6), WALL + 0.01, 1.6, CSK_R)
        s -= radial(x, y, Z_SCREW, WALL, WALL + 1, CSK_R)
    # +X end wall: round hole for the panel-mount USB-C socket
    r = SOCK_HOLE_D/2
    cs = CrossSection.circle(r)
    if FDM:   # teardrop pointing up the build direction (+Z), truncated under the socket's flange
        t = r*math.sqrt(2)
        cs = CrossSection.hull(cs + CrossSection([[(0, t), (-r/math.sqrt(2), r/math.sqrt(2)), (r/math.sqrt(2), r/math.sqrt(2))]]))
        cs = cs ^ CrossSection.square([2*r + 2, 2*min(t, SOCK_FLANGE_D/2 - 0.75)], center=True)
    # cross-section is in (Y, Z); extrude along +X through the end wall
    hole = cs.extrude(WALL + 4)                     # (x=Y, y=Z, z=X)
    hole = hole.transform([[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0]])   # -> (X, Y, Z)
    s -= hole.translate([IN_L/2 - 2, SOCK_Y, SOCK_Z_C])
    return s

# ---------------- back cover ----------------
def cover():
    cl, ch = OUT_L - 2*REBATE_W - 2*COVER_CLR, OUT_H - 2*REBATE_W - 2*COVER_CLR
    c = slab(rrect(cl, ch, R_OUT - REBATE_W - COVER_CLR), Z_CI, Z_END)
    # panel standoffs: tubes pressing on the panel back, long M3 screws from outside into the brass inserts
    tip = Z_PB - PRESS
    z_head = Z_PB - PANEL_SCREW_ENGAGE + PANEL_SCREW_L      # underside of the screw head
    assert Z_END - 12 < z_head < Z_END - 1, f"PANEL_SCREW_L doesn't suit this depth: head seat at {z_head:.1f}"
    for (x, y) in PANEL_HOLES:
        c += cyl(x, y, tip, Z_CI + 0.01, 5.0)
    for (x, y) in PANEL_HOLES:
        c -= cyl(x, y, z_head, Z_END + 1, 3.5)              # shallow head recess
        c -= cyl(x, y, tip - 1, Z_END + 1, CLEAR_R)          # M3 clearance
    # MatrixPortal, HUB75 end: pads land on the board's own threaded standoffs (power lug between);
    # metal M3 pan head screw from outside the cover into the standoff, head recessed below the surface.
    ptip = Z_BTOP + BSTANDOFF_H + LUG_T                     # pad length follows the standoff height
    z_seat = Z_END - PWR_HEAD_SINK - PWR_HEAD_H             # underside of the screw head
    reach = PWR_SCREW_L - (z_seat - ptip) - LUG_T           # how far the screw goes into the standoff
    assert 1.5 <= reach <= BSTANDOFF_H - 0.1, f"power screw reach {reach:.1f} mm doesn't suit the standoff"
    for (x, y) in POSTS:
        c += cyl(x, y, ptip, Z_CI + 0.01, PWR_PAD_R)
        c -= cyl(x, y, ptip - 1, Z_END + 1, CLEAR_R)         # M3 clearance
        c -= cyl(x, y, z_seat, Z_END + 1, PWR_HEAD_R + 0.5)  # head recess
    # MatrixPortal, USB end: two snap hooks catch the long edges from underneath
    hx = BX_USB + HOOK_U
    zc = Z_BOARD - HOOK_GAP                                  # catch face (board underside rests here)
    for edge_y, sgn in ((BY_HI, 1), (BY_LO, -1)):            # sgn: which side of the board the beam is on
        y_in = edge_y + sgn*0.3                              # beam inner face, 0.3 off the board edge
        y_out = y_in + sgn*HOOK_T
        c += box(hx - HOOK_W/2, hx + HOOK_W/2, min(y_in, y_out), max(y_in, y_out), zc - 1.6, Z_CI + 0.01)
        if FDM:   # 45deg root fillet on the outside of the beam (bending stress peaks at the root)
            room = (IN_H/2 - abs(y_out)) - 0.3 if abs(y_out) > IN_H/4 else 2.0   # stay clear of the side wall
            y_f = y_out + sgn*max(0.5, min(2.0, room))
            c += Manifold.hull(box(hx - HOOK_W/2, hx + HOOK_W/2, min(y_in, y_f), max(y_in, y_f), Z_CI - 0.01, Z_CI + 0.01)
                               + box(hx - HOOK_W/2, hx + HOOK_W/2, min(y_in, y_out), max(y_in, y_out), Z_CI - 2.0, Z_CI))
        # catch: flat face at zc, 45deg lead-in ramp below it
        tip = edge_y - sgn*HOOK_CATCH
        c += Manifold.hull(box(hx - HOOK_W/2, hx + HOOK_W/2, min(y_in, tip), max(y_in, tip), zc - 0.4, zc)
                           + box(hx - HOOK_W/2, hx + HOOK_W/2, min(y_in, y_in + 0.01*sgn), max(y_in, y_in + 0.01*sgn), zc - 1.6, zc))
    # screw blocks hanging down inside the shell walls, with a pilot for the side screw
    for (x, y) in BOSSES:
        axis, sg = wall_of(x, y)
        a, b = sg*(-BLOCK_GAP - BLOCK_T), sg*(-BLOCK_GAP)              # offsets from the inner wall face
        if axis == 0:
            w = sg*IN_L/2
            c += box(min(w + a, w + b), max(w + a, w + b), y - BOSS_R, y + BOSS_R, Z_CI - BOSS_DEPTH, Z_CI + 0.01)
        else:
            w = sg*IN_H/2
            c += box(x - BOSS_R, x + BOSS_R, min(w + a, w + b), max(w + a, w + b), Z_CI - BOSS_DEPTH, Z_CI + 0.01)
        c -= radial(x, y, Z_SCREW, -BLOCK_GAP - BLOCK_T - 1, 0.5, PILOT_R)
    # zip-tie anchors (bridge with a 4 x 2 mm tunnel along Y)
    for (x, y) in TIE_ANCHORS:
        c += box(x - 5, x + 5, y - 4, y + 4, Z_CI - 4.5, Z_CI + 0.01)
        c -= box(x - 2, x + 2, y - 5, y + 5, Z_CI - 2.5, Z_CI - 0.5)
    # vents: rows of rounded slots, skipping anything near standoffs, bosses, the board and the LED window
    keep = ([(x, y, 8.5) for (x, y) in PANEL_HOLES] + [(x, y, 8.0) for (x, y) in BOSSES]
            + [(x, y, 6) for (x, y) in POSTS] + [(x, y, 7) for (x, y) in TIE_ANCHORS])
    bx0, bx1 = BX_USB - 10, BX_USB + PCB_L + 3
    by0, by1 = BY_LO - 3, BY_HI + 3
    vents = []
    L, W, pitch = 26.0, 3.0, 7.0
    for row in range(-7, 8):
        y = row * pitch
        if abs(y) > ch/2 - 12: continue
        for col in range(-4, 5):
            x = col * (L + 6) + (0 if row % 2 == 0 else (L + 6)/2)
            if abs(x) + L/2 > cl/2 - 12: continue
            if x + L/2 > bx0 and x - L/2 < bx1 and y + W/2 > by0 and y - W/2 < by1: continue
            if any(abs(x - kx) < L/2 + kr and abs(y - ky) < W/2 + kr for kx, ky, kr in keep): continue
            vents.append(slot_x(x, y, Z_CI - 1, Z_END + 1, L, W))
    c -= Manifold.batch_boolean(vents, OpType.Add)
    return c, len(vents)

def save(m, path):
    import trimesh
    mesh = m.to_mesh()
    t = trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts), process=True)
    parts = [b for b in t.split(only_watertight=False) if abs(b.volume) > 1.0]   # drop zero-volume slivers
    t = trimesh.util.concatenate(parts) if len(parts) > 1 else parts[0]
    t.export(path)
    return t

if __name__ == "__main__":
    sh = shell()
    cv, nv = cover()
    sfx = "_FDM" if FDM else ""
    t1 = save(sh, f"{OUT}/FlightPortal_v4_shell{sfx}.stl")
    t2 = save(cv, f"{OUT}/FlightPortal_v4_back_cover{sfx}.stl")
    print("variant:", "FDM" if FDM else "MJF")
    print(f"panel T={PANEL_T}  outer {OUT_L:.1f} x {OUT_H:.1f} x {Z_END:.1f} mm")
    print(f"ribbon stack: socket {BSOCK_H} + header {HDR_EXPOSED:.1f} + IDC {IDC_H} + bend {RIBBON_BEND} = {STACK_GAP:.1f} mm")
    print(f"panel screws: M3x{PANEL_SCREW_L:g}, head recess {Z_END - (Z_PB - PANEL_SCREW_ENGAGE + PANEL_SCREW_L):.1f} mm deep, "
          f"{PANEL_SCREW_ENGAGE:g} mm into the panel")
    print(f"side-screw countersinks: {2*CSK_R:.1f} mm across, {CSK_R - 1.6:.2f} mm deep, "
          f"{WALL - (CSK_R - 1.6):.2f} mm of plain wall under them; a 6.0 mm head sits {CSK_R - 3.0:.2f} mm below the surface")
    print(f"power screws: M3x{PWR_SCREW_L:g} pan head, {Z_END - PWR_HEAD_SINK - PWR_HEAD_H - (Z_BTOP + BSTANDOFF_H + LUG_T):.1f} mm of pad under the head, "
          f"{PWR_SCREW_L - (Z_END - PWR_HEAD_SINK - PWR_HEAD_H - (Z_BTOP + BSTANDOFF_H + LUG_T)) - LUG_T:.1f} mm into the standoff; "
          f"board X {BX_USB:.1f} to {BX_USB + PCB_L:.1f}, {BOARD_INSET:g} mm from the end wall")
    print(f"Z: diffuser {Z_DIFF}-{Z_PANEL}, panel back {Z_PB:.1f}, board {Z_BOARD:.1f}, cover {Z_CI:.1f}-{Z_END:.1f}")
    for n, t in (("shell", t1), ("cover", t2)):
        print(f"{n}: watertight={t.is_watertight} bodies={len(t.split(only_watertight=False))} "
              f"vol={t.volume/1000:.1f} cm3 tris={len(t.faces)} extents={np.round(t.extents,1)}")
    print("vents:", nv, " posts:", [tuple(round(v,1) for v in p) for p in POSTS], " usbY", round(USB_Y,1),
          " socket axis Y/Z", SOCK_Y, round(SOCK_Z_C,2))
