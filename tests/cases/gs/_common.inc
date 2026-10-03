# shared setup for gs.sh (boot orchestrator): runs as a separate process, loads gs.conf itself
script_under_test=gs/gs.sh
invocation=standalone_wait
dump_baseline=0
sort_shim_log=1   # gs.sh starts several jobs with "&": compare the SET of calls, not their order
dump_files=(config/gpio.txt)
dump_trees=(home)
gs_sb() {
	mkdir -p "$ROOT/proc" "$ROOT/run" "$ROOT/tmp" "$ROOT/config" "$ROOT/gs"
	: > "$ROOT/proc/mounts"                    # record partition NOT mounted unless a case says so
	conf_set rec_dir "$ROOT/Videos"; mkdir -p "$ROOT/Videos"
}
