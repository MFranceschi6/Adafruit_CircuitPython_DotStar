# The MIT License (MIT)
#
# Copyright (c) 2016 Damien P. George (original Neopixel object)
# Copyright (c) 2017 Ladyada
# Copyright (c) 2017 Scott Shawcroft for Adafruit Industries
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""
`adafruit_dotstar` - DotStar strip driver
====================================================

* Author(s): Damien P. George, Limor Fried & Scott Shawcroft
"""
import busio
import digitalio
import math

__version__ = "0.0.0-auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_DotStar.git"

START_HEADER_SIZE = 4
LED_START = 0b11100000  # Three "1" bits, followed by 5 brightness bits

# Pixel color order constants
RGB = (0, 1, 2)
RBG = (0, 2, 1)
GRB = (1, 0, 2)
GBR = (1, 2, 0)
BRG = (2, 0, 1)
BGR = (2, 1, 0)


class DotStar:
    """
    A sequence of dotstars.

    :param ~microcontroller.Pin clock: The pin to output dotstar clock on.
    :param ~microcontroller.Pin data: The pin to output dotstar data on.
    :param int n: The number of dotstars in the chain
    :param float brightness: Brightness of the pixels between 0.0 and 1.0
    :param bool auto_write: True if the dotstars should immediately change when
        set. If False, `show` must be called explicitly.
    :param tuple pixel_order: Set the pixel order on the strip - different
        strips implement this differently. If you send red, and it looks blue
        or green on the strip, modify this! It should be one of the values
        above.
    :param int baudrate: Desired clock rate if using hardware SPI (ignored if
        using 'soft' SPI). This is only a recommendation; the actual clock
        rate may be slightly different depending on what the system hardware
        can provide.


    Example for Gemma M0:

    .. code-block:: python

        import adafruit_dotstar
        import time
        from board import *

        RED = 0x100000

        with adafruit_dotstar.DotStar(APA102_SCK, APA102_MOSI, 1) as pixels:
            pixels[0] = RED
            time.sleep(2)
    """

    def __init__(self, clock, data, n, line, *, brightness=1.0, auto_write=True,
                 pixel_order=BGR, baudrate=4000000):
        self._spi = None
        self._line = line
        try:
            self._spi = busio.SPI(clock, MOSI=data)
            while not self._spi.try_lock():
                pass
            self._spi.configure(baudrate=baudrate)

        except (NotImplementedError, ValueError):
            self.dpin = digitalio.DigitalInOut(data)
            self.cpin = digitalio.DigitalInOut(clock)
            self.dpin.direction = digitalio.Direction.OUTPUT
            self.cpin.direction = digitalio.Direction.OUTPUT
            self.cpin.value = False
        self._n = n
        # Supply one extra clock cycle for each two pixels in the strip.
        self.end_header_size = n // 16
        if n % 16 != 0:
            self.end_header_size += 1
        self._buf = bytearray(line * n * 4 + line * START_HEADER_SIZE + line * self.end_header_size)
        self.end_header_index = START_HEADER_SIZE + n * 4 - self.end_header_size
        self.pixel_order = pixel_order
        for j in range(line):
            # Four empty bytes to start.
            for i in range(START_HEADER_SIZE):
                self._buf[(j * n) + i] = 0x00
            # Mark the beginnings of each pixel.
            for i in range(START_HEADER_SIZE, self.end_header_index, 4):
                self._buf[(j * n) + i] = 0xff
            # 0xff bytes at the end.
            for i in range(self.end_header_index, (self.end_header_index + self.end_header_size)):
                self._buf[(j * n) + i] = 0xff
        self._brightness = 1.0
        # Set auto_write to False temporarily so brightness setter does _not_
        # call show() while in __init__.
        self.auto_write = False
        self.brightness = brightness
        self.auto_write = auto_write

    def deinit(self):
        """Blank out the DotStars and release the resources."""
        self.auto_write = False
        for i in range(START_HEADER_SIZE, self.end_header_index):
            if i % 4 != 0:
                self._buf[i] = 0
        self.show()
        if self._spi:
            self._spi.deinit()
        else:
            self.dpin.deinit()
            self.cpin.deinit()

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        self.deinit()

    def __repr__(self):
        return "[" + ", ".join([str(x) for x in self]) + "]"

    def _set_item(self, index, value):
        """
        value can be one of three things:
                a (r,g,b) list/tuple
                a (r,g,b, brightness) list/tuple
                a single, longer int that contains RGB values, like 0xFFFFFF
            brightness, if specified should be a float 0-1

        Set a pixel value. You can set per-pixel brightness here, if it's not passed it
        will use the max value for pixel brightness value, which is a good default.

        Important notes about the per-pixel brightness - it's accomplished by
        PWMing the entire output of the LED, and that PWM is at a much
        slower clock than the rest of the LEDs. This can cause problems in
        Persistence of Vision Applications
        """
        num = 0
        if index > self._n:
            num = math.ceil(self._n / index)

        offset = index * 4 + (START_HEADER_SIZE * (num + 1))
        rgb = value
        if isinstance(value, int):
            rgb = (value >> 16, (value >> 8) & 0xff, value & 0xff)

        if len(rgb) == 4:
            brightness = value[3]
            # Ignore value[3] below.
        else:
            brightness = 1

        # LED startframe is three "1" bits, followed by 5 brightness bits
        # then 8 bits for each of R, G, and B. The order of those 3 are configurable and
        # vary based on hardware
        # same as math.ceil(brightness * 31) & 0b00011111
        # Idea from https://www.codeproject.com/Tips/700780/Fast-floor-ceiling-functions
        brightness_byte = 32 - int(32 - brightness * 31) & 0b00011111
        self._buf[offset] = brightness_byte | LED_START
        self._buf[offset + 1] = rgb[self.pixel_order[0]]
        self._buf[offset + 2] = rgb[self.pixel_order[1]]
        self._buf[offset + 3] = rgb[self.pixel_order[2]]

    def __setitem__(self, index, val):
        if isinstance(index, slice):
            start, stop, step = index.indices(self._n)
            length = stop - start
            if step != 0:
                # same as math.ceil(length / step)
                # Idea from https://fizzbuzzer.com/implement-a-ceil-function/
                length = (length + step - 1) // step
            if len(val) != length:
                raise ValueError("Slice and input sequence size do not match.")
            for val_i, in_i in enumerate(range(start, stop, step)):
                self._set_item(in_i, val[val_i])
        else:
            self._set_item(index, val)

        if self.auto_write:
            self.show()

    def __getitem__(self, index):
        if isinstance(index, slice):
            out = []
            for in_i in range(*index.indices(self._n)):
                out.append(
                    tuple(self._buf[in_i * 4 + (3 - i) + START_HEADER_SIZE] for i in range(3)))
            return out
        if index < 0:
            index += len(self)
        if index >= self._n or index < 0:
            raise IndexError
        offset = index * 4
        return tuple(self._buf[offset + (3 - i) + START_HEADER_SIZE]
                     for i in range(3))

    def __len__(self):
        return self._n

    @property
    def brightness(self):
        """Overall brightness of the pixel"""
        return self._brightness

    @brightness.setter
    def brightness(self, brightness):
        self._brightness = min(max(brightness, 0.0), 1.0)
        if self.auto_write:
            self.show()

    def fill(self, color):
        """Colors all pixels the given ***color***."""
        auto_write = self.auto_write
        self.auto_write = False
        for i in range(self._n):
            self[i] = color
        if auto_write:
            self.show()
        self.auto_write = auto_write

    def _ds_writebytes(self, buf, start=0, length=-1):
        if length == -1:
            length = len(buf)
        for i in range(start,length):
            for _ in range(8):
                self.cpin.value = True
                self.dpin.value = (buf[i] & 0x80)
                self.cpin.value = False
                buf[i] = buf[i] << 1

    def __str__(self):
        s = ""
        s += "Number of leds -> " + str(self._n) + "\n"
        s += "Dimension of Buffer -> " + str(len(self._buf)) + "\n"
        s += "Start header size -> " + str(START_HEADER_SIZE) + '\n'
        s += "End header index -> " + str(self.end_header_index) + '\n'
        s += 'End header size -> ' + str(self.end_header_size) + '\n'
        s += '| '
        for i in range(self._n * 4 + START_HEADER_SIZE + self.end_header_size):
            s += ' ' + str(i) + "  "
        s += '|\n'
        for i in range(self._line):
            s += "| "
            for j in range(START_HEADER_SIZE):
                s += hex(self._buf[(self._n*i)+j])
                if self._buf[(self._n*i)+j] == 0:
                    s += '  '
                else:
                    s += ' '
            for j in range(START_HEADER_SIZE, self.end_header_index):
                s += hex(self._buf[(self._n*i)+j])
                if self._buf[(self._n*i)+j] == 0:
                    s += '  '
                else:
                    s += ' '
            for j in range(self.end_header_index, (self.end_header_index + self.end_header_size)):
                s += hex(self._buf[(self._n*i)+j])
                if self._buf[(self._n*i)+j] == 0:
                    s += '  '
                else:
                    s += ' '
            s += " |\n"
        return s

    def show(self, line=0):
        """Shows the new colors on the pixels themselves if they haven't already
        been autowritten.

        The colors may or may not be showing after this function returns because
        it may be done asynchronously."""
        # Create a second output buffer if we need to compute brightness
        buf = self._buf
        if self.brightness < 1.0:
            buf = bytearray(self._buf)
            # Four empty bytes to start.
            for i in range(START_HEADER_SIZE):
                buf[i] = 0x00
            for i in range(START_HEADER_SIZE, self.end_header_index):
                buf[i] = self._buf[i] if i % 4 == 0 else int(self._buf[i] * self._brightness)
            # Four 0xff bytes at the end.
            for i in range(self.end_header_index, len(buf)):
                buf[i] = 0xff

        if self._spi:
            self._spi.write(buf, line * self._n, (line + 1) * self._n)
        else:
            self._ds_writebytes(buf, line * self._n, (line + 1) * self._n)
            self.cpin.value = False
