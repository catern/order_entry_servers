from cffi import FFI
import re

ffibuilder = FFI()
ffibuilder.set_source(
    "order_entry_servers._raw", """
#include "ETILayoutsNS_Cash.h"
""", include_dirs=["."])
ffibuilder.cdef("""
""", packed=True)
# sooo...
# let's maybe... seek to ETI_INTERFACE_VERSION
# er hmmm nahh
# remove directives...
# let's just manually fix it up actually, it's fine for now.

no_string_define_pattern = re.compile('^#define [A-Za-z0-9_]+[ \t]+".*"\n')
pattern = re.compile('^#define ([A-Za-z0-9_]+) .*\n')
array_pattern = re.compile('\[[A-Z0-9_]+\]')

def define_to_dots(line: str) -> str:
    line = re.sub(no_string_define_pattern, r'\n', line)
    line = re.sub(pattern, r'#define \1 ...\n', line)
    line = re.sub(array_pattern, r'[...]', line)
    return line

file = "".join([define_to_dots(line) for line in open("ETILayoutsNS_Cash.h", "r")])
ffibuilder.cdef(file)

# for line in open("ETILayoutsNS_Cash.h", "r"):
#     if line.startswith("#define"):
#         print(line)
#         ffibuilder.cdef(define_to_dots(line), override=True)

