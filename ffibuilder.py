from cffi import FFI
import os
import pathlib
import shutil

ffibuilder = FFI()
ffibuilder.set_source(
    "order_entry_servers._raw", """
#include "ETILayoutsNS_Cash.h"
""")
ffibuilder.cdef("""
""", packed=True)
ffibuilder.cdef(open("ETILayoutsNS_Cash.h", "r").read())
