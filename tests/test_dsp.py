"""Engine DSP primitives."""

import numpy as np

from openpulse.engine.dsp import (
    BandNoise,
    Gate,
    LeakyEnergy,
    OneShot,
    Osc,
    Smoother,
    smoothstep,
    soft_limit,
)


def test_osc_phase_continuous():
    osc = Osc()
    fs = 1000
    a = osc.block(16, fs, 40.0)
    b = osc.block(16, fs, 40.0)
    full = np.concatenate([a, b])
    # no discontinuity at the block seam
    assert abs(full[16] - full[15]) < 0.3
    assert -1.001 <= full.min() and full.max() <= 1.001


def test_osc_frequency():
    osc = Osc()
    fs = 4000
    sig = np.concatenate([osc.block(200, fs, 50.0) for _ in range(10)])
    # dominant bin ~ 50 Hz
    mag = np.abs(np.fft.rfft(sig * np.hanning(sig.size)))
    freqs = np.fft.rfftfreq(sig.size, 1 / fs)
    assert abs(freqs[np.argmax(mag)] - 50.0) < 3.0


def test_smoother_step_response():
    sm = Smoother(5.0, 0.0)
    for _ in range(400):
        sm.step(1.0, 0.005)
    assert 0.95 < sm.y <= 1.0


def test_bandnoise_bounded_and_bandpassed():
    rng = np.random.default_rng(0)
    bn = BandNoise(rng)
    sig = np.concatenate([bn.block(256, 4000, 10.0, 30.0) for _ in range(8)])
    assert sig.min() >= -1.0 and sig.max() <= 1.0
    mag = np.abs(np.fft.rfft(sig))
    freqs = np.fft.rfftfreq(sig.size, 1 / 4000)
    band = mag[(freqs >= 8) & (freqs <= 35)].mean()
    high = mag[(freqs >= 200)].mean()
    assert band > 3 * high


def test_oneshot_shape_and_finish():
    env = OneShot()
    env.trigger(5.0, 5.0, 10.0, 1.0)  # 20 ms total
    fs = 2000
    out = np.concatenate([env.block(8, fs) for _ in range(12)])  # 48 ms
    assert out.max() <= 1.0 and out[0] < 0.5  # attack
    assert out[-1] == 0.0 and not env.active  # finished


def test_gate_hysteresis():
    g = Gate(open_thr=0.1, close_thr=0.05, hold_s=0.05)
    # opens above 0.1
    for _ in range(30):
        g.process(0.2, 0.01)
    assert g._g > 0.8
    # stays open between close and open thresholds
    for _ in range(3):
        g.process(0.07, 0.01)
    assert g._open
    # closes below close_thr after hold
    for _ in range(30):
        g.process(0.0, 0.01)
    assert g._g < 0.1


def test_soft_limit_never_exceeds_one():
    x = np.linspace(0, 5, 500)
    y = soft_limit(x, 0.7)
    assert y.max() < 1.0
    assert np.allclose(y[x < 0.6], x[x < 0.6])  # linear below the knee


def test_leaky_energy_ramps():
    le = LeakyEnergy(1.0)
    block = np.full(16, 0.5, np.float32)
    for _ in range(2000):
        le.update(block, 0.01)
    assert abs(le.e - 0.25) < 0.02  # mean-square of 0.5


def test_smoothstep():
    assert smoothstep(0, 1, -1) == 0.0
    assert smoothstep(0, 1, 2) == 1.0
    assert abs(smoothstep(0, 1, 0.5) - 0.5) < 1e-9
