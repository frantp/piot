#!/usr/bin/env python3

from collections import OrderedDict
import contextlib
import importlib
import os
from serial import Serial
from smbus2 import SMBus
import socket
import sys
import time
import toml
import traceback

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


__all__ = [
    "get_inputs", "get_outputs", "collect", "GPIOContext", "ActivationContext",
    "DriverBase", "SMBusDriver", "SerialDriver"
]


TAG_ERROR = "ERROR"
ACT_PIN_ID = "ACTIVATION_PIN"


def find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            yield obj[key]
        for v in obj.values():
            yield from find(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from find(v, key)


def format_msg(timestamp, measurement, tags, fields):
    tstr = ",".join(["{}={}".format(k, v) for k, v in tags.items()])
    fstr = ",".join(["{0}={3}{1}{2}{3}".format(
        k, v,
        "i" if isinstance(v, int) else "",
        "\"" if isinstance(v, str) else "")
        for k, v in fields.items()]
    )
    return "{},{} {} {}".format(measurement, tstr, fstr, timestamp)


def sync_wait(sync):
    return sync - time.time() % sync if sync > 0 else 0


def round_step(x, step):
    return x // step * step if step else x


def get_inputs(cfg, stack):
    if not cfg:
        return None
    inputs = []
    for driver_id in cfg:
        for dcfg in cfg[driver_id]:
            try:
                pin = None
                if ACT_PIN_ID in dcfg:
                    pin = dcfg[ACT_PIN_ID]
                    dcfg = {k: v for k, v in dcfg.items()
                            if k != ACT_PIN_ID}
                driver_module = importlib.import_module(
                    "piot.inputs." + driver_id)
                driver = getattr(driver_module, "Driver")(**dcfg)
                tags = [(driver_id + "." + k, v)
                        for k, v in dcfg.items()
                        if type(v) in (int, float, bool, str)]
                inputs.append((stack.enter_context(driver), pin, *tags))
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                traceback.print_exc()
    return inputs


def get_outputs(cfg, stack):
    outputs = []
    for driver_id in cfg:
        for dcfg in cfg[driver_id]:
            try:
                driver_module = importlib.import_module(
                    "piot.outputs." + driver_id)
                driver = getattr(driver_module, "Driver")(**dcfg)
                outputs.append(stack.enter_context(driver))
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                traceback.print_exc()
    return outputs


def collect(inputs, sync=0):
    sync_ns = int(sync * 1e9)
    time.sleep(sync_wait(sync))
    for input, pin, *cfg_tags in inputs:
        try:
            with ActivationContext(pin):
                res = input.run()
                for did, ts, fields, *tags in res:
                    tags.extend(cfg_tags)
                    yield (did, round_step(ts, sync_ns), fields, *tags)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            traceback.print_exc()
            ts = round_step(time.time_ns(), sync_ns)
            yield (input.sid(), ts, None, (TAG_ERROR, None))


def main():
    if len(sys.argv) <= 1:
        print("Usage: {} <cfg_file>".format(sys.argv[0]))
        exit()
    cfg_file = sys.argv[1]

    # Read configuration
    cfg = toml.load(cfg_file)
    interval = cfg.get("interval", 0)
    host = cfg.get("host", socket.gethostname())
    inputs_cfg = cfg.get("inputs", {})
    outputs_cfg = cfg.get("outputs", {})

    # Run drivers
    with GPIOContext(inputs_cfg), contextlib.ExitStack() as stack:
        inputs = get_inputs(inputs_cfg, stack)
        outputs = get_outputs(outputs_cfg, stack)
        while True:
            for driver_id, ts, fields, *tags in collect(inputs, interval):
                if fields:
                    fields = OrderedDict([(k, v) for k, v in fields.items()
                                         if v is not None])
                dtags = OrderedDict([("host", host)] + tags)
                for output in outputs:
                    try:
                        output.run(driver_id, ts, fields, dtags)
                    except Exception:
                        traceback.print_exc()


class GPIOContext:
    def __init__(self, cfg):
        GPIO.setmode(GPIO.BCM)
        pin_list = list(find(cfg, ACT_PIN_ID))
        GPIO.setup(pin_list, GPIO.OUT, initial=GPIO.HIGH)

    def close(self):
        GPIO.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class ActivationContext:
    def __init__(self, pin=None):
        self._open = False
        self._pin = pin

    def open(self):
        if self._pin is None or self._open:
            return
        self._open = True
        GPIO.output(self._pin, GPIO.LOW)

    def close(self):
        if self._pin is None or not self._open:
            return
        GPIO.output(self._pin, GPIO.HIGH)
        self._open = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class DriverBase:
    def run(self):
        raise NotImplementedError()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def sid(self):
        return self.__class__.__module__.split(".")[-1]


class SMBusDriver(DriverBase):
    def __init__(self, bus=1):
        super().__init__()
        self._bus = SMBus(bus)

    def close(self):
        if self._bus:
            self._bus.close()
        super().close()


class SerialDriver(DriverBase):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._args = args
        self._kwargs = kwargs

    @contextlib.contextmanager
    def _open_serial(self):
        serial = Serial(timeout=1, *self._args, **self._kwargs)
        try:
            serial.reset_input_buffer()
            serial.reset_output_buffer()
            yield serial
        finally:
            serial.close()

    def _cmd(self, serial, cmd, size=0):
        serial.write(cmd)
        serial.flush()
        time.sleep(0.1)
        if size > 0:
            return serial.read(size)


if __name__ == "__main__":
    main()
