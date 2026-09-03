"""lockup — front wheels dragging toward a stop under braking."""

from openpulse.engine.effects._slipbase import SlipEventEffect


class LockupEffect(SlipEventEffect):
    id = "lockup"
    _front = True
    _direction = "under"
