from collections import OrderedDict
import time

from ..core import DriverBase
import board
import busio
from adafruit_sht31d import SHT31D


class Driver(DriverBase):
    def __init__(self, address=0x44):
        super().__init__()
        i2c = busio.I2C(board.SCL, board.SDA)
        self._sensor = SHT31D(i2c, address=address)

    def run(self):
        return [(self.sid(), time.time_ns(), OrderedDict([
            ("temperature", self._sensor.temperature),
            ("humidity",    self._sensor.relative_humidity)
        ]))]
