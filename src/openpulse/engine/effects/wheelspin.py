"""wheelspin — rear (driven) wheels over-rotating under throttle."""

from openpulse.engine.effects._slipbase import SlipEventEffect


class WheelspinEffect(SlipEventEffect):
    id = "wheelspin"
    _front = False
    _direction = "over"
