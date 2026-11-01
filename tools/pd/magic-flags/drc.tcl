# tools/pd/magic-flags/drc.tcl: Magic DRC on one clipped window, same style as the flow
# (drc(full), euclidean). The clip is small, so checking all of it is quick.
crashbackups disable
locking disable
units internal
drc off
gds readonly true
gds maskhints true
gds read $::env(GDS)
load clip
select top cell
drc euclidean on
drc style drc(full)
drc on
drc check
drc catchup
set osc [cif scale out]
set fout [open $::env(MOUT) w]
foreach {why coords} [drc listall why] {
    foreach c $coords {
        puts $fout [format "%s|%.3f %.3f %.3f %.3f" $why \
            [expr {$osc * [lindex $c 0]}] [expr {$osc * [lindex $c 1]}] \
            [expr {$osc * [lindex $c 2]}] [expr {$osc * [lindex $c 3]}]]
    }
}
close $fout
exit 0
