"""
Patch pyrtlsdr's ctypes bindings to tolerate a mainline (osmocom) librtlsdr build.

pyrtlsdr (unmaintained since ~2021) unconditionally binds a handful of GPIO/dithering
functions that only exist in certain librtlsdr forks. Homebrew's librtlsdr formula
ships mainline osmocom librtlsdr, which doesn't export them, so `import rtlsdr` raises
AttributeError at import time. This rewrites those bindings to skip functions the
loaded library doesn't actually export, instead of failing.

Run once after `pip install -r requirements.txt` (safe to re-run; it's idempotent).
"""

import sys
import sysconfig
from pathlib import Path

MARKER = "_optional_binding"

OLD_BLOCK = """# RTLSDR_API int rtlsdr_set_dithering(rtlsdr_dev *dev, int on)
f = librtlsdr.rtlsdr_set_dithering
f.restype, f.argtypes = c_int, [p_rtlsdr_dev, c_int]

# RTLSDR_API int rtlsdr_set_gpio_output(rtlsdr_dev_t *dev, uint8_t gpio)
f = librtlsdr.rtlsdr_set_gpio_output
f.restype, f.argtypes = c_int, [p_rtlsdr_dev, c_uint8]

# RTLSDR_API int rtlsdr_set_gpio_input(rtlsdr_dev_t *dev, uint8_t gpio)
f = librtlsdr.rtlsdr_set_gpio_input
f.restype, f.argtypes = c_int, [p_rtlsdr_dev, c_uint8]

# RTLSDR_API int librtlsdr.rtlsdr_set_gpio_bit(rtlsdr_dev_t *dev, uint8_t gpio, int val)
f = librtlsdr.rtlsdr_set_gpio_bit
f.restype, f.argtypes = c_int, [p_rtlsdr_dev, c_uint8, c_int]

# RTLSDR_API int librtlsdr.rtlsdr_get_gpio_bit(rtlsdr_dev_t *dev, uint8_t gpio, int *val)
f = librtlsdr.rtlsdr_get_gpio_bit
f.restype, f.argtypes = c_int, [p_rtlsdr_dev, c_uint8, POINTER(c_int)]

# RTLSDR_API int rtlsdr_set_gpio_byte(rtlsdr_dev_t *dev, int val)
f = librtlsdr.rtlsdr_set_gpio_byte
f.restype, f.argtypes = c_int, [p_rtlsdr_dev, c_int]

# RTLSDR_API int rtlsdr_get_gpio_byte(rtlsdr_dev_t *dev, int *val)
f = librtlsdr.rtlsdr_get_gpio_byte
f.restype, f.argtypes = c_int, [p_rtlsdr_dev, POINTER(c_int)]

# RTLSDR_API int rtlsdr_set_gpio_status(rtlsdr_dev_t *dev, int *status )
f = librtlsdr.rtlsdr_set_gpio_status
f.restype, f.argtypes = c_int, [p_rtlsdr_dev, POINTER(c_int)]"""

NEW_BLOCK = """# Not exported by mainline osmocom librtlsdr (only present in some forks);
# bind them only when available so import doesn't fail on stock builds.
def _optional_binding(name, restype, argtypes):
    try:
        f = getattr(librtlsdr, name)
    except AttributeError:
        return
    f.restype, f.argtypes = restype, argtypes

# RTLSDR_API int rtlsdr_set_dithering(rtlsdr_dev *dev, int on)
_optional_binding('rtlsdr_set_dithering', c_int, [p_rtlsdr_dev, c_int])

# RTLSDR_API int rtlsdr_set_gpio_output(rtlsdr_dev_t *dev, uint8_t gpio)
_optional_binding('rtlsdr_set_gpio_output', c_int, [p_rtlsdr_dev, c_uint8])

# RTLSDR_API int rtlsdr_set_gpio_input(rtlsdr_dev_t *dev, uint8_t gpio)
_optional_binding('rtlsdr_set_gpio_input', c_int, [p_rtlsdr_dev, c_uint8])

# RTLSDR_API int librtlsdr.rtlsdr_set_gpio_bit(rtlsdr_dev_t *dev, uint8_t gpio, int val)
_optional_binding('rtlsdr_set_gpio_bit', c_int, [p_rtlsdr_dev, c_uint8, c_int])

# RTLSDR_API int librtlsdr.rtlsdr_get_gpio_bit(rtlsdr_dev_t *dev, uint8_t gpio, int *val)
_optional_binding('rtlsdr_get_gpio_bit', c_int, [p_rtlsdr_dev, c_uint8, POINTER(c_int)])

# RTLSDR_API int rtlsdr_set_gpio_byte(rtlsdr_dev_t *dev, int val)
_optional_binding('rtlsdr_set_gpio_byte', c_int, [p_rtlsdr_dev, c_int])

# RTLSDR_API int rtlsdr_get_gpio_byte(rtlsdr_dev_t *dev, int *val)
_optional_binding('rtlsdr_get_gpio_byte', c_int, [p_rtlsdr_dev, POINTER(c_int)])

# RTLSDR_API int rtlsdr_set_gpio_status(rtlsdr_dev_t *dev, int *status )
_optional_binding('rtlsdr_set_gpio_status', c_int, [p_rtlsdr_dev, POINTER(c_int)])"""

OPEN_MARKER = "hasattr(librtlsdr, 'rtlsdr_set_dithering')"

OPEN_OLD_BLOCK = """        # disable PLL dithering if necessary. If it's going to happen, it must
        # happen before frequency is set.
        result = librtlsdr.rtlsdr_set_dithering(self.dev_p, int(dithering_enabled))
        if result < 0:
            raise IOError('Error code %d when setting PLL dithering mode'\\
                           % (result))"""

OPEN_NEW_BLOCK = """        # disable PLL dithering if necessary. If it's going to happen, it must
        # happen before frequency is set. Not all librtlsdr builds export this
        # (e.g. mainline osmocom), so skip it when unavailable.
        if hasattr(librtlsdr, 'rtlsdr_set_dithering'):
            result = librtlsdr.rtlsdr_set_dithering(self.dev_p, int(dithering_enabled))
            if result < 0:
                raise IOError('Error code %d when setting PLL dithering mode'\\
                               % (result))"""


def find_rtlsdr_file(relative_path):
    """Locate a file inside the installed rtlsdr package without importing it
    (importing rtlsdr/__init__.py is exactly what crashes on an unpatched install)."""
    candidates = [sysconfig.get_path("purelib"), sysconfig.get_path("platlib")]
    candidates += sys.path
    for entry in candidates:
        if not entry:
            continue
        path = Path(entry) / "rtlsdr" / relative_path
        if path.is_file():
            return path
    return None


def patch_file(relative_path, marker, old_block, new_block):
    path = find_rtlsdr_file(relative_path)
    if path is None:
        print(f"Could not locate rtlsdr/{relative_path}; is pyrtlsdr installed in this environment?")
        sys.exit(1)

    text = path.read_text(encoding="utf-8")

    if marker in text:
        print(f"Already patched: {path}")
        return

    if old_block not in text:
        print(
            f"Expected block not found in {path} (pyrtlsdr version may differ). "
            "No changes made; patch script may need updating."
        )
        sys.exit(1)

    path.write_text(text.replace(old_block, new_block), encoding="utf-8")
    print(f"Patched: {path}")


def main():
    patch_file("librtlsdr.py", MARKER, OLD_BLOCK, NEW_BLOCK)
    patch_file("rtlsdr.py", OPEN_MARKER, OPEN_OLD_BLOCK, OPEN_NEW_BLOCK)


if __name__ == "__main__":
    main()
