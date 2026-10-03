# shared setup for stream.sh (video player launcher). STARTUP ONLY: the script ends in a FIFO loop
# (record button); the case pre-creates $ROOT/run/record_button.fifo as a REGULAR file, so "mkfifo" fails
# under "set -e" and the script exits (status 1) right after startup. Record start/stop in the loop is NOT covered.
script_under_test=gs/stream.sh
invocation=standalone_wait
dump_baseline=0
sort_shim_log=1   # player, OSD subshell and wallpaper subshell run as background jobs
rewrite_extra=(/dev/shm)
stream_sb() {
	mkdir -p "$ROOT/run" "$ROOT/tmp" "$ROOT/Videos" "$ROOT/sys/class/drm/card0-HDMI-A-1" "$ROOT/dev/shm"
	echo connected > "$ROOT/sys/class/drm/card0-HDMI-A-1/status"
	: > "$ROOT/run/record_button.fifo"
	conf_set rec_dir "$ROOT/Videos"
}
