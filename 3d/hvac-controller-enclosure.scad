// ============================================================================
// HVAC Controller Enclosure - Wall-Mountable
// ============================================================================
// Parametric enclosure for:
//   - Freenove ESP32-S3 Breakout Board
//   - Kootek 8-Channel Relay Module
//   - TCA9548A I2C Multiplexer Hub
//   - 0.96" SSD1306 OLED Display (front panel)
//   - 12mm Bypass Button (front panel)
//   - 5mm Warning LED (front panel)
//
// Usage:
//   Set `part` to "base", "lid", or "both" to render each piece.
//   Adjust `tolerance` for your printer's fit.
// ============================================================================

// --- Part Selector ---
part = "both";  // "base", "lid", or "both"

// --- Global Settings ---
$fn = 30;
tolerance = 0.3;       // Printer tolerance for fits

// --- Wall & Structure ---
wall = 2.5;            // Wall thickness (mm)
corner_r = 3;          // Corner radius for printability
standoff_h = 6;        // Standoff height above base floor
standoff_base_d = 8;   // Standoff base diameter for stability
standoff_top_d = 6;    // Standoff top (pillar) diameter
screw_hole_d = 3.2;    // M3 through-hole diameter
board_gap = 8;         // Gap between boards for airflow/wiring

// --- Component Dimensions (L x W x H) ---
// Freenove ESP32-S3 Breakout
freenove_l = 87.6;
freenove_w = 83.2;
freenove_h = 22;
freenove_mount_l = 81;   // Hole center-to-center X
freenove_mount_w = 77;   // Hole center-to-center Y
freenove_hole_d = 3.2;

// Kootek 8-Channel Relay Module
relay_l = 139;
relay_w = 56;
relay_h = 19;
relay_mount_l = 132;     // Hole center-to-center X
relay_mount_w = 50;      // Hole center-to-center Y
relay_hole_d = 3.1;

// TCA9548A I2C Hub
i2c_l = 31.5;
i2c_w = 21.4;
i2c_h = 7;
i2c_hole_d = 3.0;       // M3
// Diagonal mounting: 2 holes at opposite corners
i2c_mount_diag_x = 25;  // Approximate diagonal hole spacing X
i2c_mount_diag_y = 15;  // Approximate diagonal hole spacing Y

// SSD1306 OLED Display
oled_l = 27;
oled_w = 27;
oled_h = 3.5;
oled_mount_l = 23.5;    // Hole center-to-center X
oled_mount_w = 23.5;    // Hole center-to-center Y
oled_hole_d = 3.0;      // M3
oled_view_l = 22;       // Visible display area width
oled_view_w = 11;       // Visible display area height

// 12mm Bypass Button
button_bezel_d = 14;
button_depth = 25;
button_cutout_d = 12;

// 5mm Warning LED
led_d = 5;
led_hole_d = 5.5;

// --- Enclosure Interior Layout ---
// Layout strategy (viewed from above, relay terminals face the right/open side):
//
//   +-----------------------------------------------+
//   |  Relay Module (139 x 56)         [terminals]-->  (open side, right)
//   |                                               |
//   |-----------------------------------------------|
//   |  Freenove Breakout (87.6 x 83.2) | I2C Hub   |
//   |                                   | (corner)  |
//   +-----------------------------------------------+
//
// Interior width  = relay_l (longest piece, along X)
// Interior depth  = relay_w + board_gap + freenove_w (along Y)

interior_l = relay_l + 2 * board_gap;
interior_w = relay_w + board_gap + freenove_w + 2 * board_gap;
interior_h_base = max(relay_h, freenove_h, i2c_h) + standoff_h + 5; // Extra clearance above tallest component

// Total exterior
ext_l = interior_l + 2 * wall;
ext_w = interior_w + 2 * wall;
ext_h_base = interior_h_base + wall;  // Base box height (no lid)

// Lid dimensions
lid_h = 8;             // Lid total height (lip + top panel)
lid_lip = 4;           // Lip that inserts into the base
lid_lip_clearance = tolerance;

// --- Lid Screw Tabs ---
lid_tab_screw_d = 3.2;    // M3 screw hole in tabs
lid_tab_w = 12;            // Tab width
lid_tab_h = 10;            // Tab height (extends below lid edge)
num_lid_tabs = 6;          // 2 per long side, 1 per short side

// --- Wall Mount Keyholes ---
keyhole_large_d = 8;      // Large end (screw head)
keyhole_small_d = 4.5;    // Narrow slot (screw shaft)
keyhole_slot_len = 8;     // Slot length
keyhole_depth = 2.5;      // Recess depth into back wall
// 4 keyholes: 2 top, 2 bottom on the back
keyhole_inset_x = 30;     // Distance from side edges
keyhole_inset_y = 20;     // Distance from top/bottom edges

// --- Vent Slots ---
vent_slot_l = 20;         // Length of each vent slot
vent_slot_w = 2;          // Width of each vent slot
vent_spacing = 5;         // Spacing between slots
vent_count_side = 4;      // Number of vent slots per side panel

// --- Cable Entry ---
cable_slot_w = 12;        // Width of cable entry slot
cable_slot_h = 8;         // Height of cable entry slot
cable_slot_count = 3;     // Number of cable entry slots on bottom

// --- Front Panel Layout ---
// OLED, button, and LED positions on the lid (X, Y from lid center)
// OLED centered horizontally, upper area of lid
oled_pos_x = 0;
oled_pos_y = interior_w / 4;

// Button to the left of OLED
button_pos_x = -ext_l / 4;
button_pos_y = interior_w / 4;

// LED to the right of OLED
led_pos_x = ext_l / 4;
led_pos_y = interior_w / 4;


// ============================================================================
// MODULES
// ============================================================================

// --- Rounded Rectangle (2D) ---
module rounded_rect_2d(l, w, r) {
    offset(r = r) offset(delta = -r)
        square([l, w], center = true);
}

// --- Rounded Box (hollow, open top) ---
module rounded_box(l, w, h, wall_t, r) {
    difference() {
        // Outer shell
        linear_extrude(height = h)
            rounded_rect_2d(l, w, r);
        // Inner cavity
        translate([0, 0, wall_t])
            linear_extrude(height = h)
                rounded_rect_2d(l - 2 * wall_t, w - 2 * wall_t, max(r - wall_t, 0.5));
    }
}

// --- Standoff with through-hole ---
module standoff(h, base_d, top_d, hole_d) {
    difference() {
        union() {
            // Wide base for stability
            cylinder(d = base_d, h = h * 0.3);
            // Narrower pillar
            cylinder(d = top_d, h = h);
        }
        // Through-hole
        translate([0, 0, -0.1])
            cylinder(d = hole_d, h = h + 0.2);
    }
}

// --- Keyhole (for wall mounting, cut into back wall) ---
module keyhole(large_d, small_d, slot_len, depth) {
    // Large circle at bottom
    translate([0, 0, -0.1])
        cylinder(d = large_d, h = depth + 0.2);
    // Slot going upward
    translate([0, 0, -0.1])
        hull() {
            cylinder(d = small_d, h = depth + 0.2);
            translate([0, slot_len, 0])
                cylinder(d = small_d, h = depth + 0.2);
        }
}

// --- Vent Slot Array (cut into a side wall) ---
module vent_slots(count, slot_l, slot_w, spacing) {
    total_h = count * slot_w + (count - 1) * spacing;
    for (i = [0 : count - 1]) {
        translate([0, 0, -total_h / 2 + i * (slot_w + spacing)])
            cube([slot_l, wall + 0.2, slot_w], center = true);
    }
}

// --- Board Mounting Holes (4 corners pattern) ---
module board_mount_holes(mount_l, mount_w, hole_d, depth) {
    for (dx = [-1, 1], dy = [-1, 1]) {
        translate([dx * mount_l / 2, dy * mount_w / 2, 0])
            cylinder(d = hole_d, h = depth, center = true);
    }
}

// --- Lid Screw Boss (on base wall, for lid attachment) ---
module lid_screw_boss(d_outer, d_hole, h) {
    difference() {
        cylinder(d = d_outer, h = h);
        translate([0, 0, -0.1])
            cylinder(d = d_hole, h = h + 0.2);
    }
}


// ============================================================================
// BASE ASSEMBLY
// ============================================================================

module base() {

    // --- Board positions (centers, relative to enclosure center) ---
    // Relay module: along top edge of interior (positive Y), centered on X
    relay_cx = 0;
    relay_cy = (interior_w / 2) - board_gap - relay_w / 2;

    // Freenove: below relay, centered-left on X
    freenove_cx = -(interior_l / 2 - board_gap - freenove_l / 2);
    freenove_cy = -(interior_w / 2) + board_gap + freenove_w / 2;

    // I2C hub: in the corner gap to the right of the freenove
    i2c_cx = freenove_cx + freenove_l / 2 + board_gap + i2c_l / 2;
    i2c_cy = freenove_cy - freenove_w / 2 + i2c_w / 2;

    difference() {
        union() {
            // --- Main box (open top) ---
            rounded_box(ext_l, ext_w, ext_h_base, wall, corner_r);

            // --- Relay Module Standoffs ---
            for (dx = [-1, 1], dy = [-1, 1]) {
                translate([
                    relay_cx + dx * relay_mount_l / 2,
                    relay_cy + dy * relay_mount_w / 2,
                    wall
                ])
                    standoff(standoff_h, standoff_base_d, standoff_top_d, relay_hole_d);
            }

            // --- Freenove Breakout Standoffs ---
            for (dx = [-1, 1], dy = [-1, 1]) {
                translate([
                    freenove_cx + dx * freenove_mount_l / 2,
                    freenove_cy + dy * freenove_mount_w / 2,
                    wall
                ])
                    standoff(standoff_h, standoff_base_d, standoff_top_d, freenove_hole_d);
            }

            // --- I2C Hub Standoffs (diagonal: bottom-left, top-right) ---
            for (corner = [[-1, -1], [1, 1]]) {
                translate([
                    i2c_cx + corner[0] * i2c_mount_diag_x / 2,
                    i2c_cy + corner[1] * i2c_mount_diag_y / 2,
                    wall
                ])
                    standoff(standoff_h, standoff_base_d, standoff_top_d, i2c_hole_d);
            }

            // --- Lid Screw Bosses (inside walls, near top) ---
            // Along the long sides (front and back), 2 each
            for (side_y = [-1, 1]) {
                for (pos_x = [-ext_l / 4, ext_l / 4]) {
                    translate([
                        pos_x,
                        side_y * (ext_w / 2 - wall - 4),
                        ext_h_base - 12
                    ])
                        lid_screw_boss(8, lid_tab_screw_d, 12);
                }
            }
            // Along the short sides, 1 each
            for (side_x = [-1, 1]) {
                translate([
                    side_x * (ext_l / 2 - wall - 4),
                    0,
                    ext_h_base - 12
                ])
                    lid_screw_boss(8, lid_tab_screw_d, 12);
            }
        }

        // --- Keyholes on the back (bottom of enclosure exterior) ---
        // The "back" is the bottom face (Z=0) since it mounts to a wall
        for (dx = [-1, 1], dy = [-1, 1]) {
            translate([
                dx * (ext_l / 2 - keyhole_inset_x),
                dy * (ext_w / 2 - keyhole_inset_y),
                0
            ])
            rotate([0, 0, 0])
                keyhole(keyhole_large_d, keyhole_small_d, keyhole_slot_len, keyhole_depth);
        }

        // --- Side Ventilation Slots ---
        // Left side (X = -ext_l/2)
        vent_z_center = ext_h_base * 0.6;
        translate([-ext_l / 2, 0, vent_z_center])
            rotate([0, 90, 0])
                vent_slots(vent_count_side, vent_slot_l, vent_slot_w, vent_spacing);

        // Back side (Y = -ext_w/2), also add vents
        translate([0, -ext_w / 2, vent_z_center])
            rotate([90, 0, 0])
                vent_slots(vent_count_side, vent_slot_l, vent_slot_w, vent_spacing);

        // --- Open side for relay screw terminals (right side, X = +ext_l/2) ---
        // Large cutout on the right wall for wire access to relay terminals
        relay_opening_w = relay_w + 20;  // Wider than relay for access
        relay_opening_h = ext_h_base - wall - 5;  // Nearly full height
        translate([ext_l / 2 - wall / 2, relay_cy, wall + relay_opening_h / 2 + 2])
            cube([wall + 0.2, relay_opening_w, relay_opening_h], center = true);

        // --- Cable Entry Slots (bottom edge of front side, Y = +ext_w/2) ---
        cable_total_span = cable_slot_count * cable_slot_w + (cable_slot_count - 1) * 10;
        for (i = [0 : cable_slot_count - 1]) {
            cx = -cable_total_span / 2 + i * (cable_slot_w + 10) + cable_slot_w / 2;
            translate([cx, ext_w / 2 - wall / 2, wall + cable_slot_h / 2])
                cube([cable_slot_w, wall + 0.2, cable_slot_h], center = true);
        }

        // --- Additional cable entry on bottom side (Y = -ext_w/2) ---
        for (i = [0 : 1]) {
            cx = -15 + i * 30;
            translate([cx, -ext_w / 2 + wall / 2, wall + cable_slot_h / 2])
                cube([cable_slot_w, wall + 0.2, cable_slot_h], center = true);
        }
    }

    // --- Debug: Board outlines (comment out for final render) ---
    // Uncomment these to visualize board placement:
    // %translate([relay_cx, relay_cy, wall + standoff_h])
    //     cube([relay_l, relay_w, relay_h], center = true);
    // %translate([freenove_cx, freenove_cy, wall + standoff_h])
    //     cube([freenove_l, freenove_w, freenove_h], center = true);
    // %translate([i2c_cx, i2c_cy, wall + standoff_h])
    //     cube([i2c_l, i2c_w, i2c_h], center = true);
}


// ============================================================================
// LID ASSEMBLY
// ============================================================================

module lid() {
    lid_ext_l = ext_l - 2 * lid_lip_clearance;
    lid_ext_w = ext_w - 2 * lid_lip_clearance;
    lip_l = ext_l - 2 * wall - 2 * lid_lip_clearance;
    lip_w = ext_w - 2 * wall - 2 * lid_lip_clearance;

    difference() {
        union() {
            // --- Top panel ---
            linear_extrude(height = wall)
                rounded_rect_2d(lid_ext_l, lid_ext_w, corner_r);

            // --- Inner lip (inserts into base) ---
            translate([0, 0, -lid_lip])
                linear_extrude(height = lid_lip)
                    difference() {
                        rounded_rect_2d(lip_l, lip_w, max(corner_r - wall, 0.5));
                        rounded_rect_2d(lip_l - 2 * wall, lip_w - 2 * wall, max(corner_r - 2 * wall, 0.5));
                    }
        }

        // --- OLED Display Viewing Window ---
        translate([oled_pos_x, oled_pos_y, -0.1])
            cube([oled_view_l, oled_view_w, wall + 0.2], center = true);

        // --- OLED Mounting Holes (through lid for M3 screws) ---
        for (dx = [-1, 1], dy = [-1, 1]) {
            translate([
                oled_pos_x + dx * oled_mount_l / 2,
                oled_pos_y + dy * oled_mount_w / 2,
                -0.1
            ])
                cylinder(d = oled_hole_d + tolerance, h = wall + 0.2);
        }

        // --- Bypass Button Hole (12mm cutout) ---
        translate([button_pos_x, button_pos_y, -0.1])
            cylinder(d = button_cutout_d, h = wall + 0.2);

        // --- Warning LED Hole (5.5mm) ---
        translate([led_pos_x, led_pos_y, -0.1])
            cylinder(d = led_hole_d, h = wall + 0.2);

        // --- Lid Screw Holes ---
        // Must match the boss positions on the base
        // Long sides
        for (side_y = [-1, 1]) {
            for (pos_x = [-ext_l / 4, ext_l / 4]) {
                translate([
                    pos_x,
                    side_y * (ext_w / 2 - wall - 4),
                    -lid_lip - 0.1
                ])
                    cylinder(d = lid_tab_screw_d, h = lid_lip + wall + 0.2);
            }
        }
        // Short sides
        for (side_x = [-1, 1]) {
            translate([
                side_x * (ext_l / 2 - wall - 4),
                0,
                -lid_lip - 0.1
            ])
                cylinder(d = lid_tab_screw_d, h = lid_lip + wall + 0.2);
        }

        // --- Ventilation slots on lid ---
        for (i = [0 : 2]) {
            translate([
                -ext_l / 2 + 25 + i * 30,
                -ext_w / 4,
                -0.1
            ])
                cube([vent_slot_l, vent_slot_w, wall + 0.2], center = true);
        }
    }

    // --- OLED Standoffs (hang down from lid interior) ---
    for (dx = [-1, 1], dy = [-1, 1]) {
        translate([
            oled_pos_x + dx * oled_mount_l / 2,
            oled_pos_y + dy * oled_mount_w / 2,
            -lid_lip - 3  // Extend below lip
        ])
            difference() {
                cylinder(d = 6, h = 3 + lid_lip);
                translate([0, 0, -0.1])
                    cylinder(d = oled_hole_d + tolerance, h = 3 + lid_lip + 0.2);
            }
    }
}


// ============================================================================
// RENDER SELECTOR
// ============================================================================

if (part == "base") {
    base();
}
else if (part == "lid") {
    // Render lid right-side up (top face up) for printing
    translate([0, 0, wall])
        rotate([180, 0, 0])
            lid();
}
else if (part == "both") {
    // Show assembled view: base + lid on top
    base();
    translate([0, 0, ext_h_base])
        lid();
}
else {
    echo("ERROR: Set 'part' to \"base\", \"lid\", or \"both\"");
}

// ============================================================================
// DIMENSION SUMMARY (echoed on render)
// ============================================================================
echo(str("Enclosure exterior: ", ext_l, " x ", ext_w, " x ", ext_h_base + lid_h, " mm"));
echo(str("Enclosure interior: ", interior_l, " x ", interior_w, " x ", interior_h_base, " mm"));
echo(str("Base height: ", ext_h_base, " mm"));
echo(str("Lid height: ", lid_h, " mm (lip: ", lid_lip, " mm)"));
