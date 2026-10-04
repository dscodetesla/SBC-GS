"""Pure-logic model of the ArduPilot (Copter) behaviours that matter to the GS RC/MAVLink chain.

No I/O, no pymavlink, injected clock: every test is deterministic and takes microseconds.
Each rule carries its evidence tag (docs/MAVLINK-ROUTER.md vocabulary):
  SRC   read from ArduPilot docs/source (see docs/MAVLINK-ROUTER.md rows 93-103, docs/CHAINS.md)
  SITL  observed on the prebuilt ArduCopter 4.7.1 SITL with tests/sim/sitl_probe.py (physics sim, no HW)
  INF   inference, not checked against the firmware
This is a MODEL: it is not ArduPilot. SITL numbers differ slightly (override expiry measured 2.8 s for a 3 s setting).
"""

RC_RELEASE = 0        # SRC docs/CHAINS.md: 0 releases the channel
RC_IGNORE = 65535     # SRC: 65535 = leave the channel alone


class ApmModel:
    def __init__(self, *, rc_override_time=3.0, fs_gcs_enable=0, fs_gcs_timeout=5.0, mav_gcs_sysid=255,
                 mav_gcs_sysid_hi=0, rc_options_ignore_override=False, armed=True, receiver_present=False,
                 rc_fs_timeout=1.0, fs_thr_enable=1, rc_input=None):
        self.rc_override_time = rc_override_time        # SRC default 3.0; 0 disables overrides; <0 never expires
        self.fs_gcs_enable = fs_gcs_enable              # SITL 4.7.1 default 0 (= no GCS failsafe at all)
        self.fs_gcs_timeout = fs_gcs_timeout            # SITL default 5
        self.mav_gcs_sysid = mav_gcs_sysid              # SITL default 255 (older firmware: SYSID_MYGCS, UNVERIFIED)
        self.mav_gcs_sysid_hi = mav_gcs_sysid_hi
        self.ignore_override = rc_options_ignore_override   # SRC RC_OPTIONS bit 1
        self.armed = armed
        self.receiver_present = receiver_present        # a real ELRS/SBUS receiver feeding the FC
        self.rc_fs_timeout = rc_fs_timeout              # SRC default 1 s
        self.fs_thr_enable = fs_thr_enable
        self.rc_input = list(rc_input or [1500, 1500, 1000, 1500, 1500, 1500, 1500, 1500])  # "regular RC" (stale values, #32862)
        self.override = [None] * 8                      # (value, t) per channel
        self.last_gcs = None                            # last heartbeat/manual_control from a GCS sysid
        self.gcs_failsafe = False
        self.rc_failsafe = False
        self.last_override_any = None
        self.mode = "STABILIZE"
        self.events = []                                # (t, text) like STATUSTEXT

    def _log(self, t, text):
        self.events.append((t, text))

    def sysid_is_gcs(self, sysid):
        # SRC GCS.cpp sysid_is_gcs: HI >= SYSID means the whole range counts
        if self.mav_gcs_sysid_hi >= self.mav_gcs_sysid:
            return self.mav_gcs_sysid <= sysid <= self.mav_gcs_sysid_hi
        return sysid == self.mav_gcs_sysid

    # ---- inputs ----
    def on_heartbeat(self, sysid, t):
        if self.sysid_is_gcs(sysid):            # SITL: sysid 125 heartbeats do NOT count, 255 does
            self._seen(t)

    def on_manual_control(self, sysid, t):
        if self.sysid_is_gcs(sysid):            # SITL: MANUAL_CONTROL alone keeps the GCS failsafe away
            self._seen(t)

    def _seen(self, t):
        self.last_gcs = t
        if self.gcs_failsafe:
            self.gcs_failsafe = False           # SITL: "GCS Failsafe Cleared"; the flight-mode change is NOT undone (SRC gcs-failsafe)
            self._log(t, "GCS Failsafe Cleared")

    def on_rc_override(self, sysid, chans, t):
        """chans: 8 raw values. Returns True if accepted."""
        if not self.sysid_is_gcs(sysid):        # SITL: sysid 77 overrides ignored; SRC GCS_Common.cpp
            self._log(t, f"override from sysid {sysid} ignored (not a GCS)")
            return False
        if self.ignore_override or self.rc_override_time == 0:
            return False
        for i, v in enumerate(chans[:8]):
            if v == RC_IGNORE:
                continue
            self.override[i] = None if v == RC_RELEASE else (v, t)
        self.last_override_any = t
        return True

    # ---- outputs ----
    def _active(self, i, t):
        o = self.override[i]
        if o is None:
            return None
        if self.rc_override_time < 0 or t - o[1] <= self.rc_override_time:
            return o[0]
        return None

    def channel(self, i, t):
        """Value the flight code sees on channel i (0-based). Falls back to stale regular RC after expiry (INF/#32862)."""
        v = self._active(i, t)
        return v if v is not None else self.rc_input[i]

    def overridden(self, t):
        return any(self._active(i, t) is not None for i in range(8))

    # ---- time ----
    def tick(self, t):
        # GCS failsafe: needs FS_GCS_ENABLE and a GCS seen at least once (SRC gcs-failsafe.html). SITL: the flag and the
        # "GCS Failsafe" text appear also while disarmed; the flight-mode ACTION is taken only when armed (INF for the mode map).
        if (self.fs_gcs_enable and self.last_gcs is not None and not self.gcs_failsafe
                and t - self.last_gcs > self.fs_gcs_timeout):
            self.gcs_failsafe = True
            if self.armed:
                self.mode = {1: "RTL", 2: "RTL", 3: "RTL", 4: "LAND", 5: "LAND"}.get(int(self.fs_gcs_enable), "RTL")
            self._log(t, "GCS Failsafe")
        # radio failsafe: with no physical receiver, the loss of overrides is a radio loss (SRC radio-failsafe: "RC_OVERRIDES are lost if using a GCS only")
        if (self.armed and not self.receiver_present and self.last_override_any is not None and not self.rc_failsafe
                and not self.overridden(t) and self.rc_override_time > 0
                and t - self.last_override_any > self.rc_override_time + self.rc_fs_timeout):
            self.rc_failsafe = True
            if self.fs_thr_enable:
                self.mode = "RTL"
            self._log(t, "Radio Failsafe")
        if self.rc_failsafe and self.overridden(t):
            self.rc_failsafe = False
            self._log(t, "Radio Failsafe Cleared")
