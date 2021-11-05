from setuptools import setup, find_packages

setup(name='order_entry_servers',
      version='0.0.1',
      keywords='linux',
      url='https://github.com/twosigma/order_entry_servers',
      cffi_modules=["ffibuilder.py:ffibuilder"],
      packages=find_packages(),
)
