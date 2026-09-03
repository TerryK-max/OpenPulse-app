# Test fixtures

`test_ac_fixture.py` auto-discovers real Assetto Corsa captures here and validates
that the parser + normalizer + Deriver survive them. It **skips** when there are
none, so committing a capture is what turns those checks on.

## Add a capture

Run a session in Assetto Corsa (a lap or two, ideally with some kerb strikes,
a lockup, some wheelspin), then:

```bash
# shared memory (game on this Windows PC)
python -m openpulse.tools.telemetry_capture --source ac-shm  --seconds 40 \
    --raw --out tests/fixtures/ac_shm_<car>_<track>.jsonl

# UDP (game on another PC)
python -m openpulse.tools.telemetry_capture --source ac-udp --host <game-pc-ip> --seconds 40 \
    --out tests/fixtures/ac_udp_<car>_<track>.jsonl
```

Check the sanity report the tool prints:

- `speed_ms`, `rpm`, `gear` ranges look right for the car/track
- `accel_g` signs make sense (braking negative on the longitudinal axis, etc.)
- `grip_scrub` climbs when you were sliding, ~0 on a clean lap
- wheel order is `FL FR RL RR` (front-left inside a left-hander loads up, etc.)

If a unit or axis is off, fix it in `openpulse/telemetry/ac/normalize.py` (search
for `# VERIFY`) and re-capture. The `--raw` dump (`*.raw.bin`) is the raw struct
bytes, for checking a byte offset by hand.

A full capture is ~1 MB/s of JSONL — **do not commit it**. Keep it under
`recordings/` (gitignored), run `openpulse-analyze recordings/<file>` to see the
signal distributions (for tuning `DeriveConfig` / effect thresholds), then trim
a small committable fixture:

```bash
openpulse-trim recordings/ac_shm_full.jsonl tests/fixtures/ac_shm_<car>_<track>.jsonl --every 40
```

Commit the trimmed `.jsonl` + its `.meta.json`. `ac_shm_lap.jsonl` here is one
such capture (a GT lap on a real install).
