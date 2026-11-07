# SPDX-License-Identifier: Apache-2.0
# src/pdn_cfg.tcl: LibreLane's default power grid, plus Metal4 stripes that
# reach into the SRAM macros and land on their Metal4 supply columns.
#
# The tile grid is vertical Metal4 stripes only, and the macro supplies are
# vertical Metal4 columns, so a stripe feeds a macro only where it overlaps a
# column of its own net. pdngen cuts its stripes short of every block macro, so
# the wrapper below makes the macro masters look like core cells while the grid
# is built (pdngen then draws the stripes across them) and restores them.
#
# A stripe may not cross the macro's Metal4 obstructions: they hide macro wiring
# of other nets. Some supply columns are split in two pins (VDD! at one end,
# VDDARRAY! at the other) with an obstruction band between them, and some
# stripes fall between columns. So after pdngen every stripe is cut out of each
# obstruction it crosses (keeping the Metal4 spacing), and pieces left wholly
# inside a macro, which would float, are deleted. A split column is then fed
# from both of its ends, which is why every macro needs core rows (and thus
# grid) on both sides. The check fails the flow unless no stripe overlaps an
# obstruction or a pin of another net, and every macro supply pin touches a
# stripe piece that leaves the macro.

proc tse_macros {} {
    set out {}
    foreach inst [[ord::get_db_block] getInsts] {
        if { [[$inst getMaster] isBlock] } { lappend out $inst }
    }
    return $out
}

# Hide the macros before the default grid definitions run: the default macro
# grid would otherwise claim them and fail for having no shapes of its own.
set ::tse_masters {}
foreach inst [tse_macros] {
    set m [$inst getMaster]
    if { [lsearch $::tse_masters $m] < 0 } { lappend ::tse_masters $m }
}
foreach m $::tse_masters { $m setType CORE }

source $::env(SCRIPTS_DIR)/openroad/common/pdn_cfg.tcl

rename pdngen tse_pdngen_stock

# A master rectangle in die coordinates for one placed instance.
proc tse_to_die {inst x0 y0 x1 y1} {
    set bb [$inst getBBox]
    set ox [$bb xMin]; set oy [$bb yMin]
    set w [[$inst getMaster] getWidth]; set h [[$inst getMaster] getHeight]
    switch [$inst getOrient] {
        R0   { return [list [expr {$ox + $x0}] [expr {$oy + $y0}] [expr {$ox + $x1}] [expr {$oy + $y1}]] }
        MX   { return [list [expr {$ox + $x0}] [expr {$oy + $h - $y1}] [expr {$ox + $x1}] [expr {$oy + $h - $y0}]] }
        MY   { return [list [expr {$ox + $w - $x1}] [expr {$oy + $y0}] [expr {$ox + $w - $x0}] [expr {$oy + $y1}]] }
        R180 { return [list [expr {$ox + $w - $x1}] [expr {$oy + $h - $y1}] [expr {$ox + $w - $x0}] [expr {$oy + $h - $y0}]] }
        default { error "TSEPDN: orientation [$inst getOrient] of [$inst getName] not handled" }
    }
}

# Supply pin shapes of one macro on a layer: {net pin {x0 y0 x1 y1}}.
proc tse_macro_columns {inst layer} {
    set out {}
    foreach iterm [$inst getITerms] {
        set mterm [$iterm getMTerm]
        if { [lsearch {POWER GROUND} [$mterm getSigType]] < 0 } { continue }
        set net [$iterm getNet]
        if { $net == "NULL" } {
            error "TSEPDN: [$inst getName] pin [$mterm getName] is not connected"
        }
        foreach mpin [$mterm getMPins] {
            foreach box [$mpin getGeometry] {
                if { [$box isVia] || [[$box getTechLayer] getName] != $layer } { continue }
                lappend out [list [$net getName] [$mterm getName] \
                    [tse_to_die $inst [$box xMin] [$box yMin] [$box xMax] [$box yMax]]]
            }
        }
    }
    return $out
}

# Obstruction rectangles of one macro on a layer, in die coordinates.
proc tse_macro_obs {inst layer} {
    set out {}
    foreach box [[$inst getMaster] getObstructions] {
        if { [$box isVia] || [[$box getTechLayer] getName] != $layer } { continue }
        lappend out [tse_to_die $inst [$box xMin] [$box yMin] [$box xMax] [$box yMax]]
    }
    return $out
}

proc tse_overlap {a b} {
    lassign $a ax0 ay0 ax1 ay1
    lassign $b bx0 by0 bx1 by1
    return [expr {$ax0 < $bx1 && $bx0 < $ax1 && $ay0 < $by1 && $by0 < $ay1}]
}

# Length of a vertical stripe piece that lies outside every macro it crosses.
proc tse_outside {r macros} {
    lassign $r x0 y0 x1 y1
    set out [expr {$y1 - $y0}]
    foreach m $macros {
        lassign [lindex $m 0] mx0 my0 mx1 my1
        if { $x1 <= $mx0 || $x0 >= $mx1 } { continue }
        set ov [expr {min($y1, $my1) - max($y0, $my0)}]
        if { $ov > 0 } { set out [expr {$out - $ov}] }
    }
    return $out
}

# A piece is tied to the grid only if it crosses rails of both supplies outside
# the macros: two cell rows.
proc tse_min_outside {} {
    set row [lindex [[ord::get_db_block] getRows] 0]
    return [expr {2 * [[$row getSite] getHeight]}]
}

proc tse_macro_boxes {} {
    set out {}
    foreach inst [tse_macros] {
        set bb [$inst getBBox]
        lappend out [list [list [$bb xMin] [$bb yMin] [$bb xMax] [$bb yMax]]]
    }
    return $out
}

# The pieces left of a vertical shape once it is cut out of the macro
# obstructions (keeping the layer spacing), without the pieces that would
# float inside a macro; "same" if the shape crosses no obstruction.
proc tse_pieces {r macros gap} {
    lassign $r x0 y0 x1 y1
    if { $y1 - $y0 <= $x1 - $x0 } { return same }
    set holes {}
    foreach m $macros {
        lassign $m mb obs
        if { ![tse_overlap $r $mb] } { continue }
        foreach o $obs {
            if { [tse_overlap $r $o] } {
                lappend holes [list [expr {[lindex $o 1] - $gap}] [expr {[lindex $o 3] + $gap}]]
            }
        }
    }
    if { [llength $holes] == 0 } { return same }
    set pieces {}
    set lo $y0
    foreach h [lsort -integer -index 0 $holes] {
        lassign $h h0 h1
        if { $h0 > $lo } { lappend pieces [list $lo [expr {min($h0, $y1)}]] }
        if { $h1 > $lo } { set lo $h1 }
    }
    if { $lo < $y1 } { lappend pieces [list $lo $y1] }
    set out {}
    foreach p $pieces {
        lassign $p p0 p1
        if { [tse_outside [list $x0 $p0 $x1 $p1] $macros] >= [tse_min_outside] } {
            lappend out [list $x0 $p0 $x1 $p1]
        }
    }
    return $out
}

# Cut every vertical stripe on the layer out of the macro obstructions, and drop
# the top-level supply pin shapes pdngen made on the stripes that cross a macro.
# Tiny Tapeout's pin check wants every supply pin shape to reach both the top and
# the bottom edge, so a cut pin fails it; the full-height stripes beside the
# macros keep their pins, and the cut stripe pieces stay on the grid unpinned.
proc tse_cut_stripes {layer} {
    set tlayer [[[ord::get_db] getTech] findLayer $layer]
    set gap [$tlayer getSpacing]
    set macros {}
    foreach inst [tse_macros] {
        set bb [$inst getBBox]
        lappend macros [list [list [$bb xMin] [$bb yMin] [$bb xMax] [$bb yMax]] [tse_macro_obs $inst $layer]]
    }
    set cut 0; set pins 0
    foreach net [[ord::get_db_block] getNets] {
        if { ![$net isSpecial] } { continue }
        foreach sw [$net getSWires] {
            foreach w [$sw getWires] {
                if { [$w isVia] || [[$w getTechLayer] getName] != $layer } { continue }
                set p [tse_pieces [list [$w xMin] [$w yMin] [$w xMax] [$w yMax]] $macros $gap]
                if { $p == "same" } { continue }
                set type [$w getWireShapeType]
                odb::dbSBox_destroy $w
                incr cut
                foreach r $p { odb::dbSBox_create $sw $tlayer {*}$r $type }
            }
        }
        foreach bterm [$net getBTerms] {
            foreach bpin [$bterm getBPins] {
                set boxes {}
                set changed 0
                foreach b [$bpin getBoxes] {
                    set r [list [$b xMin] [$b yMin] [$b xMax] [$b yMax]]
                    set p same
                    if { [[$b getTechLayer] getName] == $layer } { set p [tse_pieces $r $macros $gap] }
                    if { $p == "same" } {
                        lappend boxes [list [$b getTechLayer] $r]
                    } else {
                        set changed 1
                    }
                }
                if { !$changed } { continue }
                set status [$bpin getPlacementStatus]
                odb::dbBPin_destroy $bpin
                incr pins
                if { [llength $boxes] == 0 } { continue }
                set nb [odb::dbBPin_create $bterm]
                foreach b $boxes { odb::dbBox_create $nb [lindex $b 0] {*}[lindex $b 1] }
                $nb setPlacementStatus $status
            }
        }
    }
    puts "TSEPDN: cut $cut stripes and dropped $pins supply pins that crossed macro obstructions"
}

proc tse_check_macro_stripes {layer} {
    set errors 0
    set stripes {}
    foreach net [[ord::get_db_block] getNets] {
        if { ![$net isSpecial] } { continue }
        foreach sw [$net getSWires] {
            foreach w [$sw getWires] {
                if { [$w isVia] || [[$w getTechLayer] getName] != $layer } { continue }
                lappend stripes [list [$net getName] [list [$w xMin] [$w yMin] [$w xMax] [$w yMax]]]
            }
        }
        foreach bterm [$net getBTerms] {
            foreach bpin [$bterm getBPins] {
                foreach b [$bpin getBoxes] {
                    if { [[$b getTechLayer] getName] != $layer } { continue }
                    lappend stripes [list [$net getName] [list [$b xMin] [$b yMin] [$b xMax] [$b yMax]]]
                }
            }
        }
    }
    foreach inst [tse_macros] {
        set bb [$inst getBBox]
        set mb [list [$bb xMin] [$bb yMin] [$bb xMax] [$bb yMax]]
        lassign $mb mx0 my0 mx1 my1
        set cols [tse_macro_columns $inst $layer]
        set obs [tse_macro_obs $inst $layer]
        set fed {}
        foreach c $cols { dict set fed [lindex $c 1] 0 }
        set n 0
        foreach s $stripes {
            lassign $s net r
            if { ![tse_overlap $r $mb] } { continue }
            incr n
            lassign $r x0 y0 x1 y1
            if { [tse_outside $r [tse_macro_boxes]] < [tse_min_outside] } {
                puts "TSEPDN: FAIL $net piece at x [expr {$x0 / 1000.0}] y [expr {$y0 / 1000.0}] does not leave [$inst getName]"
                incr errors
            }
            foreach o $obs {
                if { [tse_overlap $r $o] } {
                    puts "TSEPDN: FAIL $net stripe at x [expr {$x0 / 1000.0}] crosses an obstruction of [$inst getName]"
                    incr errors
                }
            }
            foreach c $cols {
                lassign $c cnet pin cr
                if { ![tse_overlap $r $cr] } { continue }
                if { $cnet != $net } {
                    puts "TSEPDN: FAIL $net stripe at x [expr {$x0 / 1000.0}] overlaps $pin ($cnet)"
                    incr errors
                } else {
                    dict set fed $pin 1
                }
            }
        }
        dict for {pin ok} $fed {
            if { !$ok } {
                puts "TSEPDN: FAIL [$inst getName] supply $pin touches no stripe"
                incr errors
            }
        }
        puts "TSEPDN: [$inst getName] [$inst getOrient] bbox ($mx0 $my0 $mx1 $my1) dbu, $n stripe pieces over it, fed pins: [dict keys [dict filter $fed value 1]]"
    }
    if { $errors } {
        error "TSEPDN: $errors power grid problems over the macros"
    }
}

# Tiny Tapeout's pin check: every supply pin shape reaches within 10 um of the
# die's top and bottom edges, and each supply keeps at least one pin.
proc tse_check_supply_pins {} {
    set block [ord::get_db_block]
    set die [$block getDieArea]
    set dbu [[[ord::get_db] getTech] getDbUnitsPerMicron]
    set reach [expr {10 * $dbu}]
    set errors 0
    foreach net [$block getNets] {
        if { ![$net isSpecial] } { continue }
        set n 0
        foreach bterm [$net getBTerms] {
            foreach bpin [$bterm getBPins] {
                foreach b [$bpin getBoxes] {
                    incr n
                    if { [$b yMin] - [$die yMin] > $reach || [$die yMax] - [$b yMax] > $reach } {
                        puts "TSEPDN: FAIL [$net getName] pin at x [expr {[$b xMin] / double($dbu)}] spans y [expr {[$b yMin] / double($dbu)}] to [expr {[$b yMax] / double($dbu)}], not die height"
                        incr errors
                    }
                }
            }
        }
        if { $n == 0 } {
            puts "TSEPDN: FAIL [$net getName] has no pin shape left"
            incr errors
        }
        puts "TSEPDN: [$net getName] $n supply pin shapes"
    }
    if { $errors } {
        error "TSEPDN: $errors supply pin problems"
    }
}

proc pdngen {args} {
    set rc [catch { tse_pdngen_stock {*}$args } err]
    foreach m $::tse_masters { $m setType BLOCK }
    if { $rc } { error $err }
    tse_cut_stripes $::env(PDN_VERTICAL_LAYER)
    tse_check_macro_stripes $::env(PDN_VERTICAL_LAYER)
    tse_check_supply_pins
}

# --- TSE: empty the bottom-right pure-fill corner (Magic pSD.e/f at the die edge) ---
# A hard placement blockage keeps standard-cell fillers out of x[1189.0,1289.28] um,
# y[0,40.0] um. Runs during PDN generation, before global placement and fill, so both
# skip the corner. Routing is unaffected (this is a placement blockage, not an obstruction).
proc tse_corner_blockage {} {
    set block [ord::get_db_block]
    set dbu [[[ord::get_db] getTech] getDbUnitsPerMicron]
    set x0 [expr {int(1189.0 * $dbu)}]
    set y0 0
    set x1 [expr {int(1289.28 * $dbu)}]
    set y1 [expr {int(40.0 * $dbu)}]
    odb::dbBlockage_create $block $x0 $y0 $x1 $y1
    puts "TSEPDN: corner placement blockage $x0 $y0 $x1 $y1"
}
tse_corner_blockage
